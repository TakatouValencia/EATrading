import math
from typing import List, Dict, Optional
from datetime import datetime

def calculate_ema(data: List[Dict], period: int, key: str = 'close') -> Optional[float]:
    if len(data) < period:
        return None
    
    # Calculate initial SMA
    sma = sum([c[key] for c in data[:period]]) / period
    multiplier = 2 / (period + 1)
    
    ema = sma
    for i in range(period, len(data)):
        ema = (data[i][key] - ema) * multiplier + ema
        
    return ema

def get_ema_series(data: List[Dict], period: int, key: str = 'close') -> List[float]:
    if len(data) < period:
        return []
    
    sma = sum([c[key] for c in data[:period]]) / period
    multiplier = 2 / (period + 1)
    
    emas = [None] * (period - 1) + [sma]
    ema = sma
    
    for i in range(period, len(data)):
        ema = (data[i][key] - ema) * multiplier + ema
        emas.append(ema)
        
    return emas

def calculate_atr(data: List[Dict], period: int = 14) -> float:
    if len(data) < period + 1:
        return 0.5
        
    true_ranges = []
    for i in range(len(data) - period, len(data)):
        current = data[i]
        previous = data[i-1]
        
        high_low = current['high'] - current['low']
        high_close = abs(current['high'] - previous['close'])
        low_close = abs(current['low'] - previous['close'])
        
        tr = max(high_low, high_close, low_close)
        true_ranges.append(tr)
        
    return sum(true_ranges) / period

def get_atr_series(data: List[Dict], period: int = 14) -> List[float]:
    if len(data) < period + 1:
        return []
        
    true_ranges = [0.0]
    for i in range(1, len(data)):
        current = data[i]
        previous = data[i-1]
        
        high_low = current['high'] - current['low']
        high_close = abs(current['high'] - previous['close'])
        low_close = abs(current['low'] - previous['close'])
        
        tr = max(high_low, high_close, low_close)
        true_ranges.append(tr)
        
    atrs = [None] * period
    for i in range(period, len(data)):
        atr = sum(true_ranges[i-period+1:i+1]) / period
        atrs.append(atr)
        
    return atrs

def calculate_bollinger_bands(data: List[Dict], period: int = 20, num_std: float = 2.0, key: str = 'close') -> Optional[Dict]:
    if len(data) < period:
        return None
        
    recent_data = data[-period:]
    sma = sum([c[key] for c in recent_data]) / period
    
    variance = sum([(c[key] - sma) ** 2 for c in recent_data]) / period
    std_dev = math.sqrt(variance)
    
    return {
        'upper': sma + (num_std * std_dev),
        'middle': sma,
        'lower': sma - (num_std * std_dev),
        'width': (2 * num_std * std_dev) / sma  # Normalized width
    }

def get_vwap(data: List[Dict], current_candle_idx: int) -> Optional[Dict]:
    """Calculate Session VWAP and Standard Deviation Bands."""
    if current_candle_idx < 0 or current_candle_idx >= len(data):
        return None
        
    # Find start of current session (usually start of the day)
    current_time_str = data[current_candle_idx]['timestamp']
    try:
        if isinstance(current_time_str, str):
            dt = datetime.fromisoformat(current_time_str.replace('Z', '+00:00'))
        else:
            dt = current_time_str
        current_date = dt.date()
    except:
        return None
        
    start_idx = current_candle_idx
    while start_idx > 0:
        prev_time_str = data[start_idx-1]['timestamp']
        try:
            if isinstance(prev_time_str, str):
                prev_dt = datetime.fromisoformat(prev_time_str.replace('Z', '+00:00'))
            else:
                prev_dt = prev_time_str
                
            if prev_dt.date() != current_date:
                break
        except:
            break
        start_idx -= 1
        
    cumulative_tp_v = 0.0
    cumulative_v = 0.0
    
    for i in range(start_idx, current_candle_idx + 1):
        c = data[i]
        tp = (c['high'] + c['low'] + c['close']) / 3.0
        v = c.get('volume', 1.0)
        cumulative_tp_v += tp * v
        cumulative_v += v
        
    if cumulative_v == 0:
        return None
        
    vwap = cumulative_tp_v / cumulative_v
    
    # Calculate Standard Deviation for bands
    cumulative_variance = 0.0
    for i in range(start_idx, current_candle_idx + 1):
        c = data[i]
        tp = (c['high'] + c['low'] + c['close']) / 3.0
        v = c.get('volume', 1.0)
        cumulative_variance += v * ((tp - vwap) ** 2)
        
    variance = cumulative_variance / cumulative_v
    std_dev = math.sqrt(variance)
    
    return {
        'vwap': vwap,
        'upper_1': vwap + std_dev,
        'lower_1': vwap - std_dev,
        'upper_2': vwap + (2 * std_dev),
        'lower_2': vwap - (2 * std_dev)
    }

