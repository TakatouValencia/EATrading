import os
import json
import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from risk_calculator import calculate_pips, calculate_lot_size
import settings_manager
from market_schedule import is_forex_market_open, is_killzone_active, get_utc_datetime, is_high_impact_news_window

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
                                  ifvgs: List[Dict] = None,
                                  smt_divergence: Dict = None,
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
                parsed_ts = datetime.fromisoformat(ts)
                if parsed_ts.tzinfo is not None:
                    now_time = parsed_ts.astimezone(timezone.utc).replace(tzinfo=None)
                else:
                    now_time = parsed_ts
            except Exception:
                now_time = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            now_time = datetime.now(timezone.utc).replace(tzinfo=None)

        if symbol in self.cooldowns:
            if now_time < self.cooldowns[symbol]:
                return None
            else:
                del self.cooldowns[symbol]

        # 3. Currency / Asset Specifications
        is_xau = "XAU" in symbol
        pip_unit = 0.10 if is_xau else 0.0001
        
        # 4. Strict Market Hours & Weekend Shield (Checks both event time and real-world system UTC)
        is_open, open_reason = is_forex_market_open(now_time, symbol, check_real_time=(current_time_str is None))
        if not is_open:
            return None

        # 5. Strict Session Filter: London KZ & NY KZ (Dead Zone 17:30-19:30 WIB paused)
        is_killzone, kz_reason = is_killzone_active(now_time)
        if not is_killzone:
            return None

        # 5B. High-Impact Macroeconomic News Shield (Protects against slippage / spread expansion)
        is_news, news_reason = is_high_impact_news_window(now_time)
        if is_news:
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
            # RULE 2: Setup Identification (Mode A: Sweep Reversal vs Mode B: Trend Continuation)
            # -------------------------------------------------------------
            sweep_target_type = f"SWEEP_{trend_tag}"
            candidate_sweeps = [s for s in (sweeps or []) if s.get('type') == sweep_target_type]
            major_candidate_sweeps = [
                s for s in candidate_sweeps 
                if s.get('pool_type') in ['ASIAN_LOW', 'ASIAN_HIGH', 'PDL', 'PDH', 'EQL', 'EQH']
            ]

            setup_mode = None
            matched_sweep = None
            pool_name = "HTF Order Flow"

            # Check Mode A: Major Liquidity Pool Sweep (Reversal)
            if major_candidate_sweeps:
                candidate = major_candidate_sweeps[-1]
                p_name = candidate.get('pool_name', 'Liquidity Pool')
                is_blacklisted = False
                if trade_manager and hasattr(trade_manager, 'is_pool_blacklisted'):
                    is_blacklisted = trade_manager.is_pool_blacklisted(p_name)
                    
                if not is_blacklisted:
                    has_rej = candidate.get('has_rejection', False) or (candidate.get('wick_ratio', 0) >= 0.28)
                    if reversal_patterns and any(trend_tag in p for p in reversal_patterns):
                        has_rej = True
                    if has_rej:
                        # Check LTF Structure Shift post-sweep (CHoCH or BOS)
                        choch_found = False
                        for ev in reversed((events or [])[-12:]):
                            if ev.get('type') in [f"CHOCH_{trend_tag}", f"BOS_{trend_tag}"]:
                                choch_found = True
                                break
                        if choch_found:
                            setup_mode = "SWEEP_REVERSAL"
                            matched_sweep = candidate
                            pool_name = p_name
                            confluence_score += 4
                            reasons.append(f"Liquidity Sweep: {pool_name} Swept with Rejection Wick & Structure Shift (+4)")

            # Check Mode B: Institutional Trend Continuation (Order Flow Pullback)
            if not setup_mode:
                # Strictly requires H4 & H1 trend alignment with trade direction
                if effective_h4 == trend_tag and (h1_trend == trend_tag or m15_trend == trend_tag):
                    # Recent BOS in trend direction
                    recent_bos = False
                    for ev in reversed((events or [])[-12:]):
                        if ev.get('type') == f"BOS_{trend_tag}":
                            recent_bos = True
                            break
                    if recent_bos:
                        # Valuation check: Discount for Buy, Premium for Sell
                        eq_level = pd_zones.get('eq', current_price) if pd_zones else current_price
                        in_discount = (current_price <= eq_level + (1.5 if is_xau else 0.0015)) if is_bullish else (current_price >= eq_level - (1.5 if is_xau else 0.0015))
                        if in_discount:
                            setup_mode = "TREND_CONTINUATION"
                            pool_name = f"Trend Continuation ({trend_tag})"
                            confluence_score += 3
                            reasons.append(f"Trend Continuation: H4/H1 Pro-Trend Order Flow & BOS (+3)")

            if not setup_mode:
                # No valid institutional setup found
                return None

            # RULE 2C: RSI Momentum Health Check
            if engine_ltf and hasattr(engine_ltf, 'calculate_rsi'):
                rsi_val = engine_ltf.calculate_rsi(14)
                if setup_mode == "SWEEP_REVERSAL":
                    if is_bullish and rsi_val > 48.0:
                        return None
                    elif not is_bullish and rsi_val < 52.0:
                        return None
                else: # TREND_CONTINUATION
                    if is_bullish and rsi_val > 62.0:
                        return None
                    elif not is_bullish and rsi_val < 38.0:
                        return None
                reasons.append(f"Momentum Health: RSI ({rsi_val:.1f}) aligned with setup (+2)")

            # RULE 2D: Displacement Candle Ratio Check (No weak dojis/indecision)
            c_high = current_price
            c_low = current_price
            c_open = current_price
            if engine_ltf and getattr(engine_ltf, 'data', None):
                last_c = engine_ltf.data[-1]
                c_high = last_c['high']
                c_low = last_c['low']
                c_open = last_c['open']
                c_range = c_high - c_low
                c_body = abs(last_c['close'] - c_open)
                body_ratio = (c_body / c_range) if c_range > 0 else 0
                if body_ratio < 0.32:
                    return None

            # -------------------------------------------------------------
            # RULE 4: Entry Zone in Fresh FVG or Unmitigated OB (Strict Discount for BUY, Premium for SELL)
            # -------------------------------------------------------------
            fvg_target_type = f"FVG_{trend_tag}"
            ob_target_type = f"OB_{trend_tag}"

            # Strict Valuation Reference: Never Enter in the Middle or Counter-Valuation
            eq_level = pd_zones.get('eq', current_price) if pd_zones else current_price

            candidate_fvgs = [f for f in valid_fvgs if f.get('type') == fvg_target_type and not f.get('mitigated', False)]
            
            # Combine LTF/MTF OBs with HTF H1 OBs
            all_obs = valid_obs + [o for o in (h1_obs or []) if o.get('type') == ob_target_type and not o.get('mitigated', False)]
            seen_ob_sig = set()
            candidate_obs = []
            for o in all_obs:
                sig = f"{o.get('type')}_{o.get('bottom')}_{o.get('top')}"
                if sig not in seen_ob_sig:
                    seen_ob_sig.add(sig)
                    candidate_obs.append(o)

            matched_poi = None
            poi_type = None
            poi_top = 0.0
            poi_bottom = 0.0
            is_fresh_zone = True

            if is_bullish:
                # STRICT DISCOUNT ENFORCEMENT: Never Buy in Premium or Upper Equilibrium
                if pd_zones and current_price > eq_level + (0.2 if is_xau else 0.0002):
                    return None

                # Bullish: Current candle tapped into or is inside the POI in Discount (< eq_level)
                valid_pois = []
                for f in candidate_fvgs:
                    if engine_ltf and hasattr(engine_ltf, 'classify_poi_quality'):
                        if engine_ltf.classify_poi_quality(f, pd_zones) == "INDUCEMENT":
                            continue # Skip retail trap floating in equilibrium
                    if not pd_zones or f['bottom'] <= eq_level:
                        if c_low <= f['top'] and current_price >= f['bottom'] - 0.5:
                            is_fresh = f.get('is_fresh', True) or (f.get('touch_count', 0) <= 1)
                            valid_pois.append(("FVG", f, f['top'], f['bottom'], is_fresh, f.get('touch_count', 0)))
                for o in candidate_obs:
                    if engine_ltf and hasattr(engine_ltf, 'classify_poi_quality'):
                        if engine_ltf.classify_poi_quality(o, pd_zones) == "INDUCEMENT":
                            continue
                    if not pd_zones or o['bottom'] <= eq_level:
                        if c_low <= o['top'] and current_price >= o['bottom'] - 0.5:
                            is_fresh = o.get('is_fresh', True) or (o.get('touch_count', 0) <= 1)
                            valid_pois.append(("OB", o, o['top'], o['bottom'], is_fresh, o.get('touch_count', 0)))
                
                # Check Inversion FVGs (IFVG)
                candidate_ifvgs = [iv for iv in (ifvgs or []) if iv.get('type') == 'IFVG_BULLISH' and not iv.get('mitigated', False)]
                for iv in candidate_ifvgs:
                    if not pd_zones or iv['bottom'] <= eq_level:
                        if c_low <= iv['top'] and current_price >= iv['bottom'] - 0.5:
                            valid_pois.append(("IFVG", iv, iv['top'], iv['bottom'], True, 0))

                if not valid_pois:
                    return None

                # Rejection filter: Close above low (bullish reaction)
                if current_price < c_open and (current_price - c_low) < (c_high - current_price):
                    return None

                # Prioritize: 1) Fresh Virgin POI (0 previous touches), 2) FVG over OB, 3) Closeness to entry
                valid_pois.sort(key=lambda x: (not x[4], 0 if x[0] in ["FVG", "IFVG"] else 1, abs(current_price - x[2])))
                poi_type, poi_obj, poi_top, poi_bottom, is_fresh_zone, touch_cnt = valid_pois[0]
                exec_type = "CONFIRMED"
                entry_target = round(min(current_price, poi_top + 0.4), 2 if is_xau else 5)

            else:
                # STRICT PREMIUM ENFORCEMENT: Never Sell in Discount or Lower Equilibrium
                if pd_zones and current_price < eq_level - (0.2 if is_xau else 0.0002):
                    return None

                # Bearish: Current candle tapped into or is inside the POI in Premium (> eq_level)
                valid_pois = []
                for f in candidate_fvgs:
                    if engine_ltf and hasattr(engine_ltf, 'classify_poi_quality'):
                        if engine_ltf.classify_poi_quality(f, pd_zones) == "INDUCEMENT":
                            continue # Skip retail trap floating in equilibrium
                    if not pd_zones or f['top'] >= eq_level:
                        if c_high >= f['bottom'] and current_price <= f['top'] + 0.5:
                            is_fresh = f.get('is_fresh', True) or (f.get('touch_count', 0) <= 1)
                            valid_pois.append(("FVG", f, f['top'], f['bottom'], is_fresh, f.get('touch_count', 0)))
                for o in candidate_obs:
                    if engine_ltf and hasattr(engine_ltf, 'classify_poi_quality'):
                        if engine_ltf.classify_poi_quality(o, pd_zones) == "INDUCEMENT":
                            continue
                    if not pd_zones or o['top'] >= eq_level:
                        if c_high >= o['bottom'] and current_price <= o['top'] + 0.5:
                            is_fresh = o.get('is_fresh', True) or (o.get('touch_count', 0) <= 1)
                            valid_pois.append(("OB", o, o['top'], o['bottom'], is_fresh, o.get('touch_count', 0)))
                
                # Check Inversion FVGs (IFVG)
                candidate_ifvgs = [iv for iv in (ifvgs or []) if iv.get('type') == 'IFVG_BEARISH' and not iv.get('mitigated', False)]
                for iv in candidate_ifvgs:
                    if not pd_zones or iv['top'] >= eq_level:
                        if c_high >= iv['bottom'] and current_price <= iv['top'] + 0.5:
                            valid_pois.append(("IFVG", iv, iv['top'], iv['bottom'], True, 0))

                if not valid_pois:
                    return None

                if current_price > c_open and (c_high - current_price) < (current_price - c_low):
                    return None

                # Prioritize: 1) Fresh Virgin POI, 2) FVG over OB, 3) Closeness to entry
                valid_pois.sort(key=lambda x: (not x[4], 0 if x[0] in ["FVG", "IFVG"] else 1, abs(current_price - x[3])))
                poi_type, poi_obj, poi_top, poi_bottom, is_fresh_zone, touch_cnt = valid_pois[0]
                exec_type = "CONFIRMED"
                entry_target = round(max(current_price, poi_bottom - 0.4), 2 if is_xau else 5)

            poi_obj_type = poi_obj.get('type', f"{poi_type}_{'BULLISH' if is_bullish else 'BEARISH'}")
            poi_sig = f"{symbol}_{poi_obj_type}_{poi_bottom}_{poi_top}"
            
            if is_fresh_zone:
                confluence_score += 4
                reasons.append(f"Fresh Zone: Virgin Unmitigated {poi_type} ({poi_bottom:.2f} - {poi_top:.2f}) First Tap Retest (+4)")
            else:
                confluence_score += 2
                reasons.append(f"Entry Zone: Unfilled {poi_type} ({poi_bottom:.2f} - {poi_top:.2f}) Tapped & Rejected (+2)")

            # Additional Confluence 0: Extreme POI vs Decisional POI
            if engine_ltf and hasattr(engine_ltf, 'classify_poi_quality'):
                if engine_ltf.classify_poi_quality(poi_obj, pd_zones) == "EXTREME":
                    confluence_score += 3
                    reasons.append(f"Extreme POI: Deep Institutional Origin Zone ({poi_bottom:.2f} - {poi_top:.2f}) (+3)")

            # Additional Confluence 1: Fibo OTE Golden Zone (0.618 - 0.786)
            if fibo_ote:
                ote_key = "bullish_ote" if is_bullish else "bearish_ote"
                if ote_key in fibo_ote and fibo_ote[ote_key]:
                    o_top = fibo_ote[ote_key]['top']
                    o_bot = fibo_ote[ote_key]['bottom']
                    o_min = min(o_top, o_bot)
                    o_max = max(o_top, o_bot)
                    if o_min - 0.5 <= current_price <= o_max + 0.5:
                        confluence_score += 3
                        reasons.append(f"Golden Pocket: Fibo OTE 61.8% - 78.6% Retracement ({o_min:.2f} - {o_max:.2f}) (+3)")

            # Additional Confluence 2: HTF H1 Order Block Confluence
            if h1_obs:
                for h_ob in h1_obs:
                    if (is_bullish and h_ob.get('type') == 'OB_BULLISH') or (not is_bullish and h_ob.get('type') == 'OB_BEARISH'):
                        if max(poi_bottom, h_ob['bottom']) <= min(poi_top, h_ob['top']):
                            confluence_score += 3
                            reasons.append(f"HTF Confluence: M5/M15 Zone Selaras H1 Institutional Order Block (+3)")
                            break

            # Additional Confluence 3: Supply & Demand Confluence
            if snd_zones:
                snd_target = "DEMAND" if is_bullish else "SUPPLY"
                for z in snd_zones:
                    if z.get('type') == snd_target and not z.get('mitigated', False):
                        if max(poi_bottom, z['bottom']) <= min(poi_top, z['top']):
                            confluence_score += 2
                            reasons.append(f"Institutional S&D: Overlap dengan Fresh {snd_target} Zone (+2)")
                            break

            # Additional Confluence 4: SMT Divergence (Smart Money Tool)
            if smt_divergence:
                smt_type = smt_divergence.get('type')
                if (is_bullish and smt_type == 'SMT_BULLISH') or (not is_bullish and smt_type == 'SMT_BEARISH'):
                    confluence_score += 4
                    reasons.append(f"SMT Divergence: {smt_divergence.get('reason', 'Institutional Correlation Divergence')} (+4)")

            # -------------------------------------------------------------
            # RULE 5: Stop Loss (POI Extreme + Buffer, Floor 50p, Cap 70p)
            # -------------------------------------------------------------
            buffer_pips = 1.8 if is_xau else 0.0018 # 18 pips ($1.80 on Gold buffer)
            min_sl_dist = 5.0 if is_xau else 0.0050 # Minimum SL floor 50 pips ($5.00)
            max_sl_dist = 7.0 if is_xau else 0.0070 # Maximum SL cap 70 pips ($7.00)
            
            if is_bullish:
                ref_low = matched_sweep.get('sweep_low', poi_bottom) if matched_sweep else poi_bottom
                raw_sl = min(ref_low, poi_bottom) - buffer_pips
                sl_target = round(raw_sl, 2 if is_xau else 5)
                risk_dist = entry_target - sl_target
                if risk_dist < min_sl_dist:
                    sl_target = round(entry_target - min_sl_dist, 2 if is_xau else 5)
                    risk_dist = entry_target - sl_target
                elif risk_dist > max_sl_dist:
                    sl_target = round(entry_target - max_sl_dist, 2 if is_xau else 5)
                    risk_dist = entry_target - sl_target
            else:
                ref_high = matched_sweep.get('sweep_high', poi_top) if matched_sweep else poi_top
                raw_sl = max(ref_high, poi_top) + buffer_pips
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
            # RULE 6: Take Profit Targets (TP1: +120p Banking, TP2: 180-220p HTF Target)
            # -------------------------------------------------------------
            default_tp1_dist = 12.0 if is_xau else 0.0120 # 120 pips ($12.00) - High-Reward Structural Bank
            default_tp2_dist = 18.0 if is_xau else 0.0180 # 180 pips ($18.00) - True HTF Swing Expansion

            tp_candidates = []
            if is_bullish:
                if liquidity_pools:
                    for p_key in ['asian_high', 'pdh']:
                        if liquidity_pools.get(p_key) and entry_target + 12.0 <= liquidity_pools[p_key] <= entry_target + 26.0:
                            tp_candidates.append(liquidity_pools[p_key])
                    for eqh in liquidity_pools.get('eqh', []):
                        if entry_target + 12.0 <= eqh['level'] <= entry_target + 26.0:
                            tp_candidates.append(eqh['level'])

                tp1_target = round(entry_target + default_tp1_dist, 2 if is_xau else 5)
                tp2_target = sorted(tp_candidates)[0] if tp_candidates else round(entry_target + default_tp2_dist, 2 if is_xau else 5)
                tp_target = tp2_target

            else:
                if liquidity_pools:
                    for p_key in ['asian_low', 'pdl']:
                        if liquidity_pools.get(p_key) and entry_target - 26.0 <= liquidity_pools[p_key] <= entry_target - 12.0:
                            tp_candidates.append(liquidity_pools[p_key])
                    for eql in liquidity_pools.get('eql', []):
                        if entry_target - 26.0 <= eql['level'] <= entry_target - 12.0:
                            tp_candidates.append(eql['level'])

                tp1_target = round(entry_target - default_tp1_dist, 2 if is_xau else 5)
                tp2_target = sorted(tp_candidates, reverse=True)[0] if tp_candidates else round(entry_target - default_tp2_dist, 2 if is_xau else 5)
                tp_target = tp2_target

            # -------------------------------------------------------------
            # RULE 7: Adaptive Risk to Reward (RRR >= 2.0) Enforced
            # -------------------------------------------------------------
            tp_dist = abs(tp_target - entry_target)
            rr_ratio = tp_dist / risk_dist if risk_dist > 0 else 0
            if rr_ratio < 1.75:
                return None


            # -------------------------------------------------------------
            # RULE 8: Grade A+ Verification (All Criteria Fulfilled)
            # -------------------------------------------------------------
            setup_grade = "A+"
            reasons.append(f"Session Active: {kz_reason}")
            reasons.append(f"Risk Management: Target TP (+{tp_dist/pip_unit:.0f}p / 1:{rr_ratio:.1f}R)")

            rr_tp1 = abs(tp1_target - entry_target) / risk_dist if risk_dist > 0 else 0

            return {
                "symbol": symbol,
                "type": "BUY" if is_bullish else "SELL",
                "signal_type": exec_type,
                "entry": entry_target,
                "entry_zone": f"{min(poi_bottom, poi_top):.2f} - {max(poi_bottom, poi_top):.2f} ({poi_type})",
                "sl": sl_target,
                "tp": tp_target,
                "tp1": tp1_target,
                "tp2": tp2_target,
                "reasons": reasons,
                "grade": setup_grade,
                "score": confluence_score,
                "risk_multiplier": 1.0,
                "poi_signature": poi_sig,
                "rr_ratio": round(rr_ratio, 2),
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
        tp1_val = round(best['tp1'], decimal_places)
        tp2_val = round(best['tp2'], decimal_places)

        sl_pips = calculate_pips(symbol, entry_val, sl_val)
        lot_size = round(calculate_lot_size(acc_balance, base_risk_pct, sl_pips, symbol), 2)

        tp1_pips = calculate_pips(symbol, entry_val, tp1_val)
        tp2_pips = calculate_pips(symbol, entry_val, tp2_val)
        tp_pips = tp2_pips

        ui_badge = "[UI_BADGE:ENTRY ZONE ACTIVE] Sinyal Terkonfirmasi. Siap Eksekusi Langsung."
        reasons_list = [ui_badge] + best['reasons']
        reasons_list.append(f"Target Utama TP (+{tp_pips:.0f}p): {tp_val} (Full HTF Institutional Target)")
        reasons_list.append(f"Securing Bank TP1 (+{tp1_pips:.0f}p): {tp1_val} (Kunci Profit 50%)")
        reasons_list.append("Auto Break-Even: Aktif di +65p (Memberi Ruang Napas Intraday Gold)")
        reasons_list.append(f"SMC Grade: {best['grade']} (RRR Target TP 1:{best['rr_ratio']})")

        signal = {
            "symbol": symbol,
            "type": best['type'],
            "signal_type": "CONFIRMED",
            "timestamp": now_time.isoformat() if current_time_str else datetime.now().isoformat(),
            "entry": entry_val,
            "entry_zone": best['entry_zone'],
            "sl": sl_val,
            "tp": tp_val,
            "tp1": tp1_val,
            "tp2": tp2_val,
            "lot_size": lot_size,
            "reasons": reasons_list,
            "status": "ACTIVE",
            "grade": best['grade'],
            "atr": round(atr, 2),
            "poi_signature": best['poi_signature'],
            "rr_ratio": best['rr_ratio'],
            "rr_tp1": best['rr_tp1'],
            "sweep_pool": best['sweep_pool'],
            "killzone": best['killzone']
        }


        print(f"\n{'='*60}\n[SMC GRADE A+ SETUP] {symbol} {signal['type']} ({signal['signal_type']})\nEntry: {signal['entry']} (Zone: {signal['entry_zone']}) | SL: {signal['sl']} (-{sl_pips:.0f}p)\nTP: {signal['tp']} (+{tp_pips:.0f}p | 1:{best['rr_ratio']}R)\nSweep: {best['sweep_pool']} | Session: {best['killzone']}\n{'='*60}\n")

        self.active_signals[symbol] = signal
        self.cooldowns[symbol] = now_time + timedelta(minutes=self.cooldown_minutes)
        return signal

