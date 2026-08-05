import os
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from risk_calculator import calculate_pips, calculate_lot_size
import settings_manager

class SignalGenerator:
    def __init__(self, cooldown_hours: int = 1):
        self.active_signals = {}  # symbol -> signal_dict
        self.cooldowns = {}       # symbol -> expiration_time
        self.cooldown_hours = cooldown_hours
        
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

    async def evaluate_confluence(self, symbol: str, current_price: float, 
                            events: List[Dict], obs: List[Dict], fvgs: List[Dict], sweeps: List[Dict] = None, htf_trend: str = None,
                            snr_zones: List[Dict] = None, snd_zones: List[Dict] = None, pd_zones: Dict = None, breakers: List[Dict] = None) -> Optional[Dict]:
        """
        Evaluate if a new signal should be generated based on SMC confluence and LLM approval.
        Implements High Probability Grading (Grade A/B) based on Liquidity Sweeps and Multi-Timeframe.
        """
        
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
        
        entry_target = None
        sl_target = None
        # ICT Killzones (Time of Day Check)
        event_time_str = last_event.get('timestamp')
        is_killzone = False
        if event_time_str:
            try:
                # Basic parsing, assume UTC if no tzinfo, or just extract hour
                if isinstance(event_time_str, str):
                    dt = datetime.fromisoformat(event_time_str.replace('Z', '+00:00'))
                else:
                    dt = event_time_str
                
                # Convert to EST for ICT Killzones (UTC-5/4, approximate as UTC-4 for summer)
                est_hour = (dt.hour - 4) % 24
                # London Killzone (02:00 - 05:00 EST) or NY Killzone (08:00 - 11:00 EST)
                if (2 <= est_hour <= 5) or (8 <= est_hour <= 11):
                    is_killzone = True
                    confluence_score += 2
                    reasons.append("ICT Killzone")
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

        # Criterion 2: Fair Value Gap (FVG) - More aggressive entry
        valid_fvg = None
        for fvg in reversed(fvgs):
            if is_bullish and fvg['type'] == "FVG_BULLISH" and not fvg['mitigated']:
                valid_fvg = fvg
                confluence_score += 1
                reasons.append("Unmitigated Bullish FVG")
                # Enter at the top of the FVG for more frequent fills
                if not entry_target: 
                    entry_target = fvg['top']
                    sl_target = fvg['bottom'] - 0.5
                break
            elif not is_bullish and fvg['type'] == "FVG_BEARISH" and not fvg['mitigated']:
                valid_fvg = fvg
                confluence_score += 1
                reasons.append("Unmitigated Bearish FVG")
                if not entry_target:
                    entry_target = fvg['bottom']
                    sl_target = fvg['top'] + 0.5
                break

        # Criterion 3: Order Block proximity / Valid OB (Stronger than FVG alone)
        valid_ob = None
        for ob in reversed(obs):
            if is_bullish and ob['type'] == "OB_BULLISH" and not ob['mitigated']:
                valid_ob = ob
                confluence_score += 1
                reasons.append("Valid Bullish OB")
                # Overwrite FVG entry if OB exists, or use OB as secondary confirmation
                if not entry_target:
                    entry_target = ob['top']
                    sl_target = ob['bottom'] - 0.5
                break
            elif not is_bullish and ob['type'] == "OB_BEARISH" and not ob['mitigated']:
                valid_ob = ob
                confluence_score += 1
                reasons.append("Valid Bearish OB")
                if not entry_target:
                    entry_target = ob['bottom']
                    sl_target = ob['top'] + 0.5
                break

        # Criterion 4: Breaker Blocks (ICT)
        valid_breaker = None
        if breakers:
            for brk in reversed(breakers):
                if is_bullish and brk['type'] == "BREAKER_BULLISH":
                    valid_breaker = brk
                    confluence_score += 2
                    reasons.append("Bullish Breaker Block")
                    if not entry_target:
                        entry_target = brk['top']
                        sl_target = brk['bottom'] - 0.5
                    break
                elif not is_bullish and brk['type'] == "BREAKER_BEARISH":
                    valid_breaker = brk
                    confluence_score += 2
                    reasons.append("Bearish Breaker Block")
                    if not entry_target:
                        entry_target = brk['bottom']
                        sl_target = brk['top'] + 0.5
                    break

        # Criterion 5 & 6: Support/Resistance (MNSR) & Supply/Demand (Minor confluences)
        if snr_zones:
            for snr in snr_zones:
                if is_bullish and snr['type'] == "SUPPORT" and abs(current_price - snr['level']) / current_price < 0.002:
                    if snr.get('is_mnsr'):
                        confluence_score += 2
                        reasons.append("Major Support (MNSR)")
                    else:
                        confluence_score += 1
                        reasons.append(f"{snr['strength']} Support")
                    break
                elif not is_bullish and snr['type'] == "RESISTANCE" and abs(current_price - snr['level']) / current_price < 0.002:
                    if snr.get('is_mnsr'):
                        confluence_score += 2
                        reasons.append("Major Resistance (MNSR)")
                    else:
                        confluence_score += 1
                        reasons.append(f"{snr['strength']} Resistance")
                    break

        # --- SETUP GRADING LOGIC (ADVANCED SMC) ---
        # Strict validation: If not in correct PD zone, we still allow it but deduct points
        if not in_correct_pd_zone:
            confluence_score -= 1 # Penalize score
            
        # Grade A: Has Sweep/IDM AND (OB, FVG, or Breaker) AND HTF Alignment AND ICT Killzone
        # Grade B: Missing one strong confluence but has decent score
        setup_grade = "NONE"
        risk_multiplier = 0.0
        
        has_poi = (valid_ob or valid_fvg or valid_breaker)
        
        if has_poi:
            # Grade A requirement remains strict
            if has_sweep and has_idm and has_htf_alignment and is_killzone and in_correct_pd_zone:
                setup_grade = "A"
                risk_multiplier = 1.0
                reasons.append("GRADE A: High Prob")
            # Grade B requirement lowered to >= 3 so we get 2-3 signals a day
            elif confluence_score >= 3: 
                setup_grade = "B"
                risk_multiplier = 0.5
                reasons.append("GRADE B: Standard")
        
        if setup_grade in ["A", "B"] and entry_target and sl_target:
            signal_type = "BUY LIMIT" if is_bullish else "SELL LIMIT"
            
            # Aggressive Entry: Pull the entry closer to the broken structure level (S/R Flip)
            # so signals don't feel 'too far' and actually get filled.
            bos_level = last_event.get('level')
            if bos_level:
                if is_bullish:
                    # Enter at the higher of traditional entry or BOS level, capped by current price
                    aggressive_entry = min(max(entry_target, bos_level), current_price - 0.5)
                    entry_target = (entry_target + aggressive_entry) / 2.0
                else:
                    aggressive_entry = max(min(entry_target, bos_level), current_price + 0.5)
                    entry_target = (entry_target + aggressive_entry) / 2.0
            
            # Hitung jarak resiko (SL) berdasarkan struktur market (OB / FVG)
            raw_risk = abs(entry_target - sl_target)
            
            # Widen SL ke 30-50 pips ($3 - $5 untuk XAU/USD) untuk menghindari stop hunt, tapi jangan terlalu lebar
            if "XAU" in symbol:
                min_risk, max_risk = 3.0, 5.0  # 30 - 50 pips (reduced from 50-70)
                min_tp, max_tp = 6.0, 15.0     # 60 - 150 pips
            else:
                min_risk, max_risk = 0.003, 0.005
                min_tp, max_tp = 0.006, 0.015
                
            # Terapkan SL yang dinamis tapi dibatasi
            effective_risk = max(min_risk, min(raw_risk, max_risk))
            
            # Update harga SL sesuai perhitungan risk
            sl_target = (entry_target - effective_risk) if is_bullish else (entry_target + effective_risk)
            
            # Tentukan jarak TP (Target RR kurang lebih 1:2.5 hingga 1:3.5)
            raw_tp_dist = effective_risk * 3.0
            effective_tp_dist = max(min_tp, min(raw_tp_dist, max_tp))
            
            # Update harga TP
            if is_bullish:
                tp_target = entry_target + effective_tp_dist
            else:
                tp_target = entry_target - effective_tp_dist
                
            # --- LLM APPROVAL PHASE ---
            if self.client:
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
                "grade": setup_grade
            }
            
            self.active_signals[symbol] = signal
            self.cooldowns[symbol] = datetime.now() + timedelta(hours=self.cooldown_hours)
            
            return signal
            
        return None

    def get_active_signal(self, symbol: str) -> Optional[Dict]:
        return self.active_signals.get(symbol)
        
    def clear_signal(self, symbol: str):
        if symbol in self.active_signals:
            del self.active_signals[symbol]
