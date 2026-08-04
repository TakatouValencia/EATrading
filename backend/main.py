from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import json
import os
import traceback
from typing import List
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from data_provider import DataProvider
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
from database import Database
from trade_manager import TradeManager
import settings_manager
from discord_notifier import send_discord_alert, send_discord_trade_update

app = FastAPI(title="Novaire EA SMC Engine")

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with actual frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Services
db = Database()
trade_manager = TradeManager(db)
data_provider = DataProvider()
signal_generator = SignalGenerator(cooldown_hours=0.5)

# WebSocket Connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                print(f"Error broadcasting to client: {e}")

manager = ConnectionManager()

async def handle_trade_closed(trade: dict, new_status: str, pnl: float):
    # Broadcast to web
    payload = {
        "type": "TRADE_CLOSED",
        "trade": trade,
        "status": new_status,
        "pnl": pnl
    }
    await manager.broadcast(json.dumps(payload))
    # Notify Discord
    await send_discord_trade_update(trade, new_status, pnl)

trade_manager.on_trade_closed = handle_trade_closed

# Background task for SMC Engine loop
async def run_smc_analysis(tick: dict):
    """
    Called by DataProvider whenever a new tick arrives.
    We append the tick to our active dataframe, run SMC logic, and check for signals.
    """
    try:
        symbol = tick['symbol']
        
        if not hasattr(app.state, 'market_data_lock'):
            app.state.market_data_lock = asyncio.Lock()
            
        # Secure the state using Lock to prevent race conditions during rapid ticks
        async with app.state.market_data_lock:
            if not hasattr(app.state, 'market_data'):
                app.state.market_data = {}
                
            if symbol not in app.state.market_data:
                # Fetch initial historical data
                df_ltf = data_provider.get_historical_data(symbol, interval="5min")
                df_htf = data_provider.get_historical_data(symbol, interval="1h")
                app.state.market_data[symbol] = {"ltf": df_ltf, "htf": df_htf}
            else:
                df_ltf = app.state.market_data[symbol]["ltf"]
                df_htf = app.state.market_data[symbol]["htf"]
                
                # Proper tick to candle aggregation logic
                tick_time = tick.get('timestamp')
                tick_price = tick['price']
                
                if isinstance(tick_time, str):
                    try:
                        tick_time_obj = datetime.fromisoformat(tick_time.replace('Z', '+00:00'))
                    except:
                        tick_time_obj = datetime.now()
                else:
                    tick_time_obj = tick_time if hasattr(tick_time, 'minute') else datetime.now()
                    
                # --- Update LTF (5min) ---
                if df_ltf:
                    last_ltf = df_ltf[-1]
                    last_ltf_time = last_ltf['timestamp']
                    if isinstance(last_ltf_time, str):
                        try:
                            last_ltf_time_obj = datetime.fromisoformat(last_ltf_time.replace('Z', '+00:00'))
                        except:
                            last_ltf_time_obj = datetime.now()
                    else:
                        last_ltf_time_obj = last_ltf_time
                        
                    time_diff_ltf = (tick_time_obj - last_ltf_time_obj).total_seconds()
                    
                    if time_diff_ltf < 300: # Within 5 minutes
                        last_ltf['close'] = tick_price
                        last_ltf['high'] = max(last_ltf['high'], tick_price)
                        last_ltf['low'] = min(last_ltf['low'], tick_price)
                    else:
                        new_candle = {
                            'timestamp': tick_time_obj.isoformat(),
                            'open': tick_price, 'high': tick_price, 'low': tick_price, 'close': tick_price, 'volume': 0
                        }
                        df_ltf.append(new_candle)
                        if len(df_ltf) > 1000: df_ltf.pop(0)

                # --- Update HTF (1h) ---
                if df_htf:
                    last_htf = df_htf[-1]
                    last_htf_time = last_htf['timestamp']
                    if isinstance(last_htf_time, str):
                        try:
                            last_htf_time_obj = datetime.fromisoformat(last_htf_time.replace('Z', '+00:00'))
                        except:
                            last_htf_time_obj = datetime.now()
                    else:
                        last_htf_time_obj = last_htf_time
                        
                    time_diff_htf = (tick_time_obj - last_htf_time_obj).total_seconds()
                    
                    if time_diff_htf < 3600: # Within 1 hour
                        last_htf['close'] = tick_price
                        last_htf['high'] = max(last_htf['high'], tick_price)
                        last_htf['low'] = min(last_htf['low'], tick_price)
                    else:
                        new_htf_candle = {
                            'timestamp': tick_time_obj.isoformat(),
                            'open': tick_price, 'high': tick_price, 'low': tick_price, 'close': tick_price, 'volume': 0
                        }
                        df_htf.append(new_htf_candle)
                        if len(df_htf) > 1000: df_htf.pop(0)
                
            # Run SMC Engine on LTF
            engine_ltf = SMCEngine(df_ltf)
            events = engine_ltf.detect_bos_choch()
            fvgs = engine_ltf.detect_fvg()
            obs = engine_ltf.detect_order_blocks(events)
            sweeps = engine_ltf.detect_liquidity_sweeps()
            snr_zones = engine_ltf.detect_support_resistance()
            snd_zones = engine_ltf.detect_supply_demand()
            
            # Run SMC Engine on HTF to get trend
            engine_htf = SMCEngine(df_htf)
            htf_events = engine_htf.detect_bos_choch()
            
            htf_trend = None
            if htf_events:
                last_htf_event = htf_events[-1]
                if "BULLISH" in last_htf_event['type']:
                    htf_trend = "BULLISH"
                elif "BEARISH" in last_htf_event['type']:
                    htf_trend = "BEARISH"
            
            # Check for Signals ONLY if we don't already have an active/pending trade for this symbol
            signal = None
            if not trade_manager.has_active_trade(symbol):
                # Now evaluate_confluence is async (calls Gemini LLM)
                signal = await signal_generator.evaluate_confluence(
                    symbol=symbol,
                    current_price=tick['price'],
                    events=events,
                    obs=obs,
                    fvgs=fvgs,
                    sweeps=sweeps,
                    htf_trend=htf_trend,
                    snr_zones=snr_zones,
                    snd_zones=snd_zones
                )
            
        # Outside the lock - Broadcast to clients
        payload = {
            "type": "TICK",
            "data": tick
        }
        
        if signal:
            payload["signal"] = signal
            result = db.save_signal(signal)
            if result and "id" in result:
                signal["id"] = result["id"]
            trade_manager.add_trade(signal)
            
            # Send Discord notification (runs asynchronously in background)
            await send_discord_alert(signal)
            
        await trade_manager.process_tick(tick)
            
        await manager.broadcast(json.dumps(payload))
        
    except Exception as e:
        print(f"[{tick.get('symbol', 'UNKNOWN')}] SMC Engine Error: {e}")
        traceback.print_exc()

