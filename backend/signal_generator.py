import os
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from risk_calculator import calculate_pips, calculate_lot_size
import settings_manager

class SignalGenerator:
    def __init__(self, cooldown_minutes: int = 120):
        self.active_signals = {}  # symbol -> signal_dict
        self.cooldowns = {}       # symbol -> expiration_time
        self.cooldown_minutes = cooldown_minutes
        self.rejected_zones = set()
        
        # Setup Custom LLM (OpenAI-compatible)
        self.llm_api_key = os.getenv("LLM_API_KEY")
        self.llm_base_url = os.getenv("LLM_BASE_URL")
        self.llm_model_name = os.getenv("LLM_MODEL_NAME", "gpt-3.5-turbo")
        
        if self.llm_api_key and self.llm_base_url:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                api_key=self.llm_api_key,
                base_url=self.llm_base_url,
            )
        else:
            self.client = None

    def fetch_market_sentiment(self) -> str:
        """
        Fetch current market sentiment/news headlines.
        In a production environment, this would call a News API (e.g., ForexFactory RSS, Finnhub).
        """
        import random
        sentiments = [
            "Risk-On: Equities are rallying, USD is weakening, investors are seeking high-yield assets.",
            "Risk-Off: High inflation data just released, USD is strengthening, investors are fleeing to safe havens.",
            "Neutral: Markets are quiet ahead of the FOMC meeting tomorrow.",
            "Mixed: Tech sector is up, but commodities are dropping due to supply concerns."
        ]
        return random.choice(sentiments)

    async def evaluate_confluence(self, symbol: str, current_price: float, 
                            events: List[Dict], obs: List[Dict], fvgs: List[Dict], sweeps: List[Dict] = None, htf_trend: str = None,
                            h1_trend: str = None, h4_trend: str = None, engine_ltf = None,
                            snr_zones: List[Dict] = None, snd_zones: List[Dict] = None, pd_zones: Dict = None, breakers: List[Dict] = None,
                            dxy_trend: str = None, fibo_ote: Dict = None, poc_price: float = None, trade_manager = None,
                            amd_setups: List[Dict] = None, atr: float = 0.5, reversal_patterns: List[str] = None,
                            db = None, adx_h1: float = 25.0, adx_h4: float = 25.0) -> Optional[Dict]:
        """
        Evaluate if a new signal should be generated based on SMC confluence and LLM approval.
        Implements High Probability Grading (Grade A/B) based on Liquidity Sweeps and Multi-Timeframe.
        """
        
        # Check Trading Allowed (Psychology Limits)
        if trade_manager:
            allowed, reason = trade_manager.check_trading_allowed()
            if not allowed:
                print(f"[{symbol}] Trading blocked by Psychology Manager: {reason}")
                return None
                
        # Check Cooldown
        if symbol in self.cooldowns:
            if datetime.now() < self.cooldowns[symbol]:
                return None
            else:
                del self.cooldowns[symbol]
                
        # Market Regime Filter (ADX)
        is_ranging = False
        if adx_h1 < 25.0 or adx_h4 < 25.0:
            is_ranging = True
            
        if is_ranging:
            # Check if price is near range extreme
            near_extreme = False
            if engine_ltf and engine_ltf.is_near_range_extreme(current_price):
                near_extreme = True
                
            if near_extreme:
                print(f"[{symbol}] Explicitly skipping signal: Market is ranging (ADX < 25) AND price is near range extreme (High risk of false breakout).")
                return {"status": "SKIPPED", "reasons": ["Ranging Market: Near Extreme"]}
            else:
                print(f"[{symbol}] Standby mode: Market is ranging (ADX < 25). Waiting for trend.")
                return {"status": "SKIPPED", "reasons": ["Ranging Market"]}

        if not events:
            return None
            
        last_event = events[-1]
        is_bullish = "BULLISH" in last_event['type']
        
        # Check Confluence Criteria
        confluence_score = 0
        reasons = [last_event['type']]
        
        # Volume Analysis Validation
        if last_event.get('is_fakeout', False):
            confluence_score -= 2
            reasons.append("Volume Breakout: Low")
        else:
            confluence_score += 1
            reasons.append("Volume Breakout: High")
        
        has_htf_alignment = False
        if htf_trend:
            if (is_bullish and htf_trend == "BULLISH") or (not is_bullish and htf_trend == "BEARISH"):
                has_htf_alignment = True
                reasons.append(f"HTF Trend Alignment ({htf_trend})")
                
        # Multi-Timeframe Bias Filter (H1 and H4) - Crucial for XAUUSD
        if "XAU" in symbol:
            if h1_trend and h4_trend:
                if is_bullish and (h1_trend == "BEARISH" or h4_trend == "BEARISH"):
                    # Strict MTF filtering: Reject if M1/M15 opposes H1/H4
                    return None
                elif not is_bullish and (h1_trend == "BULLISH" or h4_trend == "BULLISH"):
                    return None
                confluence_score += 2
                reasons.append(f"MTF Bias Aligned (H1: {h1_trend}, H4: {h4_trend})")
                
        # Intermarket Correlation (DXY)
        if dxy_trend and "XAU" in symbol:
            if is_bullish and dxy_trend == "BEARISH":
                confluence_score += 2
                reasons.append("Intermarket Correlation: DXY Bearish, Gold Bullish")
            elif is_bullish and dxy_trend == "BULLISH":
                confluence_score -= 1
                reasons.append("Intermarket Correlation: DXY Bullish, Gold Bullish")
            elif not is_bullish and dxy_trend == "BULLISH":
                confluence_score += 2
                reasons.append("Intermarket Correlation: DXY Bullish, Gold Bearish")
            elif not is_bullish and dxy_trend == "BEARISH":
                confluence_score -= 1
                reasons.append("Intermarket Correlation: DXY Bearish, Gold Bearish")
        
        entry_target = None
        sl_target = None
        # ICT Killzones (Time of Day Check)
        event_time_str = last_event.get('timestamp')
        is_killzone = False
        if event_time_str:
            try:
                if isinstance(event_time_str, str):
                    dt = datetime.fromisoformat(event_time_str.replace('Z', '+00:00'))
                else:
                    dt = event_time_str
                
                # Convert to GMT (assume input is UTC since timezone is +00:00 or Z)
                gmt_hour = dt.hour
                # London (07:00-10:00 GMT), NY (12:00-15:00 GMT)
                if (7 <= gmt_hour < 10) or (12 <= gmt_hour < 15):
                    is_killzone = True
                    confluence_score += 2
                    reasons.append("Session: Inside London/NY Killzone")
                else:
                    if "XAU" in symbol:
                        # For XAUUSD, ignore signals outside killzone or treat them as Low Session Confidence
                        reasons.append("Session: Outside Killzone")
                        # In strict mode, we could return None here
            except:
                pass
                
        # Premium & Discount Check (Advanced SMC)
        in_correct_pd_zone = False
        if pd_zones:
            if is_bullish and current_price <= pd_zones['discount_high']:
                in_correct_pd_zone = True
                confluence_score += 2
                reasons.append("In Discount Zone")
            elif not is_bullish and current_price >= pd_zones['premium_low']:
                in_correct_pd_zone = True
                confluence_score += 2
                reasons.append("In Premium Zone")
        else:
            # If we can't calculate PD, assume true but no bonus points
            in_correct_pd_zone = True

        # Define dynamic buffer for SL/Limit distance
        is_xau = "XAU" in symbol
        buffer_dist = 0.5 if is_xau else 0.0005

        # Criterion 1: Liquidity Sweep & IDM (CRITICAL FOR GRADE A)
        has_sweep = False
        has_idm = False
        if sweeps:
            for sweep in reversed(sweeps):
                # Only care about sweeps that happened recently relative to current structure
                if is_bullish and sweep['type'] == "SWEEP_BULLISH":
                    has_sweep = True
                    confluence_score += 1
                    reasons.append("Bullish Liquidity Sweep")
                    if sweep.get('is_idm'):
                        has_idm = True
                        confluence_score += 1
                        reasons.append("IDM Swept")
                    break
                elif not is_bullish and sweep['type'] == "SWEEP_BEARISH":
                    has_sweep = True
                    confluence_score += 1
                    reasons.append("Bearish Liquidity Sweep")
                    if sweep.get('is_idm'):
                        has_idm = True
                        confluence_score += 1
                        reasons.append("IDM Swept")
                    break

        # Criterion 2: Fair Value Gap (FVG) - HTF Zone Check
        valid_fvg = None
        for fvg in reversed(fvgs):
            if is_bullish and fvg['type'] == "FVG_BULLISH" and not fvg['mitigated']:
                # Ensure price hasn't broken below the POI
                if current_price >= fvg['bottom']:
                    valid_fvg = fvg
                    dist_to_zone = max(0, current_price - fvg['top'])
                    if dist_to_zone == 0:
                        print(f"[{symbol}] WATCHING: Price inside HTF Bullish FVG ({fvg['bottom']} - {fvg['top']})")
                        reasons.append("Inside HTF Bullish FVG")
                    else:
                        print(f"[{symbol}] WATCHING: Price approaching HTF Bullish FVG ({fvg['bottom']} - {fvg['top']})")
                        reasons.append("Approaching HTF Bullish FVG")
                    confluence_score += 1
                    if not entry_target: 
                        entry_target = fvg['top'] if dist_to_zone >= 0.5 * atr else current_price
                        sl_target = fvg['bottom'] - buffer_dist
                    break
            elif not is_bullish and fvg['type'] == "FVG_BEARISH" and not fvg['mitigated']:
                # Ensure price hasn't broken above the POI
                if current_price <= fvg['top']:
                    valid_fvg = fvg
                    dist_to_zone = max(0, fvg['bottom'] - current_price)
                    if dist_to_zone == 0:
                        print(f"[{symbol}] WATCHING: Price inside HTF Bearish FVG ({fvg['bottom']} - {fvg['top']})")
                        reasons.append("Inside HTF Bearish FVG")
                    else:
                        print(f"[{symbol}] WATCHING: Price approaching HTF Bearish FVG ({fvg['bottom']} - {fvg['top']})")
                        reasons.append("Approaching HTF Bearish FVG")
                    confluence_score += 1
                    if not entry_target:
                        entry_target = fvg['bottom'] if dist_to_zone >= 0.5 * atr else current_price
                        sl_target = fvg['top'] + buffer_dist
                    break

        # Criterion 3: Order Block (OB) - HTF Zone Check
        valid_ob = None
        for ob in reversed(obs):
            if is_bullish and ob['type'] == "OB_BULLISH" and not ob['mitigated']:
                if current_price >= ob['bottom']:
                    valid_ob = ob
                    dist_to_zone = max(0, current_price - ob['top'])
                    if dist_to_zone == 0:
                        print(f"[{symbol}] WATCHING: Price inside HTF Bullish OB ({ob['bottom']} - {ob['top']})")
                        reasons.append("Inside HTF Bullish OB")
                    else:
                        print(f"[{symbol}] WATCHING: Price approaching HTF Bullish OB ({ob['bottom']} - {ob['top']})")
                        reasons.append("Approaching HTF Bullish OB")
                    confluence_score += 2
                    if not entry_target:
                        entry_target = ob['top'] if dist_to_zone >= 0.5 * atr else current_price
                        sl_target = ob['bottom'] - buffer_dist
                    break
            elif not is_bullish and ob['type'] == "OB_BEARISH" and not ob['mitigated']:
                if current_price <= ob['top']:
                    valid_ob = ob
                    dist_to_zone = max(0, ob['bottom'] - current_price)
                    if dist_to_zone == 0:
                        print(f"[{symbol}] WATCHING: Price inside HTF Bearish OB ({ob['bottom']} - {ob['top']})")
                        reasons.append("Inside HTF Bearish OB")
                    else:
                        print(f"[{symbol}] WATCHING: Price approaching HTF Bearish OB ({ob['bottom']} - {ob['top']})")
                        reasons.append("Approaching HTF Bearish OB")
                    confluence_score += 2
                    if not entry_target:
                        entry_target = ob['bottom'] if dist_to_zone >= 0.5 * atr else current_price
                        sl_target = ob['top'] + buffer_dist
                    break

        # Criterion 4: Breaker Blocks (ICT) - HTF Zone Check
        valid_breaker = None
        if breakers:
            for brk in reversed(breakers):
                if is_bullish and brk['type'] == "BREAKER_BULLISH":
                    if current_price >= brk['bottom']:
                        valid_breaker = brk
                        dist_to_zone = max(0, current_price - brk['top'])
                        if dist_to_zone == 0:
                            print(f"[{symbol}] WATCHING: Price inside HTF Bullish Breaker ({brk['bottom']} - {brk['top']})")
                            reasons.append("Inside HTF Bullish Breaker")
                        else:
                            print(f"[{symbol}] WATCHING: Price approaching HTF Bullish Breaker ({brk['bottom']} - {brk['top']})")
                            reasons.append("Approaching HTF Bullish Breaker")
                        confluence_score += 2
                        if not entry_target:
                            entry_target = brk['top'] if dist_to_zone >= 0.5 * atr else current_price
                            sl_target = brk['bottom'] - buffer_dist
                        break

                elif not is_bullish and brk['type'] == "BREAKER_BEARISH":
                    if current_price <= brk['top']:
                        valid_breaker = brk
                        dist_to_zone = max(0, brk['bottom'] - current_price)
                        if dist_to_zone == 0:
                            print(f"[{symbol}] WATCHING: Price inside HTF Bearish Breaker ({brk['bottom']} - {brk['top']})")
                            reasons.append("Inside HTF Bearish Breaker")
                        else:
                            print(f"[{symbol}] WATCHING: Price approaching HTF Bearish Breaker ({brk['bottom']} - {brk['top']})")
                            reasons.append("Approaching HTF Bearish Breaker")
                        confluence_score += 2
                        if not entry_target:
                            entry_target = brk['bottom'] if dist_to_zone >= 0.5 * atr else current_price
                            sl_target = brk['top'] + buffer_dist
                        break

        # Generate a unique POI signature to track rejected setups
        # poi_signature will be assigned later
            
        # Checking blacklist logic moved below

        valid_snr = False
        snr_level_used = None
        if snr_zones:
            for snr in snr_zones:
                snr_level = snr.get('level')
                if snr_level is None: continue
                
                if is_bullish and snr['type'] == "SUPPORT" and abs(current_price - snr_level) / current_price < 0.002:
                    valid_snr = True
                    snr_level_used = snr_level
                    if snr.get('is_mnsr'):
                        confluence_score += 2
                        reasons.append("Major Support (MNSR)")
                    else:
                        confluence_score += 1
                        reasons.append(f"{snr['strength']} Support")
                    break
                elif not is_bullish and snr['type'] == "RESISTANCE" and abs(current_price - snr_level) / current_price < 0.002:
                    valid_snr = True
                    snr_level_used = snr_level
                    if snr.get('is_mnsr'):
                        confluence_score += 2
                        reasons.append("Major Resistance (MNSR)")
                    else:
                        confluence_score += 1
                        reasons.append(f"{snr['strength']} Resistance")
                    break

        poi_signature = None
        if valid_ob:
            poi_signature = f"{symbol}_{valid_ob['type']}_{valid_ob['bottom']}_{valid_ob['top']}"
        elif valid_fvg:
            poi_signature = f"{symbol}_{valid_fvg['type']}_{valid_fvg['bottom']}_{valid_fvg['top']}"
        elif valid_breaker:
            poi_signature = f"{symbol}_{valid_breaker['type']}_{valid_breaker['bottom']}_{valid_breaker['top']}"
        elif valid_snr and snr_level_used is not None:
            poi_signature = f"{symbol}_SNR_{'SUPPORT' if is_bullish else 'RESISTANCE'}_{snr_level_used}"

        if poi_signature and poi_signature in self.rejected_zones:
            # We already queried the LLM for this exact zone and got rejected. Do not retry.
            return None
            
        # Check permanent database blacklist
        if poi_signature and db:
            blacklisted = db.get_blacklisted_zones(symbol)
            if poi_signature in blacklisted:
                print(f"[{symbol}] Skipping signal: Zone {poi_signature} is BLACKLISTED (Previously hit SL or BE).")
                return {"status": "SKIPPED", "reasons": [f"Blacklisted Zone: {poi_signature}"]}

        # Criterion 7: Fibonacci OTE (Optimal Trade Entry)
        in_ote = False
        if fibo_ote:
            if is_bullish and fibo_ote.get("bullish_ote"):
                ote = fibo_ote["bullish_ote"]
                if ote["bottom"] <= current_price <= ote["top"]:
                    in_ote = True
                    confluence_score += 2
                    reasons.append("In Bullish Fibo OTE (0.618-0.786)")
            elif not is_bullish and fibo_ote.get("bearish_ote"):
                ote = fibo_ote["bearish_ote"]
                if ote["bottom"] <= current_price <= ote["top"]:
                    in_ote = True
                    confluence_score += 2
                    reasons.append("In Bearish Fibo OTE (0.618-0.786)")

        # Criterion 8: Volume Profile POC
        near_poc = False
        if poc_price:
            dist_to_poc = abs(current_price - poc_price) / current_price
            if dist_to_poc < 0.002:
                near_poc = True
                confluence_score += 2
                reasons.append("Volume Profile POC Support/Resistance")

        # Criterion 9: AMD Pattern (Accumulation, Manipulation, Distribution)
        has_amd = False
        if amd_setups:
            for amd in reversed(amd_setups):
                if is_bullish and amd['type'] == "AMD_BULLISH":
                    has_amd = True
                    confluence_score += 3
                    reasons.append("AMD Pattern (Asian Sweep -> Distribution)")
                    break
                elif not is_bullish and amd['type'] == "AMD_BEARISH":
                    has_amd = True
                    confluence_score += 3
                    reasons.append("AMD Pattern (Asian Sweep -> Distribution)")
                    break

        # --- SETUP GRADING LOGIC (ADVANCED SMC) ---
        # Strict validation: If not in correct PD zone, we still allow it but deduct points
        if not in_correct_pd_zone:
            confluence_score -= 1 # Penalize score
            
        # Grade A+: Grade A + Fibo OTE + POC
        # Grade A: Has Sweep/IDM AND (OB, FVG, or Breaker) AND HTF Alignment AND ICT Killzone
        # Grade B: Missing one strong confluence but has decent score
        setup_grade = "NONE"
        risk_multiplier = 0.0
        
        has_poi = (valid_ob or valid_fvg or valid_breaker or valid_snr)
        
        if has_poi and entry_target and sl_target:
            has_reversal = reversal_patterns and (any("BULLISH" in k for k in reversal_patterns) if is_bullish else any("BEARISH" in k for k in reversal_patterns))
            
            dist_to_entry = abs(current_price - entry_target)
            # Relax limit requirement slightly: if it's outside 0.2 ATR, set a LIMIT. 
            # Often price doesn't push deep into the zone.
            exec_type = "LIMIT" if dist_to_entry >= 0.2 * atr else "CONFIRMED"
            
            if exec_type == "LIMIT":
                setup_grade = "A" if has_htf_alignment else "B"
                risk_multiplier = 1.0 if setup_grade == "A" else 0.5
                print(f"[AUDIT-LIMIT] {symbol} | Trend: {htf_trend} | Jarak ke Zona: {dist_to_entry:.2f} (ATR: {atr:.2f}) | Grade: {setup_grade} | Membentuk Sinyal LIMIT.")
                reasons.append(f"Setup Profile: LIMIT ORDER Pending (Distance: {dist_to_entry:.4f})")
                reasons.append("[UI_BADGE:PENDING LIMIT ORDER] Pasang order limit di harga entry, tunggu sampai tersentuh.")
                # Retain the POI-edge entry_target and sl_target
            else:
                if has_reversal:
                    if not poi_signature:
                        poi_signature = f"{symbol}_reversal_{current_price}"
                    setup_grade = "A" if has_htf_alignment else "B"
                    risk_multiplier = 1.0 if setup_grade == "A" else 0.5
                    reasons.append("Setup Profile: CONFIRMED ORDER (Market Execution)")
                    reasons.append("[UI_BADGE:ENTRY ZONE ACTIVE] Harga sudah di zona dengan konfirmasi, siap entry sekarang.")
                    for k in reversal_patterns:
                        reasons.append(f"Candlestick Pattern: {k}")
                    entry_target = current_price
                    sl_target = current_price # will be adjusted by ATR
                else:
                    return None
        
        if setup_grade in ["A", "B"] and entry_target and sl_target:
            signal_action = "BUY" if is_bullish else "SELL"
            
            # Market order executes exactly at current_price, but limit uses the set entry_target
            if exec_type == "CONFIRMED":
                entry_target = current_price
            
            # High WR Logic: Gunakan ATR untuk SL dan TP
            # SL = Entry +/- (1.5 * ATR)
            # TP = Entry +/- (1.0 * ATR)
            
            if atr is None or atr <= 0:
                atr = 1.0 if "XAU" in symbol else 0.001
            
            # Adjusted TP Floor (target >= 150 pips)
            if "XAU" in symbol:
                min_tp_dist = 15.0  # 150 pips
            elif "JPY" in symbol:
                min_tp_dist = 1.5
            else:
                min_tp_dist = 0.0150
            
            # Minimum SL Floor and Max Cap
            if "XAU" in symbol:
                min_sl_floor = 5.0  # 50 pips
                max_sl_cap = 8.0    # 80 pips
            elif "JPY" in symbol:
                min_sl_floor = 0.5
                max_sl_cap = 0.8
            else:
                min_sl_floor = 0.0050
                max_sl_cap = 0.0080
                
            # SL = Swing point +/- (1.0 * ATR buffer)
            # Find significant swing point
            swing_point = entry_target
            if engine_ltf:
                swing_point = engine_ltf.get_recent_swing(is_bullish=is_bullish, current_price=entry_target, atr=atr, lookback=50)
            
            if is_bullish:
                sl_target_from_swing = swing_point - (1.0 * atr)
                sl_distance = entry_target - sl_target_from_swing
                
                # Apply minimum floor and max cap
                if sl_distance < min_sl_floor:
                    sl_distance = min_sl_floor
                elif sl_distance > max_sl_cap:
                    sl_distance = max_sl_cap
                    
                sl_target = entry_target - sl_distance
                
                # Dynamic TP targeting at least 150 pips or 2R
                effective_tp_dist = max(2.0 * sl_distance, min_tp_dist)
                tp_target = entry_target + effective_tp_dist
            else:
                sl_target_from_swing = swing_point + (1.0 * atr)
                sl_distance = sl_target_from_swing - entry_target
                
                # Apply minimum floor and max cap
                if sl_distance < min_sl_floor:
                    sl_distance = min_sl_floor
                elif sl_distance > max_sl_cap:
                    sl_distance = max_sl_cap
                    
                sl_target = entry_target + sl_distance
                
                # Dynamic TP targeting at least 150 pips or 2R
                effective_tp_dist = max(2.0 * sl_distance, min_tp_dist)
                tp_target = entry_target - effective_tp_dist
                
            # Prevent generating limit signals that are ALREADY missed
            if exec_type == "LIMIT":
                is_already_missed = False
                if is_bullish and current_price >= tp_target:
                    is_already_missed = True
                elif not is_bullish and current_price <= tp_target:
                    is_already_missed = True
                    
                if is_already_missed:
                    print(f"[{symbol}] Skipping signal: Limit order already missed (Price {current_price} already hit TP {tp_target})")
                    if poi_signature and db:
                        try:
                            db.save_blacklisted_zone(symbol, poi_signature, datetime.now().isoformat())
                        except:
                            pass
                    return None
                
            # --- LLM APPROVAL PHASE ---
            # Bypass LLM for Grade B to ensure fast and frequent signals
            if self.client and setup_grade in ["A", "A+"]:
                news_sentiment = self.fetch_market_sentiment()
                reasons.append(f"Sentiment: {news_sentiment.split(':')[0]}")
                
                try:
                    prompt = f"""
You are an objective and skeptical trading risk analyst. 
Review the following market context for a {signal_action} setup.
Your primary task is to find reasons why this trade might FAIL or hit Stop Loss. 
Consider factors such as low volume fakeouts, mitigated order blocks, contra-trend action on higher timeframes, and stop-loss hunting.
Do NOT be biased by any checklist items. Evaluate the raw facts objectively.

Context:
- Symbol: {symbol}
- Current Price: {current_price}
- Setup Type: {signal_action} ({exec_type})
- HTF Trend: {htf_trend or 'Unknown'}
- Entry Target: {entry_target}
- Stop Loss: {sl_target}
- Take Profit: {tp_target}
- Identified Market Facts: {', '.join(reasons)}
- Fundamental Sentiment: {news_sentiment}

JSON Format to Return:
{{
  "approved": true/false,
  "risk_score": <number 0-100>,
  "failure_risks": ["list of reasons why this could fail"],
  "reasoning": "<short string explaining final decision>"
}}
"""
                    response = await self.client.chat.completions.create(
                        model=self.llm_model_name,
                        messages=[
                            {"role": "system", "content": "You are a trading AI that outputs only raw JSON."},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.2
                    )
                    
                    response_text = response.choices[0].message.content.strip()
                    if response_text.startswith("```"):
                        lines = response_text.split('\n')
                        if lines[0].startswith("```"): lines.pop(0)
                        if lines[-1].startswith("```"): lines.pop(-1)
                        response_text = '\n'.join(lines).strip()
                    
                    llm_decision = json.loads(response_text)
                    
                    if not llm_decision.get("approved"):
                        print(f"[{symbol}] LLM Rejected: {llm_decision.get('reasoning')}")
                        setup_grade = "REJECTED_BY_LLM"
                        reasons.append(f"LLM Rejected: {llm_decision.get('reasoning')}")
                        if poi_signature:
                            self.rejected_zones.add(poi_signature)
                    else:
                        reasons.append(f"LLM Verified (Risk Score: {llm_decision.get('risk_score')}%)")
                    
                except Exception as e:
                    if "404" not in str(e) and "model_not_found" not in str(e):
                        print(f"[{symbol}] LLM Evaluation Error: {e}. Proceeding without LLM.")

            # Calculate Lot Size with Risk Multiplier
            settings = settings_manager.load_settings()
            acc_balance = float(settings.get("account_balance", 10000.0))
            base_risk_pct = float(settings.get("risk_percentage", 1.0))
            
            # Apply grading risk multiplier
            final_risk_pct = base_risk_pct * risk_multiplier
            
            sl_pips = calculate_pips(symbol, entry_target, sl_target)
            lot_size = calculate_lot_size(acc_balance, final_risk_pct, sl_pips, symbol)
            
            # Round prices for cleaner output
            decimal_places = 2 if "XAU" in symbol or "JPY" in symbol else 5
            entry_target = round(entry_target, decimal_places)
            sl_target = round(sl_target, decimal_places)
            tp_target = round(tp_target, decimal_places)
            lot_size = round(lot_size, 2)
                
            signal = {
                "symbol": symbol,
                "type": signal_action,
                "signal_type": exec_type,
                "timestamp": datetime.now().isoformat(),
                "entry": entry_target,
                "sl": sl_target,
                "tp": tp_target,
                "lot_size": lot_size,
                "reasons": reasons,
                "status": "PENDING" if setup_grade != "REJECTED_BY_LLM" else "REJECTED",
                "grade": setup_grade,
                "atr": atr,
                "poi_signature": poi_signature
            }
            
            self.active_signals[symbol] = signal
            self.cooldowns[symbol] = datetime.now() + timedelta(minutes=self.cooldown_minutes)
            
            return signal
            
        return None

    def get_active_signal(self, symbol: str) -> Optional[Dict]:
        return self.active_signals.get(symbol)
        
    def clear_signal(self, symbol: str):
        if symbol in self.active_signals:
            del self.active_signals[symbol]
