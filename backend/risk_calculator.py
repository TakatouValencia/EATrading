def calculate_pips(pair: str, entry_price: float, exit_price: float) -> float:
    """
    Calculate the difference in pips between two prices for a given pair.
    """
    clean_pair = pair.upper().replace("/", "").replace("_", "").replace("-", "")
    
    pip_value_map = {
        "XAUUSD": 0.10,      # Gold: 1 pip = $0.10 (0.1 points)
        "EURUSD": 0.0001,
        "GBPUSD": 0.0001,
        "USDJPY": 0.01,
        "BTCUSD": 1.0
    }
    
    if "XAU" in clean_pair:
        pip_value = 0.10
    elif "JPY" in clean_pair:
        pip_value = 0.01
    elif "BTC" in clean_pair:
        pip_value = 1.0
    else:
        pip_value = pip_value_map.get(clean_pair, 0.0001)
    
    # Calculate absolute difference in pips
    diff = abs(entry_price - exit_price)
    pips = diff / pip_value
    
    return round(pips, 2)


def calculate_lot_size(account_balance: float, risk_percentage: float, sl_pips: float, pair: str) -> float:
    """
    Calculate the appropriate lot size based on account balance, risk %, and stop loss distance in pips.
    """
    if sl_pips <= 0:
        return 0.01
        
    risk_amount = account_balance * (risk_percentage / 100.0)
    clean_pair = pair.upper().replace("/", "").replace("_", "").replace("-", "")
    
    pip_value_per_lot_map = {
        "XAUUSD": 10.0,
        "EURUSD": 10.0,
        "GBPUSD": 10.0,
        "USDJPY": 9.0,
        "BTCUSD": 1.0,
    }
    
    if "XAU" in clean_pair:
        pip_value_per_lot = 10.0
    else:
        pip_value_per_lot = pip_value_per_lot_map.get(clean_pair, 10.0)
    
    # Formula: Risk($) = lot_size * sl_pips * pip_value_per_lot
    # Therefore: lot_size = Risk($) / (sl_pips * pip_value_per_lot)
    lot_size = risk_amount / (sl_pips * pip_value_per_lot)
    
    # Round to 2 decimal places with a minimum micro lot floor of 0.01
    return max(0.01, round(lot_size, 2))
