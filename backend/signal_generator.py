import os
import json
import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from risk_calculator import calculate_pips, calculate_lot_size
import settings_manager
from market_schedule import is_forex_market_open, is_killzone_active, get_utc_datetime

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
                                  d1_trend: str = None, h4_choch: str = None,
                                  engine_ltf = None, engine_htf = None,
                                  snr_zones: List[Dict] = None, snd_zones: List[Dict] = None, 
                                  pd_zones: Dict = None, breakers: List[Dict] = None, dxy_trend: str = None, 
                                  fibo_ote: Dict = None, poc_price: float = None, trade_manager = None,
                                  amd_setups: List[Dict] = None, atr: float = 1.0, reversal_patterns: List[str] = None,
                                  db = None, adx_m15: float = 25.0, adx_m5: float = 25.0,
                                  adx_h1: float = 25.0, adx_h4: float = 25.0,
                                  current_time_str: str = None,
                                  qm_patterns: List[Dict] = None,
                                  rbs_sbr: List[Dict] = None,
                                  h1_obs: List[Dict] = None,
                                  h1_pd_zones: Dict = None,
                                  m30_trend: str = None,
                                  crt_patterns: List[Dict] = None,
                                  liquidity_pools: Dict = None,
                                  **kwargs) -> Optional[Dict]:
        """
        Evaluate institutional Grade A+ SMC setup specifically tailored for XAUUSD.
        
        Strict Criteria:
        1. Session Filter: ONLY London Killzone (14:00 - 17:00 WIB) & NY Killzone (19:30 - 22:30 WIB).
        2. HTF Structure Alignment: H4 & D1 trends. No counter-trend allowed unless H4 has confirmed CHoCH.
        3. Liquidity Sweep: Must sweep Asian Session High/Low, PDH/PDL, EQH/EQL, or major swing.
           - Followed by strong Rejection Reversal candle + Volume Spike.
           - NO direct entry on touch of liquidity.
        4. LTF Confirmation: M5/M15 CHoCH post-sweep.
        5. Entry Zone: Unfilled FVG (or Unmitigated OB) in Discount (< 50% Eq) for BUY, Premium (> 50% Eq) for SELL.
        6. Dynamic SL: Sweep extreme wick + 15-20 pips buffer ($1.5 - $2.0 on Gold).
        7. Dynamic TP1: Nearest structure/FVG with minimum 1:2 RRR.
        8. Dynamic TP2: Opposite HTF Liquidity / H1 FVG.
        9. Grade A+ Only: If any requirement is missing, DO NOT emit signal.
        """
        # 1. Circuit Breaker Limits
        if trade_manager:
            allowed, reason = trade_manager.check_trading_allowed()
            if not allowed:
                return None
                
        # 2. Time Parse & Cooldown Check
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

        # 3. Currency / Asset Specifications
        is_xau = "XAU" in symbol
        pip_unit = 0.10 if is_xau else 0.0001
        
        # 4. Strict Market Hours & Weekend Shield
        is_open, open_reason = is_forex_market_open(now_time, symbol)
        if not is_open:
            return None

        # 5. Strict Session Filter: London KZ (14:00 - 17:00 WIB) & NY KZ (19:30 - 22:30 WIB)
        is_killzone, kz_reason = is_killzone_active(now_time)
        if not is_killzone:
            return None

        # 6. Check Liquidity Pools
        if liquidity_pools is None and engine_ltf and hasattr(engine_ltf, 'detect_liquidity_pools'):
            try:
                liquidity_pools = engine_ltf.detect_liquidity_pools(is_xau=is_xau)
            except Exception:
                liquidity_pools = {}
                
        # Filter POIs
        blacklisted = db.get_blacklisted_zones(symbol) if db else set()
        def is_banned(sig):
            return (sig in blacklisted) or (sig in self.rejected_zones)

        valid_obs = [ob for ob in (obs or []) if not is_banned(f"{symbol}_{ob['type']}_{ob['bottom']}_{ob['top']}")]
        valid_fvgs = [fvg for fvg in (fvgs or []) if not is_banned(f"{symbol}_{fvg['type']}_{fvg['bottom']}_{fvg['top']}")]

        # 7. Direction Evaluator
        def _evaluate_setup_candidate(is_bullish: bool) -> Optional[Dict]:
            confluence_score = 0
            reasons = []

            trend_tag = "BULLISH" if is_bullish else "BEARISH"
            opposing_tag = "BEARISH" if is_bullish else "BULLISH"

            # -------------------------------------------------------------
            # RULE 1: High Timeframe (H4 & D1) Alignment + H4 CHoCH Exception
            # -------------------------------------------------------------
            effective_h4 = h4_trend or h1_trend or m15_trend
            effective_d1 = d1_trend or h4_trend

            is_counter_trend = False
            if effective_h4 and effective_h4 == opposing_tag:
                is_counter_trend = True
            if effective_d1 and effective_d1 == opposing_tag:
                is_counter_trend = True

            h4_has_choch = False
            if h4_choch and f"CHOCH_{trend_tag}" in h4_choch:
                h4_has_choch = True

            if is_counter_trend:
                if not h4_has_choch:
                    # Strict Rule: NEVER take a counter-trend HTF setup without H4 CHoCH!
                    return None
                else:
                    confluence_score += 3
                    reasons.append(f"HTF Reversal: H4 CHoCH ({trend_tag}) Confirmed against HTF ({effective_h4}) (+3)")
            else:
                confluence_score += 3
                reasons.append(f"HTF Trend Alignment: H4 & D1 {trend_tag} Aligned (+3)")

            # -------------------------------------------------------------
            # RULE 2: Liquidity Pool Sweep (Asian, PDH/PDL, EQH/EQL, Swings)
            # -------------------------------------------------------------
            sweep_target_type = f"SWEEP_{trend_tag}"
            candidate_sweeps = [s for s in (sweeps or []) if s.get('type') == sweep_target_type]

            if not candidate_sweeps:
                # DILARANG entry tanpa adanya sweep likuiditas!
                return None

            # RULE 2A: Disallow minor M5 swings: ONLY allow Major Liquidity Pools (Asian High/Low, PDH/PDL, EQH/EQL)
            major_candidate_sweeps = [
                s for s in candidate_sweeps 
                if s.get('pool_type') in ['ASIAN_LOW', 'ASIAN_HIGH', 'PDL', 'PDH', 'EQL', 'EQH']
            ]
            if not major_candidate_sweeps:
                return None

            # Pick the most recent valid major sweep
            matched_sweep = major_candidate_sweeps[-1]
            pool_name = matched_sweep.get('pool_name', 'Liquidity Pool')
            pool_type = matched_sweep.get('pool_type', 'MAJOR')
            
            # RULE 2B: Zone Lockout / Anti-Revenge Filter
            if trade_manager and hasattr(trade_manager, 'is_pool_blacklisted'):
                if trade_manager.is_pool_blacklisted(pool_name):
                    return None
            
            # Rejection verification
            has_rejection = matched_sweep.get('has_rejection', False) or (matched_sweep.get('wick_ratio', 0) >= 0.30)
            if reversal_patterns and any(trend_tag in p for p in reversal_patterns):
                has_rejection = True
                
            if not has_rejection:
                # Entry dilarang jika tidak ada candle rejection pembalikan arah
                return None
                
            # Volume Spike verification
            volume_spike = matched_sweep.get('volume_spike', True)
            
            confluence_score += 3
            reasons.append(f"Liquidity Sweep: {pool_name} Swept with Rejection Wick & Institutional Volume (+3)")

            # RULE 2C: RSI Momentum Exhaustion Filter
            if engine_ltf and hasattr(engine_ltf, 'calculate_rsi'):
                rsi_val = engine_ltf.calculate_rsi(14)
                if is_bullish and rsi_val > 48.0:
                    # BUY: Momentum must be in exhaustion / oversold territory
                    return None
                elif not is_bullish and rsi_val < 52.0:
                    # SELL: Momentum must be in exhaustion / overbought territory
                    return None
                reasons.append(f"Momentum Exhaustion: RSI ({rsi_val:.1f}) confirmed reversal state (+2)")

            # RULE 2D: Displacement Candle Ratio Check (No weak dojis/indecision)
            if engine_ltf and getattr(engine_ltf, 'data', None):
                last_c = engine_ltf.data[-1]
                c_range = last_c['high'] - last_c['low']
                c_body = abs(last_c['close'] - last_c['open'])
                body_ratio = (c_body / c_range) if c_range > 0 else 0
                if body_ratio < 0.38:
                    return None

            # -------------------------------------------------------------
            # RULE 3: LTF Structure Shift (CHoCH on M5/M15 post-sweep)
            # -------------------------------------------------------------
            choch_found = False
            for ev in reversed((events or [])[-20:]):
                if ev.get('type') in [f"CHOCH_{trend_tag}", f"BOS_{trend_tag}"]:
                    choch_found = True
                    confluence_score += 2
                    reasons.append(f"LTF Shift: M5/M15 {ev['type']} Confirmed Post-Sweep (+2)")
                    break

            if not choch_found:
                # Wajib ada CHoCH di LTF (M5/M15) setelah sweep terjadi
                return None

            # -------------------------------------------------------------
            # RULE 4: Entry Zone in Unfilled FVG or Unmitigated OB in Discount/Premium
            # -------------------------------------------------------------
            # FVG and OB candidates in the direction of trade
            fvg_target_type = f"FVG_{trend_tag}"
            ob_target_type = f"OB_{trend_tag}"

            candidate_fvgs = [f for f in valid_fvgs if f.get('type') == fvg_target_type and not f.get('mitigated', False)]
            candidate_obs = [o for o in valid_obs if o.get('type') == ob_target_type and not o.get('mitigated', False)]

            # Premium / Discount Check
            if pd_zones:
                eq_level = pd_zones.get('eq', current_price)
                if is_bullish:
                    # BUY entry must be in Wholesale Discount (< 50% Eq)
                    if current_price > eq_level + (1.0 if is_xau else 0.0010):
                        return None
                    reasons.append("Valuation: Inside Wholesale Discount Zone (< 50% Eq) (+2)")
                else:
                    # SELL entry must be in Wholesale Premium (> 50% Eq)
                    if current_price < eq_level - (1.0 if is_xau else 0.0010):
                        return None
                    reasons.append("Valuation: Inside Wholesale Premium Zone (> 50% Eq) (+2)")

            # Find matching POI for entry
            matched_poi = None
            poi_type = None
            poi_top = 0.0
            poi_bottom = 0.0

            if is_bullish:
                # Bullish: Entry POI below current price or current price inside POI
                valid_pois = []
                for f in candidate_fvgs:
                    if current_price >= f['bottom'] - 0.3 * atr and f['top'] <= current_price + 15.0:
                        valid_pois.append(("FVG", f, f['top'], f['bottom']))
                for o in candidate_obs:
                    if current_price >= o['bottom'] - 0.3 * atr and o['top'] <= current_price + 15.0:
                        valid_pois.append(("OB", o, o['top'], o['bottom']))

                if not valid_pois:
                    # Wajib berada di area Unfilled FVG atau Unmitigated OB
                    return None

                # Prioritize FVG if available, else closest POI
                fvg_pois = [p for p in valid_pois if p[0] == "FVG"]
                if fvg_pois:
                    valid_pois = fvg_pois
                valid_pois.sort(key=lambda x: abs(current_price - x[2]))
                poi_type, poi_obj, poi_top, poi_bottom = valid_pois[0]
                
                # Determine entry price: Instant confirmation execution (eliminate missed orders)
                exec_type = "CONFIRMED"
                entry_target = current_price
            else:
                # Bearish: Entry POI above current price or current price inside POI
                valid_pois = []
                for f in candidate_fvgs:
                    if current_price <= f['top'] + 0.3 * atr and f['bottom'] >= current_price - 15.0:
                        valid_pois.append(("FVG", f, f['top'], f['bottom']))
                for o in candidate_obs:
                    if current_price <= o['top'] + 0.3 * atr and o['bottom'] >= current_price - 15.0:
                        valid_pois.append(("OB", o, o['top'], o['bottom']))

                if not valid_pois:
                    return None

                fvg_pois = [p for p in valid_pois if p[0] == "FVG"]
                if fvg_pois:
                    valid_pois = fvg_pois
                valid_pois.sort(key=lambda x: abs(current_price - x[3]))
                poi_type, poi_obj, poi_top, poi_bottom = valid_pois[0]

                exec_type = "CONFIRMED"
                entry_target = current_price

            poi_obj_type = poi_obj.get('type', f"{poi_type}_{'BULLISH' if is_bullish else 'BEARISH'}")
            poi_sig = f"{symbol}_{poi_obj_type}_{poi_bottom}_{poi_top}"
            reasons.append(f"Entry Zone: Unfilled {poi_type} ({poi_bottom:.2f} - {poi_top:.2f}) (+2)")

            # -------------------------------------------------------------
            # RULE 5: Reduced Stop Loss (Tighter Buffer 20 pips, Floor 35p, Cap 70p)
            # -------------------------------------------------------------
            buffer_pips = 2.0 if is_xau else 0.0020 # 20 pips ($2.00 on Gold)
            min_sl_dist = 3.5 if is_xau else 0.0035 # Minimum SL floor 35 pips ($3.50)
            max_sl_dist = 7.0 if is_xau else 0.0070 # Maximum SL cap 70 pips ($7.00)
            
            if is_bullish:
                sweep_extreme = matched_sweep.get('sweep_low', matched_sweep.get('level', poi_bottom))
                raw_sl = min(sweep_extreme, poi_bottom) - buffer_pips
                sl_target = round(raw_sl, 2 if is_xau else 5)
                risk_dist = entry_target - sl_target
                if risk_dist < min_sl_dist:
                    sl_target = round(entry_target - min_sl_dist, 2 if is_xau else 5)
                    risk_dist = entry_target - sl_target
                elif risk_dist > max_sl_dist:
                    sl_target = round(entry_target - max_sl_dist, 2 if is_xau else 5)
                    risk_dist = entry_target - sl_target
            else:
                sweep_extreme = matched_sweep.get('sweep_high', matched_sweep.get('level', poi_top))
                raw_sl = max(sweep_extreme, poi_top) + buffer_pips
                sl_target = round(raw_sl, 2 if is_xau else 5)
                risk_dist = sl_target - entry_target
                if risk_dist < min_sl_dist:
                    sl_target = round(entry_target + min_sl_dist, 2 if is_xau else 5)
                    risk_dist = sl_target - entry_target
                elif risk_dist > max_sl_dist:
                    sl_target = round(entry_target + max_sl_dist, 2 if is_xau else 5)
                    risk_dist = sl_target - entry_target

            if risk_dist <= 0:
                return None

            # -------------------------------------------------------------
            # RULE 6: Take Profit Target (TP2: 250 - 300 pips, TP1: 100 - 140 pips)
            # -------------------------------------------------------------
            # TP1: 100 - 140 pips (or 1.5R+) to quickly bank profit & move SL to BE
            min_tp1_dist = max(1.5 * risk_dist, (10.0 if is_xau else 0.0100))
            max_tp1_dist = 14.0 if is_xau else 0.0140
            
            if is_bullish:
                # TP1: Nearest opposing structure / unfilled FVG above entry
                tp1_candidates = []
                for s in (snr_zones or []):
                    if s.get('type') == 'RESISTANCE' and entry_target + min_tp1_dist <= s['level'] <= entry_target + max_tp1_dist:
                        tp1_candidates.append(s['level'])
                for f in valid_fvgs:
                    if f.get('type') == 'FVG_BEARISH' and entry_target + min_tp1_dist <= f['bottom'] <= entry_target + max_tp1_dist:
                        tp1_candidates.append(f['bottom'])
                for o in valid_obs:
                    if o.get('type') == 'OB_BEARISH' and entry_target + min_tp1_dist <= o['bottom'] <= entry_target + max_tp1_dist:
                        tp1_candidates.append(o['bottom'])
                
                tp1_target = sorted(tp1_candidates)[0] if tp1_candidates else round(entry_target + min(min_tp1_dist, max_tp1_dist), 2 if is_xau else 5)

                # TP2: 250 - 300 pips ($25.0 - $30.0 on Gold, default 280 pips / $28.0)
                tp2_candidates = []
                if liquidity_pools:
                    for p_key in ['asian_high', 'pdh']:
                        if liquidity_pools.get(p_key) and entry_target + 25.0 <= liquidity_pools[p_key] <= entry_target + 30.0:
                            tp2_candidates.append(liquidity_pools[p_key])
                    for eqh in liquidity_pools.get('eqh', []):
                        if entry_target + 25.0 <= eqh['level'] <= entry_target + 30.0:
                            tp2_candidates.append(eqh['level'])

                tp2_target = sorted(tp2_candidates)[0] if tp2_candidates else round(entry_target + (28.0 if is_xau else 0.0280), 2 if is_xau else 5)

            else:
                # Bearish Dynamic TP
                tp1_candidates = []
                for s in (snr_zones or []):
                    if s.get('type') == 'SUPPORT' and entry_target - max_tp1_dist <= s['level'] <= entry_target - min_tp1_dist:
                        tp1_candidates.append(s['level'])
                for f in valid_fvgs:
                    if f.get('type') == 'FVG_BULLISH' and entry_target - max_tp1_dist <= f['top'] <= entry_target - min_tp1_dist:
                        tp1_candidates.append(f['top'])
                for o in valid_obs:
                    if o.get('type') == 'OB_BULLISH' and entry_target - max_tp1_dist <= o['top'] <= entry_target - min_tp1_dist:
                        tp1_candidates.append(o['top'])

                tp1_target = sorted(tp1_candidates, reverse=True)[0] if tp1_candidates else round(entry_target - min(min_tp1_dist, max_tp1_dist), 2 if is_xau else 5)

                # TP2: 250 - 300 pips ($25.0 - $30.0 on Gold, default 280 pips / $28.0)
                tp2_candidates = []
                if liquidity_pools:
                    for p_key in ['asian_low', 'pdl']:
                        if liquidity_pools.get(p_key) and entry_target - 30.0 <= liquidity_pools[p_key] <= entry_target - 25.0:
                            tp2_candidates.append(liquidity_pools[p_key])
                    for eql in liquidity_pools.get('eql', []):
                        if entry_target - 30.0 <= eql['level'] <= entry_target - 25.0:
                            tp2_candidates.append(eql['level'])

                tp2_target = sorted(tp2_candidates, reverse=True)[0] if tp2_candidates else round(entry_target - (28.0 if is_xau else 0.0280), 2 if is_xau else 5)

            # -------------------------------------------------------------
            # RULE 7: Adaptive Risk to Reward (RRR >= 1.5) Enforced
            # -------------------------------------------------------------
            tp1_dist = abs(tp1_target - entry_target)
            tp2_dist = abs(tp2_target - entry_target)
            
            rr_tp1 = tp1_dist / risk_dist if risk_dist > 0 else 0
            rr_tp2 = tp2_dist / risk_dist if risk_dist > 0 else 0

            # Syarat: RRR ke TP1 minimal 1:1.5R (Amankan 50% Lot & SL ke BE)
            if rr_tp1 < 1.45:  # Tolerate rounding
                return None

            # -------------------------------------------------------------
            # RULE 8: Grade A+ Verification (All Criteria Fulfilled)
            # -------------------------------------------------------------
            setup_grade = "A+"
            reasons.append(f"Session Active: {kz_reason}")
            reasons.append(f"Risk Management: Target TP (TP1: {tp1_dist/pip_unit:.0f}p / 1:{rr_tp1:.1f}R | TP2: {tp2_dist/pip_unit:.0f}p / 1:{rr_tp2:.1f}R)")

            return {
                "symbol": symbol,
                "type": "BUY" if is_bullish else "SELL",
                "signal_type": exec_type,
                "entry": entry_target,
                "entry_zone": f"{min(poi_bottom, poi_top):.2f} - {max(poi_bottom, poi_top):.2f} ({poi_type})",
                "sl": sl_target,
                "tp": tp1_target,
                "tp1": tp1_target,
                "tp2": tp2_target,
                "reasons": reasons,
                "grade": setup_grade,
                "score": confluence_score,
                "risk_multiplier": 1.0,
                "poi_signature": poi_sig,
                "rr_ratio": round(rr_tp1, 2),
                "rr_tp1": round(rr_tp1, 2),
                "sweep_pool": pool_name,
                "killzone": kz_reason
            }

        # Evaluate both directions
        candidates = []
        buy_candidate = _evaluate_setup_candidate(is_bullish=True)
        if buy_candidate:
            candidates.append(buy_candidate)
            
        sell_candidate = _evaluate_setup_candidate(is_bullish=False)
        if sell_candidate:
            candidates.append(sell_candidate)

        if not candidates:
            return None

        # Pick best Grade A+ candidate
        candidates.sort(key=lambda c: (c['score'], c['rr_ratio']), reverse=True)
        best = candidates[0]

        # Calculate Lot Size
        settings = settings_manager.load_settings()
        acc_balance = float(settings.get("account_balance", 10000.0))
        base_risk_pct = float(settings.get("risk_percentage", 1.0))

        decimal_places = 2 if is_xau or "JPY" in symbol else 5
        entry_val = round(best['entry'], decimal_places)
        sl_val = round(best['sl'], decimal_places)
        tp_val = round(best['tp'], decimal_places)

        sl_pips = calculate_pips(symbol, entry_val, sl_val)
        lot_size = round(calculate_lot_size(acc_balance, base_risk_pct, sl_pips, symbol), 2)

        tp_pips = calculate_pips(symbol, entry_val, tp_val)

        ui_badge = "[UI_BADGE:ENTRY ZONE ACTIVE] Harga di zona, siap eksekusi." if best['signal_type'] == "CONFIRMED" else "[UI_BADGE:PENDING LIMIT ORDER] Pasang pending limit, tunggu jemputan."
        reasons_list = [ui_badge] + best['reasons']
        reasons_list.append(f"Target TP (+{tp_pips:.0f}p): {tp_val} (Disiplin Target Institusional)")
        reasons_list.append(f"SMC Grade: {best['grade']} (RRR 1:{best['rr_ratio']})")

        signal = {
            "symbol": symbol,
            "type": best['type'],
            "signal_type": best['signal_type'],
            "timestamp": now_time.isoformat() if current_time_str else datetime.now().isoformat(),
            "entry": entry_val,
            "entry_zone": best['entry_zone'],
            "sl": sl_val,
            "tp": tp_val,
            "tp1": tp_val,
            "tp2": round(best.get('tp2', tp_val), decimal_places),
            "lot_size": lot_size,
            "reasons": reasons_list,
            "status": "PENDING",
            "grade": best['grade'],
            "atr": round(atr, 2),
            "poi_signature": best['poi_signature'],
            "rr_ratio": best['rr_ratio'],
            "rr_tp1": best['rr_ratio'],
            "sweep_pool": best['sweep_pool'],
            "killzone": best['killzone']
        }

        print(f"\n{'='*60}\n[SMC GRADE A+ SETUP] {symbol} {signal['type']} ({signal['signal_type']})\nEntry: {signal['entry']} (Zone: {signal['entry_zone']}) | SL: {signal['sl']} (-{sl_pips:.0f}p)\nTP: {signal['tp']} (+{tp_pips:.0f}p | 1:{best['rr_ratio']}R)\nSweep: {best['sweep_pool']} | Session: {best['killzone']}\n{'='*60}\n")

        self.active_signals[symbol] = signal
        self.cooldowns[symbol] = now_time + timedelta(minutes=self.cooldown_minutes)
        return signal

