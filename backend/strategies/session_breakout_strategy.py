from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy
from indicators import get_asian_session_range, calculate_atr
from datetime import datetime

class SessionBreakoutStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Session Breakout (Asian Range)")
        # To avoid multiple entries in one day
        self.last_trade_date = None
        
    async def evaluate(self, symbol: str, current_price: float, current_time: str, 
                 df_ltf: List[Dict], df_htf: List[Dict], df_h1: List[Dict], df_h4: List[Dict]) -> Optional[Dict]:
                 
        if len(df_ltf) < 20:
            return None
            
        try:
            if isinstance(current_time, str):
                current_dt = datetime.fromisoformat(current_time.replace('Z', '+00:00'))
            else:
                current_dt = current_time
        except:
            return None
            
        # Only trade between 07:00 and 09:00 GMT (London Open)
        if not (7 <= current_dt.hour < 9):
            return None
            
        # Avoid multiple trades in one day
        current_date_str = current_dt.date().isoformat()
        if self.last_trade_date == current_date_str:
            return None
            
        asian_range = get_asian_session_range(df_ltf, current_time)
        if not asian_range:
            return None
            
        # Range should be reasonable (e.g. at least 20 pips, not too wide)
        range_size = asian_range['high'] - asian_range['low']
        if range_size < 2.0:
            return None
            
        curr_candle = df_ltf[-1]
        prev_candle = df_ltf[-2]
        
        atr = calculate_atr(df_ltf, period=14)
        
        # Bullish Breakout: Previous candle closes above Asian High
        if curr_candle['close'] > asian_range['high'] and prev_candle['close'] <= asian_range['high']:
            entry = current_price
            sl = asian_range['middle']
            
            sl_dist = entry - sl
            if sl_dist < 2.0: sl_dist = 2.0
            sl = entry - sl_dist
            
            tp = entry + (1.5 * sl_dist)
            
            if not self.is_blacklisted(entry):
                self.last_trade_date = current_date_str
                return {
                    'type': 'BUY (Session Breakout)',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'reasons': ['Price broke above Asian Session High during London Open']
                }
                
        # Bearish Breakout: Previous candle closes below Asian Low
        if curr_candle['close'] < asian_range['low'] and prev_candle['close'] >= asian_range['low']:
            entry = current_price
            sl = asian_range['middle']
            
            sl_dist = sl - entry
            if sl_dist < 2.0: sl_dist = 2.0
            sl = entry + sl_dist
            
            tp = entry - (1.5 * sl_dist)
            
            if not self.is_blacklisted(entry):
                self.last_trade_date = current_date_str
                return {
                    'type': 'SELL (Session Breakout)',
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'reasons': ['Price broke below Asian Session Low during London Open']
                }
                
        return None
