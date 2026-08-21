from abc import ABC, abstractmethod
from typing import Dict, List, Optional

class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    Ensures all strategies implement the same evaluate method
    for apple-to-apple comparison in the walk-forward backtest.
    """
    
    def __init__(self, name: str):
        self.name = name
        # Blacklist zones or price levels that recently hit SL
        self.blacklist = []
        
    @abstractmethod
    async def evaluate(self, symbol: str, current_price: float, current_time: str, 
                 df_ltf: List[Dict], df_htf: List[Dict], df_h1: List[Dict], df_h4: List[Dict]) -> Optional[Dict]:
        """
        Evaluate market conditions and return a trade signal if criteria are met.
        
        Args:
            symbol (str): The trading symbol (e.g., "XAU/USD")
            current_price (float): The latest close price
            current_time (str): Timestamp of the current candle
            df_ltf (List[Dict]): Lower timeframe data (e.g., M15) - windowed
            df_htf (List[Dict]): Higher timeframe data (e.g., H1) - windowed
            df_h1 (List[Dict]): H1 timeframe data - windowed
            df_h4 (List[Dict]): H4 timeframe data - windowed
            
        Returns:
            Optional[Dict]: Signal dictionary containing 'type', 'entry', 'sl', 'tp' or None.
        """
        pass
        
    def add_to_blacklist(self, price_level: float, threshold: float = 2.0):
        """Add a price level to blacklist to avoid re-entering failed zones."""
        self.blacklist.append({'price': price_level, 'threshold': threshold})
        
    def is_blacklisted(self, price_level: float) -> bool:
        """Check if a price level is currently blacklisted."""
        for bl in self.blacklist:
            if abs(bl['price'] - price_level) <= bl['threshold']:
                return True
        return False
