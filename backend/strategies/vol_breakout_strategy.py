from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy
from indicators import calculate_bollinger_bands, calculate_atr

class VolatilityBreakoutStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Volatility Breakout")
        
    async def evaluate(self, symbol: str, current_price: float, current_time: str, 
                 df_ltf: List[Dict], df_htf: List[Dict], df_h1: List[Dict], df_h4: List[Dict]) -> Optional[Dict]:
                 
        if len(df_ltf) < 40:
            return None
            
        # Get Bollinger Bands
        bb = calculate_bollinger_bands(df_ltf, period=20, num_std=2.0)
        if not bb:
            return None
            
        # Determine compression: We need bb width to be below the average bb width of the last 20 candles
        widths = []
        for i in range(20, 40):
            past_bb = calculate_bollinger_bands(df_ltf[i-20:i], period=20)
            if past_bb:
                widths.append(past_bb['width'])
                
        if not widths:
            return None
            
        avg_width = sum(widths) / len(widths)
        is_compressed = bb['width'] < (avg_width * 0.8) # 20% tighter than usual
        
        # If it's compressed, look for a breakout
        # We define breakout as a strong close outside the bands
        curr_candle = df_ltf[-1]
        
        atr = calculate_atr(df_ltf, period=14)
        
        if is_compressed:
            # Bullish Breakout
            if curr_candle['close'] > bb['upper'] and curr_candle['open'] < bb['upper']:
                entry = current_price
                sl = bb['middle'] # SMA 20 as SL
                
                sl_dist = entry - sl
                if sl_dist < 2.0: sl_dist = 2.0
                sl = entry - sl_dist
                
                tp = entry + (1.5 * sl_dist)
                
                if not self.is_blacklisted(entry):
                    return {
                        'type': 'BUY (Vol Breakout)',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'reasons': ['BB Compression Detected', 'Strong Bullish Breakout above Upper BB']
                    }
                    
            # Bearish Breakout
            if curr_candle['close'] < bb['lower'] and curr_candle['open'] > bb['lower']:
                entry = current_price
                sl = bb['middle']
                
                sl_dist = sl - entry
                if sl_dist < 2.0: sl_dist = 2.0
                sl = entry + sl_dist
                
                tp = entry - (1.5 * sl_dist)
                
                if not self.is_blacklisted(entry):
                    return {
                        'type': 'SELL (Vol Breakout)',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'reasons': ['BB Compression Detected', 'Strong Bearish Breakout below Lower BB']
                    }
                    
        return None
