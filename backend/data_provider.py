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
                print(f"Error fetching historical data (fallback to dummy): {data}")
                return self._generate_dummy_data(symbol)
                
        except Exception as e:
            print(f"REST API error (fallback to dummy): {e}")
            return self._generate_dummy_data(symbol)
            
    def _generate_dummy_data(self, symbol: str) -> List[Dict]:
        """Fetch real data via Yahoo Finance as fallback instead of dummy data."""
        print(f"Fallback to yfinance for {symbol}...")
        try:
            import yfinance as yf
            
            # Map symbol to Yahoo Finance ticker
            if "XAU" in symbol:
                yf_ticker = "GC=F"
            elif "DXY" in symbol:
                yf_ticker = "DX-Y.NYB"
            else:
                yf_ticker = "EURUSD=X"
            
            # Use Ticker().history() for safer single-index columns
            ticker = yf.Ticker(yf_ticker)
            df = ticker.history(period="3d", interval="5m")
            
            if df.empty:
                print(f"yfinance returned empty for {yf_ticker}, using simulated data")
                return self._simulate_fallback(symbol)
                
            formatted_data = []
            for index, row in df.iterrows():
                import pandas as pd
                if pd.isna(row['Open']) or pd.isna(row['Close']):
                    continue
                    
                formatted_data.append({
                    'timestamp': index.isoformat(),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': float(row.get('Volume', 0))
                })
                
            return formatted_data
        except Exception as e:
            print(f"yfinance fallback error: {e}")
            return self._simulate_fallback(symbol)

    def _simulate_fallback(self, symbol: str) -> List[Dict]:
        """True fallback dummy data if EVERYTHING fails."""
        now = datetime.now()
        data = []
        base = 2400.0 if "XAU" in symbol else 1.1000
        volatility = 2.0 if "XAU" in symbol else 0.0010
        import random
        from datetime import timedelta
        
        current_price = base
        for i in range(100):
            timestamp = now - timedelta(minutes=5 * (100 - i))
            open_price = current_price
            close_price = current_price + (random.random() - 0.5) * volatility
            high_price = max(open_price, close_price) + (random.random() * (volatility / 2))
            low_price = min(open_price, close_price) - (random.random() * (volatility / 2))
            
            data.append({
                'timestamp': timestamp.isoformat(),
                'open': open_price,
                'close': close_price,
                'high': high_price,
                'low': low_price,
                'volume': 1000
            })
            current_price = close_price
        return data
