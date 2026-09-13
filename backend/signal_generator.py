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
                                  engine_ltf = None, snr_zones: List[Dict] = None, snd_zones: List[Dict] = None, 
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
                                  crt_patterns: List[Dict] = None) -> Optional[Dict]:
        """
        Evaluate if a new signal should be generated based on institutional SMC confluence.
        Top-Down Architecture: H1 (Macro Bias) -> M15/M30 (Structural Shift) -> M5/M1 (Execution Snipe).
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
        req_max_sl_pips = float(settings.get("max_sl_pips", 90.0))
        req_min_sl_pips = float(settings.get("min_sl_pips", 50.0))
        req_min_tp_pips = float(settings.get("min_tp_pips", 120.0))
        req_max_tp_pips = float(settings.get("max_tp_pips", 250.0))
        req_partial_tp_pips = float(settings.get("partial_tp_pips", 100.0))
        max_daily_trades = int(settings.get("max_daily_trades", 2))

        # Check daily completed trades limit (Quality over Quantity for daily trading)
        if trade_manager and hasattr(trade_manager, "daily_completed_trades"):
            if trade_manager.daily_completed_trades >= max_daily_trades:
                print(f"[{symbol}] Daily trades limit reached ({trade_manager.daily_completed_trades}/{max_daily_trades}). Skipping new setups.")
                return None

        if atr is None or atr <= 0:
            atr = 1.5 if is_xau else 0.0010
            
        buffer_dist = max(1.0 * atr, 1.5 if is_xau else 0.0010)
        min_sl_floor = max(req_min_sl_pips * pip_unit, 5.0 if is_xau else 0.0030) # Minimum 50 pips ($5.00) on Gold
        max_sl_cap = req_max_sl_pips * pip_unit    # 90 pips ($9.00 for Gold)
        min_tp_dist = req_min_tp_pips * pip_unit   # 120 pips ($12.0 for Gold)
        max_tp_dist = req_max_tp_pips * pip_unit   # 250 pips ($25.0 for Gold)
        partial_tp_dist = req_partial_tp_pips * pip_unit # 100 pips ($10.0 for Gold)
        max_limit_distance = 15.0 if is_xau else 0.0150 # Tight limit placement for low TF

        # Resolve Low Timeframe trends (M15 Macro, M5 Intermediate)
        macro_trend = m15_trend or htf_trend or h4_trend
        inter_trend = m5_trend

        # Filter out blacklisted and rejected zones from incoming POIs
        blacklisted = db.get_blacklisted_zones(symbol) if db else set()
        def is_banned(sig):
            return (sig in blacklisted) or (sig in self.rejected_zones)

        valid_obs = [ob for ob in (obs or []) if not is_banned(f"{symbol}_{ob['type']}_{ob['bottom']}_{ob['top']}")]
        valid_fvgs = [fvg for fvg in (fvgs or []) if not is_banned(f"{symbol}_{fvg['type']}_{fvg['bottom']}_{fvg['top']}")]
        valid_breakers = [b for b in (breakers or []) if not is_banned(f"{symbol}_{b['type']}_{b['bottom']}_{b['top']}")]
        valid_qms = [q for q in (qm_patterns or []) if not is_banned(f"{symbol}_{q['type']}_{q['bottom']}_{q['top']}")]
        valid_rbs = [r for r in (rbs_sbr or []) if not is_banned(f"{symbol}_{r['type']}_{r['bottom']}_{r['top']}")]
        valid_crts = [c for c in (crt_patterns or []) if not is_banned(f"{symbol}_{c['type']}_{c['bottom']}_{c['top']}")]
        valid_snds = [s for s in (snd_zones or []) if not is_banned(f"{symbol}_{s['type']}_{s['bottom']}_{s['top']}")]

        # Strict Market Hours & Weekend Shield (Forex / Gold closes Friday 21:00 UTC - Sunday 21:00 UTC)
        is_open, open_reason = is_forex_market_open(now_time, symbol)
        if not is_open:
            return None

        # Strict Session Filter: Only trade during high-liquidity London & NY killzones
        is_killzone, kz_reason = is_killzone_active(now_time)
        if not is_killzone:
            return None

        # Strict ADX Chop Filter: Require directional momentum on M15 or H1
        if adx_m15 < 20.0 and adx_h1 < 20.0:
            return None

        # 4. Helper to evaluate setup for a specific direction
        def _evaluate_setup_candidate(is_bullish: bool) -> Optional[Dict]:
            confluence_score = 0
            reasons = []

            trend_tag = "BULLISH" if is_bullish else "BEARISH"
            opposing_tag = "BEARISH" if is_bullish else "BULLISH"

            # A0. H1 Macro Compass & M30 Alignment
            if h1_trend:
                if h1_trend == trend_tag:
                    confluence_score += 3
                    reasons.append(f"H1 Macro Bias Confirmed ({h1_trend}) (+3)")
                else:
                    # Strict Institutional Rule: NEVER trade against the H1 Macro Trend!
                    return None

            if m30_trend and m30_trend == trend_tag:
                confluence_score += 1
                reasons.append(f"M30 Structure Aligned ({m30_trend}) (+1)")

            # A1. Check H1 Obstacle Walls
            if h1_obs:
                for hob in h1_obs:
                    if is_bullish and hob.get('type') == 'OB_BEARISH' and not hob.get('mitigated', False):
                        if current_price < hob['bottom'] <= current_price + (8.0 if is_xau else 0.0080):
                            confluence_score -= 2
                            reasons.append("Approaching H1 Supply (-2)")
                    elif not is_bullish and hob.get('type') == 'OB_BULLISH' and not hob.get('mitigated', False):
                        if current_price > hob['top'] >= current_price - (8.0 if is_xau else 0.0080):
                            confluence_score -= 2
                            reasons.append("Approaching H1 Demand (-2)")

            # A2. H1 Wholesale Dealing Range
            if h1_pd_zones:
                if is_bullish and current_price <= h1_pd_zones.get('eq', float('inf')):
                    confluence_score += 2
                    reasons.append("In H1 Wholesale Discount Area (< 50% Eq) (+2)")
                elif not is_bullish and current_price >= h1_pd_zones.get('eq', 0):
                    confluence_score += 2
                    reasons.append("In H1 Wholesale Premium Area (> 50% Eq) (+2)")

            # A3. Macro Trend Alignment (M15 & M5)
            # SMC Hierarchy: M15 is Macro Intraday Trend, M5 is Intermediate Trend
            if macro_trend == trend_tag and inter_trend == trend_tag:
                confluence_score += 3
                reasons.append(f"Full Low-TF Alignment (M15 & M5 {trend_tag})")
            elif macro_trend == trend_tag and inter_trend == opposing_tag:
                confluence_score += 2
                reasons.append(f"M15 {trend_tag} Macro with M5 Pullback into POI")
            elif macro_trend == opposing_tag and inter_trend == opposing_tag:
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
                if is_bullish:
                    if current_price <= pd_zones.get('discount_high', float('inf')) + 0.3 * atr:
                        in_favorable_pd = True
                        confluence_score += 2
                        reasons.append("In Discount Zone (< 50% Eq)")
                    elif current_price > pd_zones.get('premium_low', float('inf')):
                        # Strict Institutional Rule: Never buy in deep premium (expensive area)
                        return None
                else:
                    if current_price >= pd_zones.get('premium_low', 0) - 0.3 * atr:
                        in_favorable_pd = True
                        confluence_score += 2
                        reasons.append("In Premium Zone (> 50% Eq)")
                    elif current_price < pd_zones.get('discount_high', 0):
                        # Strict Institutional Rule: Never sell in deep discount (cheap area)
                        return None

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

            # F1. Candle Range Theory (CRT) Liquidity Sweep Confluence
            if valid_crts:
                crt_target = "CRT_BULLISH" if is_bullish else "CRT_BEARISH"
                matched_crts = [c for c in valid_crts if c.get('type') == crt_target]
                if matched_crts:
                    confluence_score += 3
                    reasons.append(f"Institutional CRT Liquidity Sweep Confirmed ({crt_target}) (+3)")

            # F2. Institutional Supply & Demand (SnD) Alignment
            if valid_snds:
                snd_target = "DEMAND" if is_bullish else "SUPPLY"
                matched_snds = [s for s in valid_snds if s.get('type') == snd_target]
                if any((s['bottom'] - 0.3 * atr) <= current_price <= (s['top'] + 0.3 * atr) for s in matched_snds):
                    confluence_score += 2
                    reasons.append(f"Institutional SnD ({snd_target}) Zone Active (+2)")

            # F3. Support & Resistance (SnR) Multi-Touch Key Levels
            if snr_zones:
                snr_target = "SUPPORT" if is_bullish else "RESISTANCE"
                matched_snrs = [s for s in snr_zones if s.get('type') == snr_target]
                for s in matched_snrs:
                    if abs(current_price - s['level']) <= 0.4 * atr or (is_bullish and s['level'] <= current_price <= s['level'] + 0.4 * atr) or (not is_bullish and s['level'] >= current_price >= s['level'] - 0.4 * atr):
                        bonus = 2 if s.get('is_mnsr') or s.get('touches', 0) >= 3 else 1
                        confluence_score += bonus
                        reasons.append(f"Key {s['type'].title()} Level ({s['level']:.2f}, {s.get('touches', 2)} touches) (+{bonus})")
                        break

            # G. Find Nearest Unmitigated Institutional POI (OB, FVG, Breaker, CRT, SnD, QM, RBS/SBR)
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
                for crt in valid_crts:
                    if crt['type'] == "CRT_BULLISH":
                        if crt['top'] <= current_price + 0.3 * atr and crt['bottom'] <= current_price:
                            candidate_pois.append(("CRT", crt, crt['top'], crt['bottom']))
                for snd in valid_snds:
                    if snd['type'] == "DEMAND":
                        if snd['top'] <= current_price + 0.3 * atr and snd['bottom'] <= current_price:
                            candidate_pois.append(("DEMAND", snd, snd['top'], snd['bottom']))
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
                    # Inside or at the edge of POI: Require fresh rejection candle or immediate sweep
                    has_fresh_sweep = bool(sweeps and sweeps[-1].get('type') == 'SWEEP_BULLISH' and (abs(current_price - sweeps[-1].get('level', current_price)) <= 0.6 * atr))
                    has_candle_rev = bool(reversal_patterns and any("BULLISH" in p for p in reversal_patterns))
                    has_confirmation = has_candle_rev or has_fresh_sweep
                    if has_confirmation:
                        exec_type = "CONFIRMED"
                        entry_target = current_price
                        reasons.append(f"Entry: Confirmed Reaction Inside Bullish {poi_type} ({poi_bottom:.2f} - {poi_top:.2f})")
                    else:
                        # Falling knife protection: Wait with limit order at POI 50% equilibrium
                        exec_type = "LIMIT"
                        entry_target = round((poi_top + poi_bottom) / 2.0, 2 if is_xau else 5)
                        reasons.append(f"Setup: Bullish {poi_type} Equilibrium Limit ({entry_target:.2f})")
                else:
                    # Approaching POI -> Pending Limit Order
                    exec_type = "LIMIT"
                    poi_height = poi_top - poi_bottom
                    if poi_height + buffer_dist > max_sl_cap:
                        entry_target = poi_bottom + (max_sl_cap - buffer_dist)
                    else:
                        entry_target = poi_top
                    reasons.append(f"Setup: Bullish {poi_type} Demand Zone ({poi_bottom:.2f} - {poi_top:.2f})")

                # Structural Swing SL calculation
                swing_ref = poi_bottom
                if engine_ltf and hasattr(engine_ltf, 'get_recent_swing'):
                    try:
                        swing_ref = engine_ltf.get_recent_swing(is_bullish=True, current_price=entry_target, atr=atr, lookback=30)
                    except Exception:
                        swing_ref = poi_bottom

                sweep_ref = poi_bottom
                if sweeps:
                    bullish_sweeps = [sw.get('level', poi_bottom) for sw in sweeps[-8:] if 'BULLISH' in sw.get('type', '')]
                    if bullish_sweeps:
                        sweep_ref = min(bullish_sweeps)

                crt_ref = poi_bottom
                if valid_crts:
                    bullish_crts = [c.get('bottom', poi_bottom) for c in valid_crts if c.get('type') == 'CRT_BULLISH']
                    if bullish_crts:
                        crt_ref = min(bullish_crts)

                invalidation_price = min(poi_bottom, swing_ref, sweep_ref, crt_ref)
                raw_sl_dist = (entry_target - invalidation_price) + buffer_dist
                sl_dist = max(min_sl_floor, min(max_sl_cap, raw_sl_dist))
                sl_target = entry_target - sl_dist

                # Dynamic Structural Take Profit
                tp1_dist = max(2.0 * sl_dist, partial_tp_dist)
                tp1_target = entry_target + tp1_dist

                tp2_dist = max(3.5 * sl_dist, min_tp_dist)
                if pd_zones and 'premium_high' in pd_zones and pd_zones['premium_high'] > entry_target + tp1_dist:
                    tp_target = min(entry_target + max_tp_dist, max(entry_target + tp2_dist, pd_zones['premium_high']))
                else:
                    tp_target = min(entry_target + max_tp_dist, entry_target + tp2_dist)

                # Opposing Obstacle Check (Do not buy directly into unmitigated supply wall)
                if valid_obs:
                    opposing_obs = [ob for ob in valid_obs if 'BEARISH' in ob.get('type', '') and not ob.get('mitigated', False) and ob['bottom'] > current_price]
                    if opposing_obs:
                        nearest_opp = min(opposing_obs, key=lambda x: x['bottom'])
                        if nearest_opp['bottom'] < entry_target + 1.5 * sl_dist:
                            return None # Insufficient clearance before supply obstacle

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
                for crt in valid_crts:
                    if crt['type'] == "CRT_BEARISH":
                        if crt['bottom'] >= current_price - 0.3 * atr and crt['top'] >= current_price:
                            candidate_pois.append(("CRT", crt, crt['bottom'], crt['top']))
                for snd in valid_snds:
                    if snd['type'] == "SUPPLY":
                        if snd['bottom'] >= current_price - 0.3 * atr and snd['top'] >= current_price:
                            candidate_pois.append(("SUPPLY", snd, snd['bottom'], snd['top']))
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
                    # Inside or at the edge of POI: Require fresh rejection candle or immediate sweep
                    has_fresh_sweep = bool(sweeps and sweeps[-1].get('type') == 'SWEEP_BEARISH' and (abs(current_price - sweeps[-1].get('level', current_price)) <= 0.6 * atr))
                    has_candle_rev = bool(reversal_patterns and any("BEARISH" in p for p in reversal_patterns))
                    has_confirmation = has_candle_rev or has_fresh_sweep
                    if has_confirmation:
                        exec_type = "CONFIRMED"
                        entry_target = current_price
                        reasons.append(f"Entry: Confirmed Reaction Inside Bearish {poi_type} ({poi_bottom:.2f} - {poi_top:.2f})")
                    else:
                        # Falling knife protection: Wait with limit order at POI 50% equilibrium
                        exec_type = "LIMIT"
                        entry_target = round((poi_top + poi_bottom) / 2.0, 2 if is_xau else 5)
                        reasons.append(f"Setup: Bearish {poi_type} Equilibrium Limit ({entry_target:.2f})")
                else:
                    # Approaching POI -> Pending Limit Order
                    exec_type = "LIMIT"
                    poi_height = poi_top - poi_bottom
                    if poi_height + buffer_dist > max_sl_cap:
                        entry_target = poi_top - (max_sl_cap - buffer_dist)
                    else:
                        entry_target = poi_bottom
                    reasons.append(f"Setup: Bearish {poi_type} Supply Zone ({poi_bottom:.2f} - {poi_top:.2f})")

                # Structural Swing SL calculation
                swing_ref = poi_top
                if engine_ltf and hasattr(engine_ltf, 'get_recent_swing'):
                    try:
                        swing_ref = engine_ltf.get_recent_swing(is_bullish=False, current_price=entry_target, atr=atr, lookback=30)
                    except Exception:
                        swing_ref = poi_top

                sweep_ref = poi_top
                if sweeps:
                    bearish_sweeps = [sw.get('level', poi_top) for sw in sweeps[-8:] if 'BEARISH' in sw.get('type', '')]
                    if bearish_sweeps:
                        sweep_ref = max(bearish_sweeps)

                crt_ref = poi_top
                if valid_crts:
                    bearish_crts = [c.get('top', poi_top) for c in valid_crts if c.get('type') == 'CRT_BEARISH']
                    if bearish_crts:
                        crt_ref = max(bearish_crts)

                invalidation_price = max(poi_top, swing_ref, sweep_ref, crt_ref)
                raw_sl_dist = (invalidation_price - entry_target) + buffer_dist
                sl_dist = max(min_sl_floor, min(max_sl_cap, raw_sl_dist))
                sl_target = entry_target + sl_dist

                # Dynamic Structural Take Profit
                tp1_dist = max(2.0 * sl_dist, partial_tp_dist)
                tp1_target = entry_target - tp1_dist

                tp2_dist = max(3.5 * sl_dist, min_tp_dist)
                if pd_zones and 'discount_low' in pd_zones and pd_zones['discount_low'] < entry_target - tp1_dist:
                    tp_target = max(entry_target - max_tp_dist, min(entry_target - tp2_dist, pd_zones['discount_low']))
                else:
                    tp_target = max(entry_target - max_tp_dist, entry_target - tp2_dist)

                # Opposing Obstacle Check (Do not sell directly into unmitigated demand wall)
                if valid_obs:
                    opposing_obs = [ob for ob in valid_obs if 'BULLISH' in ob.get('type', '') and not ob.get('mitigated', False) and ob['top'] < current_price]
                    if opposing_obs:
                        nearest_opp = max(opposing_obs, key=lambda x: x['top'])
                        if nearest_opp['top'] > entry_target - 1.5 * sl_dist:
                            return None # Insufficient clearance before demand obstacle

            # Point bonus for POI
            if poi_type in ["QM", "CRT"]:
                confluence_score += 3
                reasons.append(f"Institutional {poi_type} Key Level (+3)")
            elif poi_type in ["DEMAND", "SUPPLY"]:
                confluence_score += 2
                reasons.append(f"Institutional Supply & Demand ({poi_type}) Base (+2)")
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
            if rr_ratio < 1.8:
                return None  # Enforce minimum 1:1.8 R:R for mathematical edge

            # K. Grading Scale - STRICTLY GRADE A+ ONLY (Score >= 9)
            if confluence_score >= 9:
                setup_grade = "A+"
                risk_multiplier = 1.0
            else:
                # User Requirement: Reject any setup that is not Institutional Grade A+
                return None

            return {
                "symbol": symbol,
                "type": "BUY" if is_bullish else "SELL",
                "signal_type": exec_type,
                "entry": entry_target,
                "sl": sl_target,
                "tp": tp_target,
                "tp1": tp1_target,
                "tp2": tp_target,
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
        tp1_val = round(best.get('tp1', best['tp']), decimal_places)
        tp2_val = round(best.get('tp2', best['tp']), decimal_places)

        sl_pips = calculate_pips(symbol, entry_val, sl_val)
        lot_size = round(calculate_lot_size(acc_balance, final_risk_pct, sl_pips, symbol), 2)

        # Build final signal object
        ui_badge = "[UI_BADGE:ENTRY ZONE ACTIVE] Harga di zona, siap eksekusi." if best['signal_type'] == "CONFIRMED" else "[UI_BADGE:PENDING LIMIT ORDER] Pasang pending limit, tunggu jemputan."
        reasons_list = [ui_badge] + best['reasons']
        tp1_pips = calculate_pips(symbol, entry_val, tp1_val)
        tp2_pips = calculate_pips(symbol, entry_val, tp2_val)
        reasons_list.append(f"TP1 (+{tp1_pips:.0f}p): {tp1_val} (Amankan 50% Lot & SL ke BE) | TP2 (+{tp2_pips:.0f}p): {tp2_val} (Runner)")
        reasons_list.append(f"SMC Grade: {best['grade']} (Confluence Score: {best['score']}/10, R:R: 1:{best['rr_ratio']})")
        reasons_list.append(f"Session: {kz_reason}")

        signal = {
            "symbol": symbol,
            "type": best['type'],
            "signal_type": best['signal_type'],
            "timestamp": now_time.isoformat() if current_time_str else datetime.now().isoformat(),
            "entry": entry_val,
            "sl": sl_val,
            "tp": tp_val,
            "tp1": tp1_val,
            "tp2": tp2_val,
            "partial_tp_pips": req_partial_tp_pips,
            "lot_size": lot_size,
            "reasons": reasons_list,
            "status": "PENDING",
            "grade": best['grade'],
            "atr": round(atr, 2),
            "poi_signature": best['poi_signature'],
            "rr_ratio": best['rr_ratio']
        }

        print(f"\n{'='*55}\n[SMC ENGINE] Valid Setup Found for {symbol}!\nType: {signal['type']} ({signal['signal_type']}) | Grade: {signal['grade']} (Score: {best['score']})\nEntry: {signal['entry']} | SL: {signal['sl']} | TP1: {signal['tp1']} (+{tp1_pips:.0f}p) | TP2: {signal['tp']} (+{tp2_pips:.0f}p)\n{'='*55}\n")

        self.active_signals[symbol] = signal
        self.cooldowns[symbol] = now_time + timedelta(minutes=self.cooldown_minutes)
        return signal
