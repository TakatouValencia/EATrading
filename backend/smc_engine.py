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
                    "broken_swing_idx": last_swing_high_idx
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
                    "broken_swing_idx": last_swing_low_idx
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
                
        # Simple mitigation check (for current data snapshot)
        for fvg in fvgs:
            idx = fvg['index']
            for j in range(idx + 1, len(self.data)):
                if fvg['type'] == 'FVG_BULLISH' and self.data[j]['low'] < fvg['bottom']:
                    fvg['mitigated'] = True
                    break
                elif fvg['type'] == 'FVG_BEARISH' and self.data[j]['high'] > fvg['top']:
                    fvg['mitigated'] = True
                    break
                    
        return fvgs

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
                    
        return obs

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
                sweeps.append({
                    "type": "SWEEP_BEARISH",
                    "index": i,
                    "timestamp": row['timestamp'],
                    "level": self.data[last_swing_high_idx]['high'],
                    "swept_swing_idx": last_swing_high_idx
                })
                # Reset to avoid multiple triggers for the same swing
                last_swing_high_idx = None
                
            # Bullish Sweep: price goes below last swing low, but closes above it
            elif last_swing_low_idx < i and current_low < self.data[last_swing_low_idx]['low'] and current_close > self.data[last_swing_low_idx]['low']:
                sweeps.append({
                    "type": "SWEEP_BULLISH",
                    "index": i,
                    "timestamp": row['timestamp'],
                    "level": self.data[last_swing_low_idx]['low'],
                    "swept_swing_idx": last_swing_low_idx
                })
                # Reset
                last_swing_low_idx = None
                
        return sweeps
