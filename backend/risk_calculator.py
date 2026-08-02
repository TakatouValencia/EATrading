def calculate_pips(pair: str, entry_price: float, exit_price: float) -> float:
    """
    Calculate the difference in pips between two prices for a given pair.
    """
    pip_value_map = {
        "XAUUSD": 0.10,      # Gold: 1 pip = $0.10 (usually represented as 0.1, or the 2nd decimal place)
        "EURUSD": 0.0001,
        "GBPUSD": 0.0001,
        "USDJPY": 0.01,
        "BTCUSD": 1.0
    }
    
    # Default to 0.0001 if pair not found, but it's better to explicitly support pairs
    pip_value = pip_value_map.get(pair.upper(), 0.0001)
    
    # Calculate absolute difference in pips
    diff = abs(entry_price - exit_price)
    pips = diff / pip_value
    
    return round(pips, 2)


def calculate_lot_size(account_balance: float, risk_percentage: float, sl_pips: float, pair: str) -> float:
    """
    Calculate the appropriate lot size based on account balance, risk %, and stop loss distance in pips.
    """
    if sl_pips <= 0:
        return 0.0
        
    risk_amount = account_balance * (risk_percentage / 100.0)
    
    # Pip value per standard lot (100,000 units usually)
    # This is a simplification. Real pip value depends on account currency.
    # Assuming USD account currency:
    # EURUSD: 1 pip = $10 per standard lot
    # XAUUSD: 1 pip = $10 per standard lot (100 oz contract)
    # USDJPY: Varies based on current exchange rate, approximately $7-9 per lot
    
    # For a unified generic approach assuming standard $10/pip for pairs where quote currency is USD
    pip_value_per_lot_map = {
        "XAUUSD": 10.0,
        "EURUSD": 10.0,
        "GBPUSD": 10.0,
        "BTCUSD": 1.0, # Varies heavily by broker contract size
    }
    
    pip_value_per_lot = pip_value_per_lot_map.get(pair.upper(), 10.0)
    
    # Formula: Risk($) = lot_size * sl_pips * pip_value_per_lot
    # Therefore: lot_size = Risk($) / (sl_pips * pip_value_per_lot)
    lot_size = risk_amount / (sl_pips * pip_value_per_lot)
    
    # Round to 2 decimal places (micro lots)
    return round(lot_size, 2)
