from typing import Dict, List, Optional

class SMCEngine:
    def __init__(self, data: List[Dict], swing_length: int = 2):
        """
        Initialize the SMC Engine with historical OHLCV data.
        data must be a list of dictionaries with keys: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        swing_length: Number of candles to the left and right to determine a swing point.
        """
        self.data = list(data)  # Make a copy
        self.swing_length = swing_length
        self._calculate_swings()
    
    def _calculate_swings(self):
        """Calculate Swing Highs and Swing Lows based on the swing_length."""
        for row in self.data:
            row['swing_high'] = False
            row['swing_low'] = False
            
        # We need at least 2 * swing_length + 1 candles to determine a swing
        for i in range(self.swing_length, len(self.data) - self.swing_length):
            # Window of highs
            highs = [self.data[j]['high'] for j in range(i - self.swing_length, i + self.swing_length + 1)]
            if self.data[i]['high'] == max(highs):
                self.data[i]['swing_high'] = True
                
            # Window of lows
            lows = [self.data[j]['low'] for j in range(i - self.swing_length, i + self.swing_length + 1)]
            if self.data[i]['low'] == min(lows):
                self.data[i]['swing_low'] = True

    def detect_bos_choch(self) -> List[Dict]:
        """
        Detect Break of Structure (BOS) and Change of Character (CHoCH).
        Returns a list of dictionaries with structure events.
        """
        events = []
        last_swing_high_idx = None
        last_swing_low_idx = None
        
        trend = 0  # 1 for uptrend, -1 for downtrend
        
        for i, row in enumerate(self.data):
            if row['swing_high']:
                last_swing_high_idx = i
            if row['swing_low']:
                last_swing_low_idx = i
                
            # Need both swings to determine breaks
            if last_swing_high_idx is None or last_swing_low_idx is None:
                continue
                
            current_close = row['close']
            
            # --- Volume Analysis ---
            avg_vol = 0
            count = 0
            for j in range(max(0, i-10), i):
                avg_vol += self.data[j].get('volume', 0)
                count += 1
            avg_vol = (avg_vol / count) if count > 0 else 0
            
            current_vol = row.get('volume', 0)
            is_fakeout = current_vol < (avg_vol * 1.1) if avg_vol > 0 else False
            
            # Check Bullish Break (close above last swing high)
            if last_swing_high_idx < i and current_close > self.data[last_swing_high_idx]['high']:
                if trend == 1:
                    event_type = "BOS_BULLISH"
                else:
                    event_type = "CHOCH_BULLISH"
                    trend = 1
                
                events.append({
                    "type": event_type,
                    "index": i,
                    "timestamp": row['timestamp'],
                    "level": self.data[last_swing_high_idx]['high'],
                    "broken_swing_idx": last_swing_high_idx,
                    "is_fakeout": is_fakeout
                })
                # Reset to avoid multiple triggers for the same swing
                last_swing_high_idx = None 
                
            # Check Bearish Break (close below last swing low)
            elif last_swing_low_idx < i and current_close < self.data[last_swing_low_idx]['low']:
                if trend == -1:
                    event_type = "BOS_BEARISH"
                else:
                    event_type = "CHOCH_BEARISH"
                    trend = -1
                    
                events.append({
                    "type": event_type,
                    "index": i,
                    "timestamp": row['timestamp'],
                    "level": self.data[last_swing_low_idx]['low'],
                    "broken_swing_idx": last_swing_low_idx,
                    "is_fakeout": is_fakeout
                })
                # Reset
                last_swing_low_idx = None
                
        return events

    def detect_fvg(self) -> List[Dict]:
        """
        Detect Fair Value Gaps (FVG).
        """
        fvgs = []
        
        for i in range(2, len(self.data)):
            candle1 = self.data[i-2]
            candle2 = self.data[i-1] # The large body candle
            candle3 = self.data[i]
            
            # Bullish FVG: candle1.high < candle3.low
            if candle1['high'] < candle3['low']:
                fvgs.append({
                    "type": "FVG_BULLISH",
                    "top": candle3['low'],
                    "bottom": candle1['high'],
                    "timestamp": candle2['timestamp'],
                    "index": i-1,
                    "mitigated": False
                })
                
            # Bearish FVG: candle1.low > candle3.high
            elif candle1['low'] > candle3['high']:
                fvgs.append({
                    "type": "FVG_BEARISH",
                    "top": candle1['low'],
                    "bottom": candle3['high'],
                    "timestamp": candle2['timestamp'],
                    "index": i-1,
                    "mitigated": False
                })
                
        # Strict mitigation check: If price even taps the FVG, consider it mitigated so we don't trade stale setups
        for fvg in fvgs:
            idx = fvg['index']
            for j in range(idx + 1, len(self.data)):
                if fvg['type'] == 'FVG_BULLISH' and self.data[j]['low'] <= fvg['top']:
                    fvg['mitigated'] = True
                    break
                elif fvg['type'] == 'FVG_BEARISH' and self.data[j]['high'] >= fvg['bottom']:
                    fvg['mitigated'] = True
                    break
                    
        # Return only fresh, unmitigated FVGs
        return [f for f in fvgs if not f['mitigated']]

    def detect_order_blocks(self, structure_events: List[Dict]) -> List[Dict]:
        """
        Detect Order Blocks based on structural breaks (BOS/CHoCH).
        Bullish OB: Last bearish candle before the impulsive move breaking structure.
        Bearish OB: Last bullish candle before the impulsive move breaking structure.
        """
        obs = []
        
        for event in structure_events:
            break_idx = event['index']
            swing_idx = event['broken_swing_idx']
            
            if "BULLISH" in event['type']:
                # Find the lowest point before the break
                search_start = max(0, swing_idx - 10)
                
                # Find index of min low
                low_idx = search_start
                min_low = self.data[search_start]['low']
                for j in range(search_start, break_idx):
                    if self.data[j]['low'] < min_low:
                        min_low = self.data[j]['low']
                        low_idx = j
                
                ob_candle = None
                for j in range(low_idx, -1, -1):
                    if self.data[j]['close'] < self.data[j]['open']: # Bearish candle
                        ob_candle = j
                        break
                
                if ob_candle is not None:
                    obs.append({
                        "type": "OB_BULLISH",
                        "top": self.data[ob_candle]['open'], 
                        "bottom": self.data[ob_candle]['low'],
                        "timestamp": self.data[ob_candle]['timestamp'],
                        "index": ob_candle,
                        "mitigated": False
                    })
                    
            elif "BEARISH" in event['type']:
                search_start = max(0, swing_idx - 10)
                
                high_idx = search_start
                max_high = self.data[search_start]['high']
                for j in range(search_start, break_idx):
                    if self.data[j]['high'] > max_high:
                        max_high = self.data[j]['high']
                        high_idx = j
                
                ob_candle = None
                for j in range(high_idx, -1, -1):
                    if self.data[j]['close'] > self.data[j]['open']: # Bullish candle
                        ob_candle = j
                        break
                        
                if ob_candle is not None:
                    obs.append({
                        "type": "OB_BEARISH",
                        "top": self.data[ob_candle]['high'],
                        "bottom": self.data[ob_candle]['open'], 
                        "timestamp": self.data[ob_candle]['timestamp'],
                        "index": ob_candle,
                        "mitigated": False
                    })
                    
        # Mitigation check for Order Blocks
        for ob in obs:
            idx = ob['index']
            for j in range(idx + 1, len(self.data)):
                # If price comes back and taps the OB, it's mitigated
                if ob['type'] == 'OB_BULLISH' and self.data[j]['low'] <= ob['top']:
                    ob['mitigated'] = True
                    break
                elif ob['type'] == 'OB_BEARISH' and self.data[j]['high'] >= ob['bottom']:
                    ob['mitigated'] = True
                    break
                    
        # Only return fresh, unmitigated Order Blocks
        return [ob for ob in obs if not ob['mitigated']]

    def detect_liquidity_sweeps(self) -> List[Dict]:
        sweeps = []
        last_swing_high_idx = None
        last_swing_low_idx = None
        
        for i, row in enumerate(self.data):
            if row['swing_high']:
                last_swing_high_idx = i
            if row['swing_low']:
                last_swing_low_idx = i
                
            if last_swing_high_idx is None or last_swing_low_idx is None:
                continue
                
            current_high = row['high']
            current_low = row['low']
            current_close = row['close']
            
            # Bearish Sweep: price goes above last swing high, but closes below it
            if last_swing_high_idx < i and current_high > self.data[last_swing_high_idx]['high'] and current_close < self.data[last_swing_high_idx]['high']:
                # If the swept swing was formed recently (e.g. < 15 candles ago), we classify it as an Inducement (IDM) sweep.
                is_idm = (i - last_swing_high_idx) <= 15
                
                sweeps.append({
                    "type": "SWEEP_BEARISH",
                    "index": i,
                    "timestamp": row['timestamp'],
                    "level": self.data[last_swing_high_idx]['high'],
                    "swept_swing_idx": last_swing_high_idx,
                    "is_idm": is_idm
                })
                # Reset to avoid multiple triggers for the same swing
                last_swing_high_idx = None
                
            # Bullish Sweep: price goes below last swing low, but closes above it
            elif last_swing_low_idx < i and current_low < self.data[last_swing_low_idx]['low'] and current_close > self.data[last_swing_low_idx]['low']:
                is_idm = (i - last_swing_low_idx) <= 15
                
                sweeps.append({
                    "type": "SWEEP_BULLISH",
                    "index": i,
                    "timestamp": row['timestamp'],
                    "level": self.data[last_swing_low_idx]['low'],
                    "swept_swing_idx": last_swing_low_idx,
                    "is_idm": is_idm
                })
                # Reset
                last_swing_low_idx = None
                
        return sweeps

    def detect_support_resistance(self, threshold_pct: float = 0.001) -> List[Dict]:
        """
        Detect Support and Resistance zones based on multiple touches of swing highs/lows.
        threshold_pct: max percentage difference between swing points to group them into the same zone.
        """
        snr_zones = []
        
        # Group swing highs (Resistance)
        highs = [row['high'] for row in self.data if row['swing_high']]
        lows = [row['low'] for row in self.data if row['swing_low']]
        
        # Simple clustering for Resistance
        visited_highs = set()
        for i, h1 in enumerate(highs):
            if i in visited_highs: continue
            cluster = [h1]
            visited_highs.add(i)
            for j, h2 in enumerate(highs):
                if j in visited_highs: continue
                if abs(h1 - h2) / h1 <= threshold_pct:
                    cluster.append(h2)
                    visited_highs.add(j)
            
            if len(cluster) >= 2:
                snr_zones.append({
                    "type": "RESISTANCE",
                    "level": sum(cluster) / len(cluster), # Average level
                    "touches": len(cluster),
                    "strength": "STRONG" if len(cluster) >= 3 else "MEDIUM",
                    "is_mnsr": len(cluster) >= 4 # Major Resistance
                })
                
        # Simple clustering for Support
        visited_lows = set()
        for i, l1 in enumerate(lows):
            if i in visited_lows: continue
            cluster = [l1]
            visited_lows.add(i)
            for j, l2 in enumerate(lows):
                if j in visited_lows: continue
                if abs(l1 - l2) / l1 <= threshold_pct:
                    cluster.append(l2)
                    visited_lows.add(j)
                    
            if len(cluster) >= 2:
                snr_zones.append({
                    "type": "SUPPORT",
                    "level": sum(cluster) / len(cluster),
                    "touches": len(cluster),
                    "strength": "STRONG" if len(cluster) >= 3 else "MEDIUM",
                    "is_mnsr": len(cluster) >= 4 # Major Support
                })
                
        return snr_zones

    def detect_supply_demand(self) -> List[Dict]:
        """
        Detect Supply and Demand zones based on Rally-Base-Drop, Drop-Base-Rally, etc.
        """
        snd_zones = []
        
        for i in range(2, len(self.data) - 1):
            c1 = self.data[i-2]
            c2 = self.data[i-1] # Base candle
            c3 = self.data[i]   # Momentum candle
            
            # Helper to determine candle body size and direction
            def get_body(c): return abs(c['close'] - c['open'])
            def is_bullish(c): return c['close'] > c['open']
            def is_bearish(c): return c['close'] < c['open']
            
            # Calculate average body over the last 10 candles
            avg_body = 0
            count = 0
            for j in range(max(0, i-10), i):
                avg_body += get_body(self.data[j])
                count += 1
            if count > 0:
                avg_body /= count
            else:
                avg_body = get_body(c2)
            
            # Base candle shouldn't be zero to avoid div by zero issues, but it can be small
            if avg_body == 0:
                avg_body = 0.0001
                
            # Supply: Small base followed by a strong bearish candle
            if get_body(c2) < avg_body * 0.8 and get_body(c3) > avg_body * 1.5 and is_bearish(c3):
                snd_zones.append({
                    "type": "SUPPLY",
                    "top": c2['high'],
                    "bottom": c2['low'],
                    "timestamp": c2['timestamp'],
                    "index": i-1,
                    "mitigated": False
                })
                
            # Demand: Small base followed by a strong bullish candle
            elif get_body(c2) < avg_body * 0.8 and get_body(c3) > avg_body * 1.5 and is_bullish(c3):
                snd_zones.append({
                    "type": "DEMAND",
                    "top": c2['high'],
                    "bottom": c2['low'],
                    "timestamp": c2['timestamp'],
                    "index": i-1,
                    "mitigated": False
                })
                
        # Mitigation check
        for zone in snd_zones:
            idx = zone['index']
            for j in range(idx + 1, len(self.data)):
                if zone['type'] == 'DEMAND' and self.data[j]['low'] < zone['top']:
                    zone['mitigated'] = True
                    break
                elif zone['type'] == 'SUPPLY' and self.data[j]['high'] > zone['bottom']:
                    zone['mitigated'] = True
                    break
                    
        # Return only unmitigated zones
        return [z for z in snd_zones if not z['mitigated']]

    def detect_premium_discount(self) -> Optional[Dict]:
        """
        Calculate Premium and Discount zones based on the latest major dealing range.
        Returns a dictionary with 'premium_low', 'discount_high', 'eq' (equilibrium).
        """
        last_high = None
        last_low = None
        
        # Find the most recent Swing High and Swing Low to define the range
        for i in range(len(self.data)-1, -1, -1):
            if self.data[i]['swing_high'] and last_high is None:
                last_high = self.data[i]['high']
            if self.data[i]['swing_low'] and last_low is None:
                last_low = self.data[i]['low']
            if last_high is not None and last_low is not None:
                break
                
        if last_high is None or last_low is None:
            return None
            
        range_high = max(last_high, last_low)
        range_low = min(last_high, last_low)
        eq = (range_high + range_low) / 2.0
        
        return {
            "premium_low": eq, # Area above EQ is Premium
            "discount_high": eq, # Area below EQ is Discount
            "eq": eq,
            "range_high": range_high,
            "range_low": range_low
        }

    def detect_breaker_blocks(self, structure_events: List[Dict]) -> List[Dict]:
        """
        Detect Breaker Blocks (Failed Order Blocks).
        Re-evaluates Order Blocks but checks if they were decisively broken (closed through).
        If a Bullish OB is broken, it becomes a Bearish Breaker Block, and vice versa.
        """
        # First, generate all raw OBs (ignoring mitigation for now)
        obs = []
        for event in structure_events:
            break_idx = event['index']
            swing_idx = event['broken_swing_idx']
            
            if "BULLISH" in event['type']:
                search_start = max(0, swing_idx - 10)
                low_idx = search_start
                min_low = self.data[search_start]['low']
                for j in range(search_start, break_idx):
                    if self.data[j]['low'] < min_low:
                        min_low = self.data[j]['low']
                        low_idx = j
                
                ob_candle = None
                for j in range(low_idx, -1, -1):
                    if self.data[j]['close'] < self.data[j]['open']:
                        ob_candle = j
                        break
                
                if ob_candle is not None:
                    obs.append({
                        "original_type": "OB_BULLISH",
                        "top": self.data[ob_candle]['open'], 
                        "bottom": self.data[ob_candle]['low'],
                        "timestamp": self.data[ob_candle]['timestamp'],
                        "index": ob_candle
                    })
                    
            elif "BEARISH" in event['type']:
                search_start = max(0, swing_idx - 10)
                high_idx = search_start
                max_high = self.data[search_start]['high']
                for j in range(search_start, break_idx):
                    if self.data[j]['high'] > max_high:
                        max_high = self.data[j]['high']
                        high_idx = j
                
                ob_candle = None
                for j in range(high_idx, -1, -1):
                    if self.data[j]['close'] > self.data[j]['open']:
                        ob_candle = j
                        break
                        
                if ob_candle is not None:
                    obs.append({
                        "original_type": "OB_BEARISH",
                        "top": self.data[ob_candle]['high'],
                        "bottom": self.data[ob_candle]['open'], 
                        "timestamp": self.data[ob_candle]['timestamp'],
                        "index": ob_candle
                    })

        breakers = []
        # Check if they were broken decisively
        for ob in obs:
            idx = ob['index']
            broken = False
            for j in range(idx + 1, len(self.data)):
                # Decisive break = close beyond the OB boundary
                if ob['original_type'] == 'OB_BULLISH' and self.data[j]['close'] < ob['bottom']:
                    broken = True
                    break
                elif ob['original_type'] == 'OB_BEARISH' and self.data[j]['close'] > ob['top']:
                    broken = True
                    break
                    
            if broken:
                breakers.append({
                    "type": "BREAKER_BEARISH" if ob['original_type'] == 'OB_BULLISH' else "BREAKER_BULLISH",
                    "top": ob['top'],
                    "bottom": ob['bottom'],
                    "timestamp": ob['timestamp'],
                    "index": ob['index']
                })
                
        # Return only unmitigated Breaker Blocks (price hasn't returned to test them yet after breaking)
        unmitigated_breakers = []
        for brk in breakers:
            idx = brk['index']
            mitigated = False
            for j in range(idx + 1, len(self.data)):
                # If price returns to tap the breaker block
                if brk['type'] == 'BREAKER_BULLISH' and self.data[j]['low'] <= brk['top']:
                    mitigated = True
                    break
                elif brk['type'] == 'BREAKER_BEARISH' and self.data[j]['high'] >= brk['bottom']:
                    mitigated = True
                    break
            if not mitigated:
                unmitigated_breakers.append(brk)
                
        return unmitigated_breakers

    def detect_fibo_ote(self) -> Optional[Dict]:
        """
        Calculate Fibonacci Optimal Trade Entry (OTE) zones.
        OTE is typically the 0.618 to 0.786 retracement of the latest swing.
        """
        last_high = None
        last_low = None
        
        for i in range(len(self.data)-1, -1, -1):
            if self.data[i]['swing_high'] and last_high is None:
                last_high = self.data[i]['high']
            if self.data[i]['swing_low'] and last_low is None:
                last_low = self.data[i]['low']
            if last_high is not None and last_low is not None:
                break
                
        if last_high is None or last_low is None:
            return None
            
        range_high = max(last_high, last_low)
        range_low = min(last_high, last_low)
        range_size = range_high - range_low
        
        if range_size == 0:
            return None
            
        bullish_ote_top = range_high - (range_size * 0.618)
        bullish_ote_bottom = range_high - (range_size * 0.786)
        
        bearish_ote_bottom = range_low + (range_size * 0.618)
        bearish_ote_top = range_low + (range_size * 0.786)
        
        return {
            "bullish_ote": {"top": bullish_ote_top, "bottom": bullish_ote_bottom},
            "bearish_ote": {"top": bearish_ote_top, "bottom": bearish_ote_bottom}
        }

    def calculate_volume_profile(self, lookback: int = 100) -> Optional[float]:
        """
        Calculate the Point of Control (POC) using Volume Profile over the last `lookback` candles.
        POC is the price level with the highest traded volume.
        """
        if len(self.data) < 2:
            return None
            
        start_idx = max(0, len(self.data) - lookback)
        recent_data = self.data[start_idx:]
        
        highest_price = max(c['high'] for c in recent_data)
        lowest_price = min(c['low'] for c in recent_data)
        
        if highest_price == lowest_price:
            return highest_price
            
        num_bins = 50
        bin_size = (highest_price - lowest_price) / num_bins
        if bin_size == 0:
            return highest_price
            
        bins = [0.0] * num_bins
        
        for candle in recent_data:
            volume = candle.get('volume', 0)
            if volume == 0:
                continue
                
            c_high = candle['high']
            c_low = candle['low']
            
            start_bin = int((c_low - lowest_price) / bin_size)
            end_bin = int((c_high - lowest_price) / bin_size)
            
            start_bin = max(0, min(start_bin, num_bins - 1))
            end_bin = max(0, min(end_bin, num_bins - 1))
            
            bins_spanned = end_bin - start_bin + 1
            vol_per_bin = volume / bins_spanned
            
            for b in range(start_bin, end_bin + 1):
                bins[b] += vol_per_bin
                
        max_vol = -1
        poc_bin_idx = 0
        for i, vol in enumerate(bins):
            if vol > max_vol:
                max_vol = vol
                poc_bin_idx = i
                
        poc_price = lowest_price + (poc_bin_idx * bin_size) + (bin_size / 2)
        
        return poc_price
