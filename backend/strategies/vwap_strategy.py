from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy
from indicators import get_vwap, calculate_atr, is_bullish_reversal, is_bearish_reversal

class VWAPStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("VWAP Mean Reversion")
        
    async def evaluate(self, symbol: str, current_price: float, current_time: str, 
                 df_ltf: List[Dict], df_htf: List[Dict], df_h1: List[Dict], df_h4: List[Dict]) -> Optional[Dict]:
                 
        if len(df_ltf) < 2:
            return None
            
        current_idx = len(df_ltf) - 1
        vwap_data = get_vwap(df_ltf, current_idx)
        
        if not vwap_data:
            return None
            
        atr = calculate_atr(df_ltf[-15:-1], period=14)
        
        # We need the previous candle and current candle to confirm rejection
        curr_candle = df_ltf[-1]
        prev_candle = df_ltf[-2]
        
        upper_band = vwap_data['upper_2']
        lower_band = vwap_data['lower_2']
        vwap_line = vwap_data['vwap']
        
        # Check if price rejected the Lower Band (Bullish Mean Reversion)
        # Condition: Previous candle poked below lower band, current candle is a bullish reversal closing above lower band
        if prev_candle['low'] < lower_band and curr_candle['close'] > lower_band:
            if is_bullish_reversal(df_ltf):
                # Entry on close
                entry = current_price
                sl = prev_candle['low'] - (0.5 * atr) # Buffer below swing low
                tp = vwap_line
                
                # Check RR (at least 1R)
                risk = entry - sl
                reward = tp - entry
                if risk > 0 and (reward / risk) >= 1.0:
                    if not self.is_blacklisted(entry):
                        return {
                            'type': 'BUY (VWAP Reversion)',
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'reasons': ['Price rejected VWAP Lower Band (2 StdDev)', 'Bullish Reversal Pattern detected']
                        }
                        
        # Check if price rejected the Upper Band (Bearish Mean Reversion)
        if prev_candle['high'] > upper_band and curr_candle['close'] < upper_band:
            if is_bearish_reversal(df_ltf):
                entry = current_price
                sl = prev_candle['high'] + (0.5 * atr)
                tp = vwap_line
                
                risk = sl - entry
                reward = entry - tp
                if risk > 0 and (reward / risk) >= 1.0:
                    if not self.is_blacklisted(entry):
                        return {
                            'type': 'SELL (VWAP Reversion)',
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'reasons': ['Price rejected VWAP Upper Band (2 StdDev)', 'Bearish Reversal Pattern detected']
                        }
                        
        return None