@app.on_event("startup")
async def startup_event():
    # Start DataProvider in background
    symbols = ["XAU/USD"]
    data_provider.add_callback(run_smc_analysis)
    
    # Start websocket connection to TwelveData asynchronously
    asyncio.create_task(data_provider.connect_websocket(symbols))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, listen for client messages if any
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/historical/{symbol:path}")
async def get_historical(symbol: str, interval: str = "5min"):
    """Endpoint for frontend to fetch initial chart data."""
    # Fastapi treats slashes in path params carefully, symbol could be XAU/USD
    historical_data = data_provider.get_historical_data(symbol, interval=interval)
    if not historical_data:
        return {"data": []}
    
    # lightweight-charts expects time, open, high, low, close
    formatted = []
    for r in historical_data:
        # Check if timestamp is string (from ISO) or datetime
        ts = r['timestamp']
        if isinstance(ts, str):
            try:
                # Basic isoformat parsing, handle 'Z' or offset if needed
                ts_obj = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                unix_time = int(ts_obj.timestamp())
            except:
                # If parsing fails just use it as string, lightweight chart can sometimes handle it
                unix_time = ts
        elif hasattr(ts, 'timestamp'):
            unix_time = int(ts.timestamp())
        else:
            unix_time = ts
            
        formatted.append({
            "time": unix_time,
            "open": r['open'],
            "high": r['high'],
            "low": r['low'],
            "close": r['close']
        })
    return {"data": formatted}

@app.get("/api/signals")
async def get_signals():
    """Fetch track record / recent signals."""
    return {"signals": db.get_historical_signals()}

@app.get("/api/stats")
async def get_stats():
    """Fetch trade statistics (win rate, etc)."""
    return db.get_statistics()

class SettingsModel(BaseModel):
    account_balance: float
    risk_percentage: float

@app.get("/api/settings")
async def get_settings():
    return settings_manager.load_settings()

@app.post("/api/settings")
async def update_settings(settings: SettingsModel):
    new_settings = {
        "account_balance": settings.account_balance,
        "risk_percentage": settings.risk_percentage
    }
    settings_manager.save_settings(new_settings)
    return {"status": "success", "settings": new_settings}

# --- CUSTOM SIGNAL API ---

class CustomSignalModel(BaseModel):
    symbol: str
    type: str  # "BUY LIMIT", "SELL LIMIT", "BUY", "SELL"
    entry: float
    sl: float
    tp: float
    reasons: List[str] = ["Custom API Signal"]

@app.post("/api/custom-signal")
async def receive_custom_signal(signal_data: CustomSignalModel, x_custom_signal_secret: str = Header(None)):
    """
    Endpoint to receive custom signals from external scripts.
    Protected by X-Custom-Signal-Secret header.
    """
    secret = os.getenv("CUSTOM_SIGNAL_SECRET")
    if not secret or x_custom_signal_secret != secret:
        raise HTTPException(status_code=401, detail="Unauthorized Custom Signal")
        
    settings = settings_manager.load_settings()
    acc_balance = float(settings.get("account_balance", 10000.0))
    risk_pct = float(settings.get("risk_percentage", 1.0))
    
    from risk_calculator import calculate_pips, calculate_lot_size
    sl_pips = calculate_pips(signal_data.symbol, signal_data.entry, signal_data.sl)
    lot_size = calculate_lot_size(acc_balance, risk_pct, sl_pips, signal_data.symbol)
    
    signal = {
        "symbol": signal_data.symbol,
        "type": signal_data.type,
        "timestamp": datetime.now().isoformat(),
        "entry": signal_data.entry,
        "sl": signal_data.sl,
        "tp": signal_data.tp,
        "lot_size": lot_size,
        "reasons": signal_data.reasons,
        "status": "PENDING"
    }
    
    result = db.save_signal(signal)
    if result and "id" in result:
        signal["id"] = result["id"]
        
    trade_manager.add_trade(signal)
    
    # Broadcast to websocket
    payload = {
        "type": "NEW_CUSTOM_SIGNAL",
        "signal": signal
    }
    await manager.broadcast(json.dumps(payload))
    await send_discord_alert(signal)
    
    return {"status": "success", "message": "Custom signal processed and added", "signal": signal}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
