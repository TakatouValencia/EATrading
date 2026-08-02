import os
import json
import asyncio
import websockets
import requests
import random
from typing import Callable, Optional, List, Dict
from datetime import datetime, timedelta

class DataProvider:
    def __init__(self):
        self.api_key = os.getenv("TWELVE_DATA_API_KEY", "")
        self.ws_url = f"wss://ws.twelvedata.com/v1/quotes/price?apikey={self.api_key}"
        self.rest_url = "https://api.twelvedata.com"
        self.ws_connection = None
        self.callbacks = []
        self.is_running = False

    def add_callback(self, callback: Callable):
        """Add a callback to be executed when new tick data arrives."""
        self.callbacks.append(callback)

    async def connect_websocket(self, symbols: list):
        """Connect to Twelve Data WebSocket and stream live prices."""
        if not self.api_key:
            print("WARNING: Twelve Data API Key is missing.")
            return

        self.is_running = True
        
        while self.is_running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws_connection = ws
                    
                    # Subscribe to symbols
                    subscribe_msg = {
                        "action": "subscribe",
                        "params": {
                            "symbols": ",".join(symbols)
                        }
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    print(f"Subscribed to {symbols} via WebSocket.")
                    
                    while self.is_running:
                        message = await ws.recv()
                        data = json.loads(message)
                        
                        if data.get("event") == "price":
                            tick = {
                                "symbol": data.get("symbol"),
                                "price": float(data.get("price")),
                                "timestamp": data.get("timestamp"),
                                "source": "twelvedata_ws"
                            }
                            # Trigger callbacks
                            for callback in self.callbacks:
                                await callback(tick)
                                
            except Exception as e:
                print(f"WebSocket connection error: {e}")
                print("Attempting to reconnect in 5 seconds... (Fallback to REST if needed)")
                self.ws_connection = None
                await asyncio.sleep(5)

    def get_historical_data(self, symbol: str, interval: str = "5min", outputsize: int = 500) -> List[Dict]:
        """Fetch historical data via REST API."""
        if not self.api_key:
            # Return dummy data for development if no key
            return self._generate_dummy_data(symbol)
            
        url = f"{self.rest_url}/time_series"
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            data = response.json()
            
            if "values" in data:
                # Format to our pure dict format
                formatted_data = []
                for row in data["values"]:
                    formatted_data.append({
                        'timestamp': row['datetime'],
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row.get('volume', 0))
                    })
                
                # Sort from oldest to newest
                formatted_data.sort(key=lambda x: x['timestamp'])
                return formatted_data
            else:
                print(f"Error fetching historical data: {data}")
                return []
                
        except Exception as e:
            print(f"REST API error: {e}")
            return []
            
    def _generate_dummy_data(self, symbol: str) -> List[Dict]:
        """Generate dummy data for development without API key."""
        print(f"Generating dummy data for {symbol}...")
        
        now = datetime.now()
        data = []
        
        # Base price
        base = 2000.0 if "XAU" in symbol else 1.1000
        volatility = 2.0 if "XAU" in symbol else 0.0010
        
        current_price = base
        
        for i in range(100):
            timestamp = now - timedelta(minutes=5 * (100 - i))
            current_price += (random.random() - 0.5) * volatility
            
            data.append({
                'timestamp': timestamp.isoformat(),
                'open': current_price,
                'close': current_price,
                'high': current_price + volatility/2,
                'low': current_price - volatility/2,
                'volume': 1000
            })
            
        return data
