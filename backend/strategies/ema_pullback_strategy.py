from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy
from indicators import calculate_ema, calculate_atr, is_bullish_reversal, is_bearish_reversal

class EMAPullbackStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("EMA Trend + Pullback")
        
    async def evaluate(self, symbol: str, current_price: float, current_time: str, 
                 df_ltf: List[Dict], df_htf: List[Dict], df_h1: List[Dict], df_h4: List[Dict]) -> Optional[Dict]:
                 
        if len(df_h1) < 200 or len(df_ltf) < 50:
            return None
            
        # Determine HTF Trend using H1 EMA 50 & 200
        h1_ema_50 = calculate_ema(df_h1, 50)
        h1_ema_200 = calculate_ema(df_h1, 200)
        
        if not h1_ema_50 or not h1_ema_200:
            return None
            
        is_htf_bullish = h1_ema_50 > h1_ema_200
        is_htf_bearish = h1_ema_50 < h1_ema_200
        
        # Determine LTF Pullback using M15 EMA 50
        ltf_ema_50 = calculate_ema(df_ltf, 50)
        if not ltf_ema_50:
            return None
            
        atr = calculate_atr(df_ltf, period=14)
        curr_candle = df_ltf[-1]
        prev_candle = df_ltf[-2]
        
        if is_htf_bullish:
            # Check for pullback to EMA 50
            # Condition: price was above EMA, touched it (low <= EMA50), and now shows bullish reversal closing above it
            if prev_candle['low'] <= ltf_ema_50 and curr_candle['close'] > ltf_ema_50:
                if is_bullish_reversal(df_ltf):
                    entry = current_price
                    sl = prev_candle['low'] - atr # Generous SL
                    
                    sl_dist = entry - sl
                    if sl_dist < 2.0: sl_dist = 2.0 # Minimum SL floor
                    sl = entry - sl_dist
                    
                    tp = entry + (1.5 * sl_dist) # 1.5R target
                    
                    if not self.is_blacklisted(entry):
                        return {
                            'type': 'BUY (EMA Pullback)',
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'reasons': ['HTF Trend Bullish (H1 EMA50 > EMA200)', 'M15 Pullback and Rejection from EMA50']
                        }
                        
        elif is_htf_bearish:
            # Check for pullback to EMA 50
            if prev_candle['high'] >= ltf_ema_50 and curr_candle['close'] < ltf_ema_50:
                if is_bearish_reversal(df_ltf):
                    entry = current_price
                    sl = prev_candle['high'] + atr
                    
                    sl_dist = sl - entry
                    if sl_dist < 2.0: sl_dist = 2.0
                    sl = entry + sl_dist
                    
                    tp = entry - (1.5 * sl_dist)
                    
                    if not self.is_blacklisted(entry):
                        return {
                            'type': 'SELL (EMA Pullback)',
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'reasons': ['HTF Trend Bearish (H1 EMA50 < EMA200)', 'M15 Pullback and Rejection from EMA50']
                        }
                        
        return None
