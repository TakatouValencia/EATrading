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
                            amd_setups: List[Dict] = None, atr: float = 0.5, reversal_patterns: List[str] = None) -> Optional[Dict]:
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
            reasons.append("Low Volume Breakout (Fakeout Warning)")
        else:
            confluence_score += 1
            reasons.append("High Volume Breakout (Confirmed)")
        
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
                reasons.append("Strong Intermarket Correlation (DXY Down = Gold Up)")
            elif is_bullish and dxy_trend == "BULLISH":
                confluence_score -= 1
                reasons.append("Weak Intermarket Correlation (DXY Up = Gold Down)")
            elif not is_bullish and dxy_trend == "BULLISH":
                confluence_score += 2
                reasons.append("Strong Intermarket Correlation (DXY Up = Gold Down)")
            elif not is_bullish and dxy_trend == "BEARISH":
                confluence_score -= 1
                reasons.append("Weak Intermarket Correlation (DXY Down = Gold Up)")
        
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
                    reasons.append("High Session Confidence (London/NY Killzone)")
                else:
                    if "XAU" in symbol:
                        # For XAUUSD, ignore signals outside killzone or treat them as Low Session Confidence
                        reasons.append("Low Session Confidence (Outside Killzone)")
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
                # Ensure price is INSIDE the HTF zone
                if fvg['bottom'] <= current_price <= fvg['top']:
                    print(f"[{symbol}] WATCHING: Price inside HTF Bullish FVG ({fvg['bottom']} - {fvg['top']})")
                    valid_fvg = fvg
                    confluence_score += 1
                    reasons.append("Inside HTF Bullish FVG")
                    if not entry_target: 
                        entry_target = current_price
                        sl_target = fvg['bottom'] - buffer_dist
                    break
            elif not is_bullish and fvg['type'] == "FVG_BEARISH" and not fvg['mitigated']:
                if fvg['bottom'] <= current_price <= fvg['top']:
                    print(f"[{symbol}] WATCHING: Price inside HTF Bearish FVG ({fvg['bottom']} - {fvg['top']})")
                    valid_fvg = fvg
                    confluence_score += 1
                    reasons.append("Inside HTF Bearish FVG")
                    if not entry_target:
                        entry_target = current_price
                        sl_target = fvg['top'] + buffer_dist
                    break

        # Criterion 3: Order Block (OB) - HTF Zone Check
        valid_ob = None
        for ob in reversed(obs):
            if is_bullish and ob['type'] == "OB_BULLISH" and not ob['mitigated']:
                if ob['bottom'] <= current_price <= ob['top']:
                    print(f"[{symbol}] WATCHING: Price inside HTF Bullish OB ({ob['bottom']} - {ob['top']})")
                    valid_ob = ob
                    confluence_score += 2
                    reasons.append("Inside HTF Bullish OB")
                    if not entry_target:
                        entry_target = current_price
                        sl_target = ob['bottom'] - buffer_dist
                    break
            elif not is_bullish and ob['type'] == "OB_BEARISH" and not ob['mitigated']:
                if ob['bottom'] <= current_price <= ob['top']:
                    print(f"[{symbol}] WATCHING: Price inside HTF Bearish OB ({ob['bottom']} - {ob['top']})")
                    valid_ob = ob
                    confluence_score += 2
                    reasons.append("Inside HTF Bearish OB")
                    if not entry_target:
                        entry_target = current_price
                        sl_target = ob['top'] + buffer_dist
                    break

        # Criterion 4: Breaker Blocks (ICT) - HTF Zone Check
        valid_breaker = None
        if breakers:
            for brk in reversed(breakers):
                if is_bullish and brk['type'] == "BREAKER_BULLISH":
                    if brk['bottom'] <= current_price <= brk['top']:
                        print(f"[{symbol}] WATCHING: Price inside HTF Bullish Breaker ({brk['bottom']} - {brk['top']})")
                        valid_breaker = brk
                        confluence_score += 2
                        reasons.append("Inside HTF Bullish Breaker")
                        if not entry_target:
                            entry_target = current_price
                            sl_target = brk['bottom'] - buffer_dist
                        break
                elif not is_bullish and brk['type'] == "BREAKER_BEARISH":
                    if brk['bottom'] <= current_price <= brk['top']:
                        print(f"[{symbol}] WATCHING: Price inside HTF Bearish Breaker ({brk['bottom']} - {brk['top']})")
                        valid_breaker = brk
                        confluence_score += 2
                        reasons.append("Inside HTF Bearish Breaker")
                        if not entry_target:
                            entry_target = current_price
                            sl_target = brk['top'] + buffer_dist
                        break

        # Criterion 5 & 6: Support/Resistance (MNSR) & Supply/Demand (Minor confluences)
        valid_snr = False
        if snr_zones:
            for snr in snr_zones:
                if is_bullish and snr['type'] == "SUPPORT" and abs(current_price - snr['level']) / current_price < 0.002:
                    valid_snr = True
                    if snr.get('is_mnsr'):
                        confluence_score += 2
                        reasons.append("Major Support (MNSR)")
                    else:
                        confluence_score += 1
                        reasons.append(f"{snr['strength']} Support")
                    break
                elif not is_bullish and snr['type'] == "RESISTANCE" and abs(current_price - snr['level']) / current_price < 0.002:
                    valid_snr = True
                    if snr.get('is_mnsr'):
                        confluence_score += 2
                        reasons.append("Major Resistance (MNSR)")
                    else:
                        confluence_score += 1
                        reasons.append(f"{snr['strength']} Resistance")
                    break

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
        
        if has_poi:
            # ATR Scalping High WR Logic
            has_reversal = reversal_patterns and (any("BULLISH" in k for k in reversal_patterns) if is_bullish else any("BEARISH" in k for k in reversal_patterns))
            
            if has_reversal:
                if has_htf_alignment:
                    setup_grade = "A"
                    reasons.append("GRADE A: ATR Scalping (HTF Alignment + POI + Reversal Pattern)")
                else:
                    setup_grade = "B"
                    reasons.append("GRADE B: ATR Scalping Counter-Trend (POI + Reversal Pattern)")
                    
                for k in reversal_patterns:
                    reasons.append(f"Candlestick Pattern: {k}")
                    
                entry_target = current_price
                sl_target = current_price # will be adjusted by ATR
            else:
                return None
        
        if setup_grade in ["A", "B"] and entry_target and sl_target:
            signal_type = "BUY" if is_bullish else "SELL"
            
            # Market order executes exactly at current_price
            entry_target = current_price
            
            # High WR Logic: Gunakan ATR untuk SL dan TP
            # SL = Entry +/- (1.5 * ATR)
            # TP = Entry +/- (1.0 * ATR)
            
            if atr is None or atr <= 0:
                atr = 1.0 if "XAU" in symbol else 0.001
            
            # Minimum > 100 pips TP (150 pips = 15.0 points)
            if "XAU" in symbol:
                min_tp_dist = 15.0
            elif "JPY" in symbol:
                min_tp_dist = 1.5
            else:
                min_tp_dist = 0.0150
                
            effective_tp_dist = max(2.0 * atr, min_tp_dist)
            
            # Minimum SL Floor
            if "XAU" in symbol:
                min_sl_floor = max(1.0 * atr, 3.0)  # At least 30 pips or 1.0 ATR
            elif "JPY" in symbol:
                min_sl_floor = max(1.0 * atr, 0.3)
            else:
                min_sl_floor = max(1.0 * atr, 0.0030)
                
            # SL = Swing point +/- (1.0 * ATR buffer)
            # Find significant swing point
            swing_point = entry_target
            if engine_ltf:
                swing_point = engine_ltf.get_recent_swing(is_bullish=is_bullish, current_price=entry_target, atr=atr, lookback=50)
            
            if is_bullish:
                sl_target_from_swing = swing_point - (1.0 * atr)
                sl_distance = entry_target - sl_target_from_swing
                
                # Apply minimum floor
                if sl_distance < min_sl_floor:
                    sl_target = entry_target - min_sl_floor
                else:
                    sl_target = sl_target_from_swing
                    
                tp_target = entry_target + effective_tp_dist
            else:
                sl_target_from_swing = swing_point + (1.0 * atr)
                sl_distance = sl_target_from_swing - entry_target
                
                # Apply minimum floor
                if sl_distance < min_sl_floor:
                    sl_target = entry_target + min_sl_floor
                else:
                    sl_target = sl_target_from_swing
                    
                tp_target = entry_target - effective_tp_dist
                
            # --- LLM APPROVAL PHASE ---
            # Bypass LLM for Grade B to ensure fast and frequent signals
            if self.client and setup_grade in ["A", "A+"]:
                news_sentiment = self.fetch_market_sentiment()
                reasons.append(f"Sentiment: {news_sentiment.split(':')[0]}")
                
                try:
                    prompt = f"""
You are an elite Smart Money Concept (SMC) trader analyzing a potential trade setup.
Evaluate the following context and decide if it's a high probability setup.
Return ONLY valid JSON. Do not include markdown formatting like ```json or any other text.

Context:
- Symbol: {symbol}
- Current Price: {current_price}
- Setup Type: {signal_type}
- HTF Trend: {htf_trend or 'Unknown'}
- Entry Target: {entry_target}
- Stop Loss: {sl_target}
- Take Profit: {tp_target}
- Confluence Reasons: {', '.join(reasons)}
- Setup Grade: {setup_grade}
- Fundamental Sentiment: {news_sentiment}

JSON Format to Return:
{{
  "approved": true/false,
  "confidence": <number 0-100>,
  "reasoning": "<short string explaining why>"
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
                        return None
                        
                    reasons.append(f"LLM Verified ({llm_decision.get('confidence')}%)")
                    
                except Exception as e:
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
                "type": signal_type,
                "timestamp": datetime.now().isoformat(),
                "entry": entry_target,
                "sl": sl_target,
                "tp": tp_target,
                "lot_size": lot_size,
                "reasons": reasons,
                "status": "PENDING",
                "grade": setup_grade,
                "atr": atr
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
