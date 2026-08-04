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
                            snr_zones: List[Dict] = None, snd_zones: List[Dict] = None) -> Optional[Dict]:
        """
        Evaluate if a new signal should be generated based on SMC confluence and LLM approval.
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
        
        # Strict HTF Alignment Check (Disabled for more frequent signals)
        # if htf_trend:
        #     if (is_bullish and htf_trend != "BULLISH") or (not is_bullish and htf_trend != "BEARISH"):
        #         return None
        
        # Check Confluence Criteria
        confluence_score = 0
        reasons = [last_event['type']]
        if htf_trend:
            reasons.append(f"HTF Trend Alignment ({htf_trend})")
        
        entry_target = None
        sl_target = None
        
        # Criterion 1: Order Block proximity / Valid OB
        valid_ob = None
        for ob in reversed(obs):
            if is_bullish and ob['type'] == "OB_BULLISH" and not ob['mitigated']:
                valid_ob = ob
                confluence_score += 1
                reasons.append("Valid Bullish OB")
                entry_target = ob['top']
                sl_target = ob['bottom'] - 0.5 # Small buffer for SL
                break
            elif not is_bullish and ob['type'] == "OB_BEARISH" and not ob['mitigated']:
                valid_ob = ob
                confluence_score += 1
                reasons.append("Valid Bearish OB")
                entry_target = ob['bottom']
                sl_target = ob['top'] + 0.5
                break

        # Criterion 2: FVG
        valid_fvg = None
        for fvg in reversed(fvgs):
            if is_bullish and fvg['type'] == "FVG_BULLISH" and not fvg['mitigated']:
                valid_fvg = fvg
                confluence_score += 1
                reasons.append("Unmitigated Bullish FVG")
                if not entry_target: # FVG can also act as entry if no OB
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

        # Criterion 3: Liquidity Sweep
        if sweeps:
            for sweep in reversed(sweeps):
                if is_bullish and sweep['type'] == "SWEEP_BULLISH":
                    confluence_score += 1
                    reasons.append("Bullish Liquidity Sweep")
                    break
                elif not is_bullish and sweep['type'] == "SWEEP_BEARISH":
                    confluence_score += 1
                    reasons.append("Bearish Liquidity Sweep")
                    break

        # Criterion 4: Support / Resistance (SnR)
        if snr_zones:
            for snr in snr_zones:
                if is_bullish and snr['type'] == "SUPPORT":
                    if abs(current_price - snr['level']) / current_price < 0.002: # Near support
                        confluence_score += 1
                        reasons.append(f"{snr['strength']} Support Touch")
                        if not entry_target:
                            entry_target = snr['level']
                            sl_target = entry_target - 1.0
                        break
                elif not is_bullish and snr['type'] == "RESISTANCE":
                    if abs(current_price - snr['level']) / current_price < 0.002:
                        confluence_score += 1
                        reasons.append(f"{snr['strength']} Resistance Touch")
                        if not entry_target:
                            entry_target = snr['level']
                            sl_target = entry_target + 1.0
                        break

        # Criterion 5: Supply / Demand (SnD)
        if snd_zones:
            for snd in snd_zones:
                if is_bullish and snd['type'] == "DEMAND":
                    if snd['bottom'] <= current_price <= snd['top'] * 1.001:
                        confluence_score += 1
                        reasons.append("Fresh Demand Zone")
                        if not entry_target:
                            entry_target = snd['top']
                            sl_target = snd['bottom'] - 0.5
                        break
                elif not is_bullish and snd['type'] == "SUPPLY":
                    if snd['bottom'] * 0.999 <= current_price <= snd['top']:
                        confluence_score += 1
                        reasons.append("Fresh Supply Zone")
                        if not entry_target:
                            entry_target = snd['bottom']
                            sl_target = snd['top'] + 0.5
                        break

        # Criterion 6: Break and Retest (BnR)
        if len(events) > 0:
            # Look at the most recent structural breaks
            for event in reversed(events[-3:]):
                if is_bullish and "BULLISH" in event['type']:
                    # Broken resistance becomes support
                    broken_level = event['level']
                    if abs(current_price - broken_level) / current_price < 0.0015:
                        confluence_score += 1
                        reasons.append(f"Bullish Break & Retest ({event['type']})")
                        if not entry_target:
                            entry_target = broken_level
                            sl_target = entry_target - 1.0
                        break
                elif not is_bullish and "BEARISH" in event['type']:
                    # Broken support becomes resistance
                    broken_level = event['level']
                    if abs(current_price - broken_level) / current_price < 0.0015:
                        confluence_score += 1
                        reasons.append(f"Bearish Break & Retest ({event['type']})")
                        if not entry_target:
                            entry_target = broken_level
                            sl_target = entry_target + 1.0
                        break

        if confluence_score >= 1 and entry_target and sl_target:
            signal_type = "BUY LIMIT" if is_bullish else "SELL LIMIT"
            
            risk = abs(entry_target - sl_target)
            
            # Pastikan jarak TP tidak terlalu kecil (terutama untuk scalping di TF kecil)
            # Untuk XAU/USD minimal resiko kita anggap 3.0 ($3 atau 30 pips) agar TP minimal $12 (120 pips)
            min_risk = 3.0 if "XAU" in symbol else 0.003
            effective_risk = max(risk, min_risk)
            
            # Gunakan rasio Risk to Reward 1:4
            if is_bullish:
                tp_target = entry_target + (effective_risk * 4)
            else:
                tp_target = entry_target - (effective_risk * 4)
                
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
                        
                    reasons.append(f"LLM Approved (Confidence: {llm_decision.get('confidence')}%)")
                    reasons.append(f"LLM Reasoning: {llm_decision.get('reasoning')}")
                    
                except Exception as e:
                    print(f"[{symbol}] LLM Evaluation Error: {e}. Proceeding without LLM.")

            # Calculate Lot Size
            settings = settings_manager.load_settings()
            acc_balance = float(settings.get("account_balance", 10000.0))
            risk_pct = float(settings.get("risk_percentage", 1.0))
            
            sl_pips = calculate_pips(symbol, entry_target, sl_target)
            lot_size = calculate_lot_size(acc_balance, risk_pct, sl_pips, symbol)
            
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
                "status": "PENDING"
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