def get_asian_session_range(data: List[Dict], current_time_str: str) -> Optional[Dict]:
    """Calculate High and Low of Asian Session (00:00 - 07:00 GMT)."""
    try:
        if isinstance(current_time_str, str):
            current_dt = datetime.fromisoformat(current_time_str.replace('Z', '+00:00'))
        else:
            current_dt = current_time_str
            
        current_date = current_dt.date()
    except:
        return None
        
    asian_high = float('-inf')
    asian_low = float('inf')
    found_candles = False
    
    # Go backwards to find candles in the 00:00 - 07:00 range for the CURRENT date
    for i in range(len(data)-1, -1, -1):
        c_time_str = data[i]['timestamp']
        try:
            if isinstance(c_time_str, str):
                dt = datetime.fromisoformat(c_time_str.replace('Z', '+00:00'))
            else:
                dt = c_time_str
                
            if dt.date() < current_date:
                break # Moved to previous day
                
            if dt.date() == current_date and 0 <= dt.hour < 7:
                asian_high = max(asian_high, data[i]['high'])
                asian_low = min(asian_low, data[i]['low'])
                found_candles = True
        except:
            continue
            
    if found_candles:
        return {
            'high': asian_high,
            'low': asian_low,
            'middle': (asian_high + asian_low) / 2.0
        }
    return None

def is_bullish_reversal(data: List[Dict]) -> bool:
    """Check for bullish pinbar or engulfing on the latest candle."""
    if len(data) < 2: return False
    
    curr = data[-1]
    prev = data[-2]
    
    curr_body = abs(curr['close'] - curr['open'])
    prev_body = abs(prev['close'] - prev['open'])
    
    # Bullish Engulfing
    if prev['close'] < prev['open'] and curr['close'] > curr['open']:
        if curr_body > prev_body and curr['close'] > prev['open']:
            return True
            
    # Bullish Pinbar
    curr_range = curr['high'] - curr['low']
    if curr_range > 0:
        upper_wick = curr['high'] - max(curr['open'], curr['close'])
        lower_wick = min(curr['open'], curr['close']) - curr['low']
        
        if lower_wick > curr_body * 2 and upper_wick < curr_body:
            return True
            
    return False

def is_bearish_reversal(data: List[Dict]) -> bool:
    """Check for bearish pinbar or engulfing on the latest candle."""
    if len(data) < 2: return False
    
    curr = data[-1]
    prev = data[-2]
    
    curr_body = abs(curr['close'] - curr['open'])
    prev_body = abs(prev['close'] - prev['open'])
    
    # Bearish Engulfing
    if prev['close'] > prev['open'] and curr['close'] < curr['open']:
        if curr_body > prev_body and curr['close'] < prev['open']:
            return True
            
    # Bearish Pinbar
    curr_range = curr['high'] - curr['low']
    if curr_range > 0:
        upper_wick = curr['high'] - max(curr['open'], curr['close'])
        lower_wick = min(curr['open'], curr['close']) - curr['low']
        
        if upper_wick > curr_body * 2 and lower_wick < curr_body:
            return True
            
    return False
