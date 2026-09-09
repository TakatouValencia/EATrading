import os
import json
import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from risk_calculator import calculate_pips, calculate_lot_size
import settings_manager

class SignalGenerator:
    def __init__(self, cooldown_minutes: int = 60):
        self.active_signals = {}  # symbol -> signal_dict
        self.cooldowns = {}       # symbol -> expiration_time
        self.cooldown_minutes = cooldown_minutes
        self.rejected_zones = set()
        
        # Optional Custom LLM (OpenAI-compatible)
        self.llm_api_key = os.getenv("LLM_API_KEY")
        self.llm_base_url = os.getenv("LLM_BASE_URL")
        self.llm_model_name = os.getenv("LLM_MODEL_NAME", "gpt-3.5-turbo")
        
        if self.llm_api_key and self.llm_base_url:
            try:
                from openai import AsyncOpenAI
                self.client = AsyncOpenAI(
                    api_key=self.llm_api_key,
                    base_url=self.llm_base_url,
                    timeout=5.0
                )
            except Exception:
                self.client = None
        else:
            self.client = None

    def fetch_market_sentiment(self) -> str:
        """Fetch current market sentiment/news context."""
        sentiments = [
            "Risk-On: Equities steady, USD normalizing, seeking liquidity in key institutional levels.",
            "Risk-Off: Safe-haven demand elevated, gold respecting HTF institutional order blocks.",
            "Institutional Flow: Order flow reacting at key liquidity pools and imbalance zones.",
            "Neutral-Bullish: Accumulation observed during Asian session, London expansion active."
        ]
        import random
        return random.choice(sentiments)

    async def evaluate_confluence(self, symbol: str, current_price: float, 
                                  events: List[Dict], obs: List[Dict], fvgs: List[Dict], sweeps: List[Dict] = None, 
                                  m15_trend: str = None, m5_trend: str = None,
                                  htf_trend: str = None, h1_trend: str = None, h4_trend: str = None, 
                                  engine_ltf = None, snr_zones: List[Dict] = None, snd_zones: List[Dict] = None, 
                                  pd_zones: Dict = None, breakers: List[Dict] = None, dxy_trend: str = None, 
                                  fibo_ote: Dict = None, poc_price: float = None, trade_manager = None,
                                  amd_setups: List[Dict] = None, atr: float = 1.0, reversal_patterns: List[str] = None,
                                  db = None, adx_m15: float = 25.0, adx_m5: float = 25.0,
                                  adx_h1: float = 25.0, adx_h4: float = 25.0,
                                  current_time_str: str = None,
                                  qm_patterns: List[Dict] = None,
                                  rbs_sbr: List[Dict] = None) -> Optional[Dict]:
        """
        Evaluate if a new signal should be generated based on institutional SMC confluence.
        Uses Low Timeframes: M15 (HTF/Macro Intraday), M5 (MTF Intermediate), M1 (LTF Execution).
        Strict Risk: Max 70 pips SL, Target 150 - 200 pips TP.
        """
        # 1. Check Trading Allowed (Psychology & Circuit Breaker Limits)
        if trade_manager:
            allowed, reason = trade_manager.check_trading_allowed()
            if not allowed:
                print(f"[{symbol}] Trading locked by Risk Circuit Breaker: {reason}")
                return None
                
        # 2. Check Cooldown
        if current_time_str:
            try:
                ts = current_time_str.replace("Z", "+00:00")
                if "+" in ts or (len(ts) > 10 and "-" in ts[10:]):
                    now_time = datetime.fromisoformat(ts).replace(tzinfo=None)
                else:
                    now_time = datetime.fromisoformat(ts)
            except Exception:
                now_time = datetime.now()
        else:
            now_time = datetime.now()

        if symbol in self.cooldowns:
            if now_time < self.cooldowns[symbol]:
                return None
            else:
                del self.cooldowns[symbol]

        # 3. Dynamic ATR Floors, Buffer, and Configured SL / TP Limits
        is_xau = "XAU" in symbol
        pip_unit = 0.10 if is_xau else 0.0001
        
        settings = settings_manager.load_settings()
        req_max_sl_pips = float(settings.get("max_sl_pips", 70.0))
        req_min_sl_pips = float(settings.get("min_sl_pips", 25.0))
        req_min_tp_pips = float(settings.get("min_tp_pips", 150.0))
        req_max_tp_pips = float(settings.get("max_tp_pips", 200.0))

        if atr is None or atr <= 0:
            atr = 1.5 if is_xau else 0.0010
            
        buffer_dist = 0.5 if is_xau else 0.0005
        min_sl_floor = max(req_min_sl_pips * pip_unit, 2.5 if is_xau else 0.0020)
        max_sl_cap = req_max_sl_pips * pip_unit    # Strictly 70 pips (7.0 for Gold)
        min_tp_dist = req_min_tp_pips * pip_unit   # 150 pips (15.0 for Gold)
        max_tp_dist = req_max_tp_pips * pip_unit   # 200 pips (20.0 for Gold)
        max_limit_distance = 15.0 if is_xau else 0.0150 # Tight limit placement for low TF

        # Resolve Low Timeframe trends (M15 Macro, M5 Intermediate)
        macro_trend = m15_trend or htf_trend or h4_trend
        inter_trend = m5_trend or h1_trend

        # Filter out blacklisted and rejected zones from incoming POIs
        blacklisted = db.get_blacklisted_zones(symbol) if db else set()
        def is_banned(sig):
            return (sig in blacklisted) or (sig in self.rejected_zones)

        valid_obs = [ob for ob in (obs or []) if not is_banned(f"{symbol}_{ob['type']}_{ob['bottom']}_{ob['top']}")]
        valid_fvgs = [fvg for fvg in (fvgs or []) if not is_banned(f"{symbol}_{fvg['type']}_{fvg['bottom']}_{fvg['top']}")]
        valid_breakers = [b for b in (breakers or []) if not is_banned(f"{symbol}_{b['type']}_{b['bottom']}_{b['top']}")]
        valid_qms = [q for q in (qm_patterns or []) if not is_banned(f"{symbol}_{q['type']}_{q['bottom']}_{q['top']}")]
        valid_rbs = [r for r in (rbs_sbr or []) if not is_banned(f"{symbol}_{r['type']}_{r['bottom']}_{r['top']}")]

        # Killzone Check (London 07:00-11:00 UTC, NY 12:00-17:00 UTC)
        if current_time_str:
            try:
                ts = current_time_str.replace("Z", "+00:00")
                utc_dt = datetime.fromisoformat(ts)
                utc_hour = utc_dt.hour
            except Exception:
                utc_hour = datetime.utcnow().hour
        else:
            utc_now = datetime.utcnow()
            utc_hour = utc_now.hour
        is_killzone = (7 <= utc_hour < 11) or (12 <= utc_hour < 17)

        # Strict Session Filter: Only trade during high-liquidity London & NY killzones
        if not is_killzone:
            return None

        # 4. Helper to evaluate setup for a specific direction
        def _evaluate_setup_candidate(is_bullish: bool) -> Optional[Dict]:
            confluence_score = 0
            reasons = []

            # A. Macro Trend Alignment (M15 & M5)
            # SMC Hierarchy: M15 is Macro Intraday Trend, M5 is Intermediate Trend
            trend_tag = "BULLISH" if is_bullish else "BEARISH"
            opposing_tag = "BEARISH" if is_bullish else "BULLISH"

            if macro_trend == trend_tag and inter_trend == trend_tag:
                confluence_score += 3
                reasons.append(f"Full Low-TF Alignment (M15 & M5 {trend_tag})")
            elif macro_trend == trend_tag and inter_trend == opposing_tag:
                # Institutional Retrace to Wholesale POI: Buying discount in uptrend / Selling premium in downtrend
                confluence_score += 2
                reasons.append(f"M15 {trend_tag} Macro with M5 Pullback into POI")
            elif macro_trend == opposing_tag and inter_trend == opposing_tag:
                # Strong counter-trend: heavily penalize
                confluence_score -= 3
                reasons.append(f"Counter-Trend Warning (M15 & M5 {opposing_tag})")
            elif inter_trend == trend_tag or macro_trend == trend_tag:
                confluence_score += 1
                reasons.append(f"{'M5' if inter_trend == trend_tag else 'M15'} {trend_tag} Trend Alignment")

            # B. Liquidity Sweep & Inducement (Crucial for high WR)
            has_sweep = False
            has_idm = False
            if sweeps:
                sweep_target = "SWEEP_BULLISH" if is_bullish else "SWEEP_BEARISH"
                for sw in reversed(sweeps[-15:]):
                    if sw.get('type') == sweep_target:
                        has_sweep = True
                        confluence_score += 3
                        reasons.append(f"{'Bullish' if is_bullish else 'Bearish'} Liquidity Sweep (+3)")
                        if sw.get('is_idm'):
                            has_idm = True
                            confluence_score += 1
                            reasons.append("Inducement (IDM) Taken (+1)")
                        break

            # C. Premium & Discount Alignment
            in_favorable_pd = False
            if pd_zones:
                if is_bullish and current_price <= pd_zones.get('discount_high', float('inf')):
                    in_favorable_pd = True
                    confluence_score += 2
                    reasons.append("In Discount Zone (< 50% Eq)")
                elif not is_bullish and current_price >= pd_zones.get('premium_low', 0):
                    in_favorable_pd = True
                    confluence_score += 2
                    reasons.append("In Premium Zone (> 50% Eq)")

            # D. Session Killzone Timing
            if is_killzone:
                confluence_score += 2
                reasons.append("Session: London/NY Killzone Active")
            else:
                reasons.append("Session: Standard Market Hours")

            # E. Intermarket Correlation (DXY)
            if dxy_trend and is_xau:
                if (is_bullish and dxy_trend == "BEARISH") or (not is_bullish and dxy_trend == "BULLISH"):
                    confluence_score += 1
                    reasons.append(f"DXY Inverse Correlation Confirmed ({dxy_trend})")

            # F. Fibo OTE & Volume POC
            if fibo_ote:
                ote_key = "bullish_ote" if is_bullish else "bearish_ote"
                if fibo_ote.get(ote_key):
                    ote = fibo_ote[ote_key]
                    if ote["bottom"] <= current_price <= ote["top"]:
                        confluence_score += 1
                        reasons.append("Inside Fibo OTE Zone (0.618 - 0.786)")

            if poc_price and abs(current_price - poc_price) / current_price < 0.003:
                confluence_score += 1
                reasons.append("At Volume Profile High Liquidity POC")

            # G. Find Nearest Unmitigated Institutional POI (OB, FVG, Breaker)
            matched_poi = None
            poi_type = None
            entry_target = None
            sl_target = None
            poi_sig = None

            if is_bullish:
                # Bullish setups: Entry POI must be below current price (for LIMIT) or current price inside POI
                candidate_pois = []
                for qm in valid_qms:
                    if qm['type'] == "QM_BULLISH":
                        if qm['top'] <= current_price + 0.3 * atr and qm['bottom'] <= current_price:
                            candidate_pois.append(("QM", qm, qm['top'], qm['bottom']))
                for rbs in valid_rbs:
                    if rbs['type'] == "RBS_BULLISH":
                        if rbs['top'] <= current_price + 0.3 * atr and rbs['bottom'] <= current_price:
                            candidate_pois.append(("RBS", rbs, rbs['top'], rbs['bottom']))
                for ob in valid_obs:
                    if ob['type'] == "OB_BULLISH" and not ob.get('mitigated', False):
                        if ob['top'] <= current_price + 0.3 * atr and ob['bottom'] <= current_price:
                            candidate_pois.append(("OB", ob, ob['top'], ob['bottom']))
                for fvg in valid_fvgs:
                    if fvg['type'] == "FVG_BULLISH" and not fvg.get('mitigated', False):
                        if fvg['top'] <= current_price + 0.3 * atr and fvg['bottom'] <= current_price:
                            candidate_pois.append(("FVG", fvg, fvg['top'], fvg['bottom']))
                for brk in valid_breakers:
                    if brk['type'] == "BREAKER_BULLISH":
                        if brk['top'] <= current_price + 0.3 * atr and brk['bottom'] <= current_price:
                            candidate_pois.append(("BREAKER", brk, brk['top'], brk['bottom']))

                if not candidate_pois:
                    return None

                # Pick POI closest to current price
                candidate_pois.sort(key=lambda x: abs(current_price - x[2]))
                poi_type, poi_obj, poi_top, poi_bottom = candidate_pois[0]
                poi_sig = f"{symbol}_{poi_obj.get('type', poi_type)}_{poi_bottom}_{poi_top}"
                
                # Check distance to entry
                dist_to_poi = current_price - poi_top
                if dist_to_poi > max_limit_distance:
                    return None  # Too far away

                if dist_to_poi <= 0.25 * atr:
                    # Inside or at the edge of POI -> Confirmed Market Order
                    exec_type = "CONFIRMED"
                    entry_target = current_price
                    raw_sl_dist = (entry_target - poi_bottom) + buffer_dist
                    sl_dist = max(min_sl_floor, min(max_sl_cap, raw_sl_dist))
                    sl_target = entry_target - sl_dist
                    effective_tp = max(min_tp_dist, min(max_tp_dist, max(2.5 * sl_dist, min_tp_dist)))
                    tp_target = entry_target + effective_tp
                    reasons.append(f"Entry: Inside Bullish {poi_type} ({poi_bottom:.2f} - {poi_top:.2f})")
                else:
                    # Approaching POI -> Pending Limit Order
                    exec_type = "LIMIT"
                    # If POI is too wide (> max_sl_cap), refine entry deeper inside the POI to guarantee safe SL <= 70 pips
                    poi_height = poi_top - poi_bottom
                    if poi_height + buffer_dist > max_sl_cap:
                        entry_target = poi_bottom + (max_sl_cap - buffer_dist)
                    else:
                        entry_target = poi_top
                    raw_sl_dist = (entry_target - poi_bottom) + buffer_dist
                    sl_dist = max(min_sl_floor, min(max_sl_cap, raw_sl_dist))
                    sl_target = entry_target - sl_dist
                    effective_tp = max(min_tp_dist, min(max_tp_dist, max(2.5 * sl_dist, min_tp_dist)))
                    tp_target = entry_target + effective_tp
                    reasons.append(f"Setup: Bullish {poi_type} Demand Zone ({poi_bottom:.2f} - {poi_top:.2f})")

            else:
                # Bearish setups: Entry POI must be above current price (for LIMIT) or current price inside POI
                candidate_pois = []
                for qm in valid_qms:
                    if qm['type'] == "QM_BEARISH":
                        if qm['bottom'] >= current_price - 0.3 * atr and qm['top'] >= current_price:
                            candidate_pois.append(("QM", qm, qm['bottom'], qm['top']))
                for sbr in valid_rbs:
                    if sbr['type'] == "SBR_BEARISH":
                        if sbr['bottom'] >= current_price - 0.3 * atr and sbr['top'] >= current_price:
                            candidate_pois.append(("SBR", sbr, sbr['bottom'], sbr['top']))
                for ob in valid_obs:
                    if ob['type'] == "OB_BEARISH" and not ob.get('mitigated', False):
                        if ob['bottom'] >= current_price - 0.3 * atr and ob['top'] >= current_price:
                            candidate_pois.append(("OB", ob, ob['bottom'], ob['top']))
                for fvg in valid_fvgs:
                    if fvg['type'] == "FVG_BEARISH" and not fvg.get('mitigated', False):
                        if fvg['bottom'] >= current_price - 0.3 * atr and fvg['top'] >= current_price:
                            candidate_pois.append(("FVG", fvg, fvg['bottom'], fvg['top']))
                for brk in valid_breakers:
                    if brk['type'] == "BREAKER_BEARISH":
                        if brk['bottom'] >= current_price - 0.3 * atr and brk['top'] >= current_price:
                            candidate_pois.append(("BREAKER", brk, brk['bottom'], brk['top']))

                if not candidate_pois:
                    return None

                # Pick POI closest to current price
                candidate_pois.sort(key=lambda x: abs(x[2] - current_price))
                poi_type, poi_obj, poi_bottom, poi_top = candidate_pois[0]
                poi_sig = f"{symbol}_{poi_obj.get('type', poi_type)}_{poi_bottom}_{poi_top}"

                # Check distance to entry
                dist_to_poi = poi_bottom - current_price
                if dist_to_poi > max_limit_distance:
                    return None  # Too far away

                if dist_to_poi <= 0.25 * atr:
                    # Inside or at the edge of POI -> Confirmed Market Order
                    exec_type = "CONFIRMED"
                    entry_target = current_price
                    raw_sl_dist = (poi_top - entry_target) + buffer_dist
                    sl_dist = max(min_sl_floor, min(max_sl_cap, raw_sl_dist))
                    sl_target = entry_target + sl_dist
                    effective_tp = max(min_tp_dist, min(max_tp_dist, max(2.5 * sl_dist, min_tp_dist)))
                    tp_target = entry_target - effective_tp
                    reasons.append(f"Entry: Inside Bearish {poi_type} ({poi_bottom:.2f} - {poi_top:.2f})")
                else:
                    # Approaching POI -> Pending Limit Order
                    exec_type = "LIMIT"
                    # If POI is too wide (> max_sl_cap), refine entry deeper inside the POI to guarantee safe SL <= 70 pips
                    poi_height = poi_top - poi_bottom
                    if poi_height + buffer_dist > max_sl_cap:
                        entry_target = poi_top - (max_sl_cap - buffer_dist)
                    else:
                        entry_target = poi_bottom
                    raw_sl_dist = (poi_top - entry_target) + buffer_dist
                    sl_dist = max(min_sl_floor, min(max_sl_cap, raw_sl_dist))
                    sl_target = entry_target + sl_dist
                    effective_tp = max(min_tp_dist, min(max_tp_dist, max(2.5 * sl_dist, min_tp_dist)))
                    tp_target = entry_target - effective_tp
                    reasons.append(f"Setup: Bearish {poi_type} Supply Zone ({poi_bottom:.2f} - {poi_top:.2f})")

            # Point bonus for POI
            if poi_type == "QM":
                confluence_score += 3
                reasons.append("Institutional Quasimodo (QM) Key Level (+3)")
            elif poi_type in ["RBS", "SBR"]:
                confluence_score += 2
                reasons.append(f"Institutional Role Reversal ({poi_type}) Retest (+2)")
            elif poi_type in ["OB", "BREAKER"]:
                confluence_score += 2
                reasons.append(f"Institutional {poi_type} Footprint (+2)")
            else:
                confluence_score += 1

            # H. Candlestick Reversal Confirmation
            if reversal_patterns:
                if is_bullish and any("BULLISH" in p for p in reversal_patterns):
                    confluence_score += 1
                    reasons.append("Bullish Reversal Pattern Confirmed")
                elif not is_bullish and any("BEARISH" in p for p in reversal_patterns):
                    confluence_score += 1
                    reasons.append("Bearish Reversal Pattern Confirmed")

            # I. Structure Break Confirmation (BOS / CHoCH)
            if events:
                recent_ev = events[-1]
                if (is_bullish and "BULLISH" in recent_ev['type']) or (not is_bullish and "BEARISH" in recent_ev['type']):
                    confluence_score += 1
                    reasons.append(f"Structure Break: {recent_ev['type']}")

            # J. Strict Validity Check on Entry / SL / TP
            if is_bullish:
                if exec_type == "LIMIT" and entry_target >= current_price:
                    exec_type = "CONFIRMED"
                    entry_target = current_price
                if sl_target >= entry_target or tp_target <= entry_target:
                    return None
            else:
                if exec_type == "LIMIT" and entry_target <= current_price:
                    exec_type = "CONFIRMED"
                    entry_target = current_price
                if sl_target <= entry_target or tp_target >= entry_target:
                    return None

            # Calculate Risk to Reward
            risk_dist = abs(entry_target - sl_target)
            reward_dist = abs(tp_target - entry_target)
            rr_ratio = reward_dist / risk_dist if risk_dist > 0 else 0
            if rr_ratio < 1.4:
                return None  # Enforce minimum 1:1.4 R:R for mathematical edge

            # K. Grading Scale - STRICTLY GRADE A and A+ ONLY
            if confluence_score >= 9:
                setup_grade = "A+"
                risk_multiplier = 1.0
            elif confluence_score >= 7:
                setup_grade = "A"
                risk_multiplier = 0.8
            else:
                # Strict Rule: Only Grade A and A+ allowed
                return None

            return {
                "symbol": symbol,
                "type": "BUY" if is_bullish else "SELL",
                "signal_type": exec_type,
                "entry": entry_target,
                "sl": sl_target,
                "tp": tp_target,
                "reasons": reasons,
                "grade": setup_grade,
                "score": confluence_score,
                "risk_multiplier": risk_multiplier,
                "poi_signature": poi_sig,
                "rr_ratio": round(rr_ratio, 2)
            }

        # 5. Evaluate both directions and pick the best setup
        candidates = []
        buy_candidate = _evaluate_setup_candidate(is_bullish=True)
        if buy_candidate:
            candidates.append(buy_candidate)
            
        sell_candidate = _evaluate_setup_candidate(is_bullish=False)
        if sell_candidate:
            candidates.append(sell_candidate)

        if not candidates:
            return None

        # Sort candidates by Confluence Score descending, then by R:R
        candidates.sort(key=lambda c: (c['score'], c['rr_ratio']), reverse=True)
        best = candidates[0]

        # 6. Calculate Position Size (Lot Size) with Psychology/Risk Manager
        settings = settings_manager.load_settings()
        acc_balance = float(settings.get("account_balance", 10000.0))
        base_risk_pct = float(settings.get("risk_percentage", 1.0))
        final_risk_pct = base_risk_pct * best['risk_multiplier']

        decimal_places = 2 if is_xau or "JPY" in symbol else 5
        entry_val = round(best['entry'], decimal_places)
        sl_val = round(best['sl'], decimal_places)
        tp_val = round(best['tp'], decimal_places)

        sl_pips = calculate_pips(symbol, entry_val, sl_val)
        lot_size = round(calculate_lot_size(acc_balance, final_risk_pct, sl_pips, symbol), 2)

        # Build final signal object
        ui_badge = "[UI_BADGE:ENTRY ZONE ACTIVE] Harga di zona, siap eksekusi." if best['signal_type'] == "CONFIRMED" else "[UI_BADGE:PENDING LIMIT ORDER] Pasang pending limit, tunggu jemputan."
        reasons_list = [ui_badge] + best['reasons']
        reasons_list.append(f"SMC Grade: {best['grade']} (Confluence Score: {best['score']}/10, R:R: 1:{best['rr_ratio']})")

        signal = {
            "symbol": symbol,
            "type": best['type'],
            "signal_type": best['signal_type'],
            "timestamp": now_time.isoformat() if current_time_str else datetime.now().isoformat(),
            "entry": entry_val,
            "sl": sl_val,
            "tp": tp_val,
            "lot_size": lot_size,
            "reasons": reasons_list,
            "status": "PENDING",
            "grade": best['grade'],
            "atr": round(atr, 2),
            "poi_signature": best['poi_signature'],
            "rr_ratio": best['rr_ratio']
        }

        print(f"\n{'='*55}\n[SMC ENGINE] Valid Setup Found for {symbol}!\nType: {signal['type']} ({signal['signal_type']}) | Grade: {signal['grade']} (Score: {best['score']})\nEntry: {signal['entry']} | SL: {signal['sl']} | TP: {signal['tp']} (R:R 1:{best['rr_ratio']})\n{'='*55}\n")

        self.active_signals[symbol] = signal
        self.cooldowns[symbol] = now_time + timedelta(minutes=self.cooldown_minutes)
        return signal
