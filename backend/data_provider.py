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

    def get_historical_data(self, symbol: str, interval: str = "5min", outputsize: int = 500, use_csv: bool = True) -> List[Dict]:
        """Fetch historical data via CSV or REST API."""
        # Check if CSV exists first
        if use_csv:
            csv_data = self.get_historical_data_from_csv(symbol, interval, outputsize)
            if csv_data:
                return csv_data
            
        if not self.api_key:
            # Return dummy data for development if no key
            return self._generate_dummy_data(symbol, interval)
            
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
                return self._generate_dummy_data(symbol, interval)
                
        except Exception as e:
            print(f"REST API error (fallback to dummy): {e}")
            return self._generate_dummy_data(symbol, interval)
            
    def _generate_dummy_data(self, symbol: str, interval: str = "5min") -> List[Dict]:
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
            
            # Map interval from twelvedata ('1min', '15min') to yfinance ('1m', '15m')
            yf_interval = interval.replace("min", "m")
            period = "7d" if yf_interval == "1m" else "60d"
            
            ticker = yf.Ticker(yf_ticker)
            if yf_interval == "4h":
                df_1h = ticker.history(period="730d", interval="1h")
                df = df_1h.resample('4h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
            else:
                if yf_interval == "1h": period = "730d" # Max for 1h
                df = ticker.history(period=period, interval=yf_interval)
                
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

    def get_historical_data_from_csv(self, symbol: str, interval: str, max_records: int = None) -> List[Dict]:
        """Fetch historical data from MT5 CSV files in the data directory."""
        import csv
        
        # MT5 standard export naming convention (e.g., XAUUSD_M1.csv)
        clean_symbol = symbol.replace("/", "")
        
        # Map our internal intervals to MT5 standard suffixes
        interval_map = {
            "1min": "M1",
            "5min": "M5",
            "15min": "M15",
            "30min": "M30",
            "1h": "H1",
            "4h": "H4",
            "1day": "D1"
        }
        
        mt5_interval = interval_map.get(interval, interval)
        filename = f"{clean_symbol}_{mt5_interval}.csv"
        file_path = os.path.join(os.path.dirname(__file__), 'data', filename)
        
        if not os.path.exists(file_path):
            return []
            
        try:
            print(f"Loading historical data from {file_path}...")
            data = []
            with open(file_path, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                
                # Check for MT5 specific column headers vs standard OHLCV
                # MT5 headers usually: <DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>
                # Or standard CSV with commas
                
                # Sniff delimiter
                f.seek(0)
                first_line = f.readline()
                delimiter = '\t' if '\t' in first_line else ','
                f.seek(0)
                reader = csv.DictReader(f, delimiter=delimiter)
                
                for row in reader:
                    # Parse standard MT5 date and time format
                    # E.g. <DATE> 2026.01.01, <TIME> 00:00:00
                    date_key = '<DATE>' if '<DATE>' in row else 'Date' if 'Date' in row else 'time'
                    time_key = '<TIME>' if '<TIME>' in row else 'Time' if 'Time' in row else None
                    open_key = '<OPEN>' if '<OPEN>' in row else 'Open' if 'Open' in row else 'open'
                    high_key = '<HIGH>' if '<HIGH>' in row else 'High' if 'High' in row else 'high'
                    low_key = '<LOW>' if '<LOW>' in row else 'Low' if 'Low' in row else 'low'
                    close_key = '<CLOSE>' if '<CLOSE>' in row else 'Close' if 'Close' in row else 'close'
                    vol_key = '<TICKVOL>' if '<TICKVOL>' in row else 'Volume' if 'Volume' in row else 'volume'
                    
                    if date_key not in row or not row[date_key]: continue
                    
                    try:
                        timestamp_str = row[date_key]
                        if time_key and row[time_key]:
                            timestamp_str = f"{row[date_key].replace('.', '-')}T{row[time_key]}"
                        elif '.' in timestamp_str:
                            timestamp_str = timestamp_str.replace('.', '-')
                            
                        data.append({
                            'timestamp': timestamp_str,
                            'open': float(row[open_key]),
                            'high': float(row[high_key]),
                            'low': float(row[low_key]),
                            'close': float(row[close_key]),
                            'volume': float(row.get(vol_key, 0))
                        })
                    except Exception as parse_e:
                        continue
            
            # Standardize order: oldest to newest
            if data and data[0]['timestamp'] > data[-1]['timestamp']:
                data.reverse()
                
            if max_records and len(data) > max_records:
                data = data[-max_records:]
                
            print(f"Loaded {len(data)} records from {filename}")
            return data
        except Exception as e:
            print(f"Error reading CSV {file_path}: {e}")
            return []
