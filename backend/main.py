from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import json
import os
import traceback
from typing import List, Optional
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from data_provider import DataProvider
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
from database import Database
from trade_manager import TradeManager
import settings_manager
from discord_notifier import send_discord_alert, send_discord_trade_update, send_circuit_breaker_alert
from market_schedule import is_forex_market_open

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
signal_generator = SignalGenerator(cooldown_minutes=15)

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
    if new_status in ["WIN", "LOSS", "PARTIAL_WIN", "MISSED"]:
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
        tick_time = tick.get('timestamp')
        tick_price = float(tick['price'])
        
        if isinstance(tick_time, (int, float)):
            tick_time_obj = datetime.fromtimestamp(tick_time)
        elif isinstance(tick_time, str):
            try:
                tick_time_obj = datetime.fromisoformat(tick_time.replace('Z', '+00:00'))
            except Exception:
                tick_time_obj = datetime.now()
        else:
            tick_time_obj = tick_time if hasattr(tick_time, 'minute') else datetime.now()
        
        # Check if Forex/Gold Market is Open (Blocks execution on Saturdays/weekends)
        is_open, open_reason = is_forex_market_open(tick_time_obj, symbol)
        if not is_open:
            now_ts = datetime.now().timestamp()
            if not hasattr(app.state, 'last_closed_log'):
                app.state.last_closed_log = 0
            if now_ts - app.state.last_closed_log >= 600:
                app.state.last_closed_log = now_ts
                print(f"[{symbol}] [MARKET CLOSED - WEEKEND PAUSE] {open_reason}. Tick ignored.")
            return

        if not hasattr(app.state, 'market_data_lock'):
            app.state.market_data_lock = asyncio.Lock()
            
        # Secure the state using Lock to prevent race conditions during rapid ticks
        async with app.state.market_data_lock:
            if not hasattr(app.state, 'market_data'):
                app.state.market_data = {}
                
            if symbol not in app.state.market_data:
                # Fetch initial historical data for H1, M15, M5, M1 in threadpool to keep event loop free
                df_h1 = await asyncio.to_thread(data_provider.get_historical_data, symbol, interval="1h", use_csv=False)
                df_m15 = await asyncio.to_thread(data_provider.get_historical_data, symbol, interval="15min", use_csv=False)
                df_m5 = await asyncio.to_thread(data_provider.get_historical_data, symbol, interval="5min", use_csv=False)
                df_m1 = await asyncio.to_thread(data_provider.get_historical_data, symbol, interval="1min", use_csv=False)
                
                if not df_m15 or not df_m5 or not df_m1:
                    print(f"[{symbol}] Failed to fetch initial data for M15/M5/M1.")
                    return
                    
                print(f"[{symbol}] Initialized data cache: {len(df_h1 or [])} H1, {len(df_m15)} M15, {len(df_m5)} M5, {len(df_m1)} M1 candles.")
                app.state.market_data[symbol] = {"m1": df_m1, "m5": df_m5, "m15": df_m15, "h1": df_h1, "ltf": df_m1, "htf": df_m15}
            else:
                df_m1 = app.state.market_data[symbol].get("m1", app.state.market_data[symbol].get("ltf"))
                df_m5 = app.state.market_data[symbol].get("m5", [])
                df_m15 = app.state.market_data[symbol].get("m15", app.state.market_data[symbol].get("htf"))
                
                # --- Update LTF (M1) ---
                if df_m1:
                    last_m1 = df_m1[-1]
                    last_m1_time = last_m1['timestamp']
                    if isinstance(last_m1_time, str):
                        try:
                            last_m1_time_obj = datetime.fromisoformat(last_m1_time.replace('Z', '+00:00'))
                        except Exception:
                            last_m1_time_obj = datetime.now()
                    else:
                        last_m1_time_obj = last_m1_time
                        
                    time_diff_m1 = (tick_time_obj.replace(tzinfo=None) - last_m1_time_obj.replace(tzinfo=None)).total_seconds()
                    
                    if 0 <= time_diff_m1 < 60: # Within 1 minute
                        last_m1['close'] = tick_price
                        last_m1['high'] = max(last_m1['high'], tick_price)
                        last_m1['low'] = min(last_m1['low'], tick_price)
                    else:
                        new_candle = {
                            'timestamp': tick_time_obj.isoformat(),
                            'open': tick_price, 'high': tick_price, 'low': tick_price, 'close': tick_price, 'volume': 0
                        }
                        df_m1.append(new_candle)
                        if len(df_m1) > 1000: df_m1.pop(0)

                # --- Update MTF (M5) ---
                if df_m5:
                    last_m5 = df_m5[-1]
                    last_m5_time = last_m5['timestamp']
                    if isinstance(last_m5_time, str):
                        try:
                            last_m5_time_obj = datetime.fromisoformat(last_m5_time.replace('Z', '+00:00'))
                        except Exception:
                            last_m5_time_obj = datetime.now()
                    else:
                        last_m5_time_obj = last_m5_time
                        
                    time_diff_m5 = (tick_time_obj.replace(tzinfo=None) - last_m5_time_obj.replace(tzinfo=None)).total_seconds()
                    
                    if 0 <= time_diff_m5 < 300: # Within 5 minutes
                        last_m5['close'] = tick_price
                        last_m5['high'] = max(last_m5['high'], tick_price)
                        last_m5['low'] = min(last_m5['low'], tick_price)
                    else:
                        new_m5_candle = {
                            'timestamp': tick_time_obj.isoformat(),
                            'open': tick_price, 'high': tick_price, 'low': tick_price, 'close': tick_price, 'volume': 0
                        }
                        df_m5.append(new_m5_candle)
                        if len(df_m5) > 1000: df_m5.pop(0)

                # --- Update HTF (M15) ---
                if df_m15:
                    last_m15 = df_m15[-1]
                    last_m15_time = last_m15['timestamp']
                    if isinstance(last_m15_time, str):
                        try:
                            last_m15_time_obj = datetime.fromisoformat(last_m15_time.replace('Z', '+00:00'))
                        except Exception:
                            last_m15_time_obj = datetime.now()
                    else:
                        last_m15_time_obj = last_m15_time
                        
                    time_diff_m15 = (tick_time_obj.replace(tzinfo=None) - last_m15_time_obj.replace(tzinfo=None)).total_seconds()
                    
                    if 0 <= time_diff_m15 < 900: # Within 15 minutes
                        last_m15['close'] = tick_price
                        last_m15['high'] = max(last_m15['high'], tick_price)
                        last_m15['low'] = min(last_m15['low'], tick_price)
                    else:
                        new_m15_candle = {
                            'timestamp': tick_time_obj.isoformat(),
                            'open': tick_price, 'high': tick_price, 'low': tick_price, 'close': tick_price, 'volume': 0
                        }
                        df_m15.append(new_m15_candle)
                        if len(df_m15) > 1000: df_m15.pop(0)

            # Run SMC Engine on LTF (M1)
            engine_m1 = SMCEngine(df_m1)
            events = engine_m1.detect_bos_choch()
            sweeps = engine_m1.detect_liquidity_sweeps()
            snr_zones = engine_m1.detect_support_resistance()
            snd_zones = engine_m1.detect_supply_demand()
            fibo_ote = engine_m1.detect_fibo_ote()
            poc_price = engine_m1.calculate_volume_profile(lookback=100)
            amd_setups = engine_m1.detect_amd()
            
            # Run SMC Engine on MTF (M5)
            engine_m5 = SMCEngine(df_m5) if df_m5 else None
            m5_events = engine_m5.detect_bos_choch() if engine_m5 else []
            m5_obs = engine_m5.detect_order_blocks(m5_events) if engine_m5 else []
            m5_fvgs = engine_m5.detect_fvg() if engine_m5 else []
            m5_breakers = engine_m5.detect_breaker_blocks(m5_events) if engine_m5 else []
            m5_trend = None
            if m5_events:
                last_m5_event = m5_events[-1]
                m5_trend = "BULLISH" if "BULLISH" in last_m5_event['type'] else "BEARISH"

            # Run SMC Engine on Macro (H1)
            df_h1 = app.state.market_data[symbol].get("h1", [])
            engine_h1 = SMCEngine(df_h1) if df_h1 else None
            h1_events = engine_h1.detect_bos_choch() if engine_h1 else []
            h1_obs = engine_h1.detect_order_blocks(h1_events) if engine_h1 else []
            h1_pd = engine_h1.detect_premium_discount() if engine_h1 else None
            h1_trend = ("BULLISH" if "BULLISH" in h1_events[-1]['type'] else "BEARISH") if h1_events else None

            # Run SMC Engine on HTF (M15)
            engine_m15 = SMCEngine(df_m15)
            m15_events = engine_m15.detect_bos_choch()
            m15_obs = engine_m15.detect_order_blocks(m15_events)
            m15_fvgs = engine_m15.detect_fvg()
            m15_breakers = engine_m15.detect_breaker_blocks(m15_events)
            pd_zones = engine_m15.detect_premium_discount()
            
            m15_trend = None
            if m15_events:
                last_m15_event = m15_events[-1]
                m15_trend = "BULLISH" if "BULLISH" in last_m15_event['type'] else "BEARISH"

            # Combine M15 and M5 institutional POIs (refined, tight zones)
            combined_obs = m15_obs + m5_obs
            combined_fvgs = m15_fvgs + m5_fvgs
            combined_breakers = m15_breakers + m5_breakers
            combined_qms = engine_m15.detect_quasimodo() + (engine_m5.detect_quasimodo() if engine_m5 else [])
            combined_rbs = engine_m15.detect_rbs_sbr() + (engine_m5.detect_rbs_sbr() if engine_m5 else [])
            combined_crts = engine_m15.detect_crt() + (engine_m5.detect_crt() if engine_m5 else [])
            adx_m15 = engine_m15.calculate_adx(14)
            adx_h1 = engine_h1.calculate_adx(14) if engine_h1 else 25.0

            # DXY Trend for Intermarket Correlation (using cached/async data)
            dxy_trend = None
            if "XAU" in symbol:
                if "DXY" not in app.state.market_data:
                    try:
                        df_dxy = await asyncio.to_thread(data_provider.get_historical_data, "DXY", interval="15min", use_csv=False)
                        if df_dxy:
                            app.state.market_data["DXY"] = df_dxy
                    except Exception as e:
                        print(f"Note: DXY fetch skipped: {e}")
                
                if "DXY" in app.state.market_data:
                    df_dxy = app.state.market_data["DXY"]
                    engine_dxy = SMCEngine(df_dxy)
                    dxy_events = engine_dxy.detect_bos_choch()
                    if dxy_events:
                        dxy_trend = "BULLISH" if "BULLISH" in dxy_events[-1]['type'] else "BEARISH"
            
            # Apply Risk Management / Circuit Breaker Check
            trade_manager.current_time_str = tick_time_obj.isoformat()
            trade_manager._check_daily_reset()
            allowed, reason = trade_manager.check_trading_allowed()

            # Periodic Scanning Heartbeat (Every 30 seconds per symbol)
            if not hasattr(app.state, 'last_scan_log'):
                app.state.last_scan_log = {}
            now_sec = datetime.now().timestamp()
            if now_sec - app.state.last_scan_log.get(symbol, 0) >= 30:
                app.state.last_scan_log[symbol] = now_sec
                active_c = len([t for t in trade_manager.tracked_trades if t.get('symbol') == symbol])
                cb_status = "LOCKED" if not allowed else f"OK ({trade_manager.consecutive_losses}/3 SLs, {trade_manager.daily_pnl:.1f}R)"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [SCANNING] {symbol}: {tick_price:.2f} | H1: {h1_trend or 'N/A'} | M15: {m15_trend or 'N/A'} | M5: {m5_trend or 'N/A'} | Active: {active_c} | Circuit Breaker: {cb_status}")

            # Check for Signals ONLY if we don't already have a PENDING or ACTIVE trade for this symbol
            signal = None
            if not trade_manager.has_active_trade(symbol):
                if allowed:
                    atr = engine_m1.calculate_atr(period=14)
                    reversal_patterns = engine_m1.detect_reversal_patterns()
                    
                    signal = await signal_generator.evaluate_confluence(
                        symbol=symbol,
                        current_price=tick_price,
                        events=events,
                        obs=combined_obs,
                        fvgs=combined_fvgs,
                        sweeps=sweeps,
                        m15_trend=m15_trend,
                        m5_trend=m5_trend,
                        htf_trend=m15_trend,
                        h1_trend=h1_trend,
                        snr_zones=snr_zones,
                        snd_zones=snd_zones,
                        pd_zones=pd_zones,
                        breakers=combined_breakers,
                        dxy_trend=dxy_trend,
                        fibo_ote=fibo_ote,
                        poc_price=poc_price,
                        trade_manager=trade_manager,
                        amd_setups=amd_setups,
                        atr=atr,
                        reversal_patterns=reversal_patterns,
                        db=db,
                        engine_ltf=engine_m1,
                        qm_patterns=combined_qms,
                        rbs_sbr=combined_rbs,
                        h1_obs=h1_obs,
                        h1_pd_zones=h1_pd,
                        crt_patterns=combined_crts,
                        adx_m15=adx_m15,
                        adx_h1=adx_h1
                    )
                    # Process and register new signal atomically
                    if signal and signal.get("status") not in ["SKIPPED", "REJECTED"]:
                        is_identical = False
                        for t in trade_manager.tracked_trades:
                            if t['symbol'] == symbol and t['status'] in ['PENDING', 'ACTIVE']:
                                t_entry = float(t.get('entry_price', t.get('entry', 0)))
                                if t['type'] == signal['type'] and abs(t_entry - signal['entry']) < 0.5:
                                    is_identical = True
                                    break
                                    
                        if not is_identical:
                            # Cancel old pending setups so we don't hold multiple limit orders
                            await trade_manager.cancel_pending_trades(symbol)
                            
                            result = db.save_signal(signal)
                            if result and "id" in result:
                                signal["id"] = result["id"]
                            trade_manager.add_trade(signal)
                            
                            # Send Discord notification (deduplicated by discord_notifier)
                            await send_discord_alert(signal)
                            
                else:
                    # Circuit breaker triggered
                    if not getattr(app.state, 'circuit_breaker_alert_sent', False):
                        app.state.circuit_breaker_alert_sent = True
                        print(f"[{symbol}] 🚨 CIRCUIT BREAKER ACTIVE: {reason}")
                        await send_circuit_breaker_alert(reason)
            
            await trade_manager.process_tick(tick)
            
        # Outside the lock - Broadcast to clients
        payload = {
            "type": "TICK",
            "data": tick
        }
        if signal and signal.get("status") not in ["SKIPPED", "REJECTED"]:
            payload["signal"] = signal
        
        # Calculate freshness for tracked trades
        active_trades_data = []
        for t in trade_manager.tracked_trades:
            t_copy = t.copy()
            entry = float(t_copy.get('entry_price', t_copy.get('entry', 0)))
            sl = float(t_copy.get('sl_price', t_copy.get('sl', 0)))
            ts = t_copy.get('timestamp')
            
            age_minutes = 0
            if ts:
                try:
                    ts_obj = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    now_tz = datetime.now(ts_obj.tzinfo)
                    age_minutes = int((now_tz - ts_obj).total_seconds() / 60)
                except Exception:
                    pass
            t_copy['age_minutes'] = age_minutes
            
            freshness = "FRESH"
            if t_copy['status'] == 'PENDING':
                if age_minutes > 5:
                    freshness = "VALID"
                
                dist_to_sl = abs(entry - sl)
                current_price = tick['price']
                if dist_to_sl > 0:
                    if "BUY" in t_copy['type']:
                        if current_price < entry:
                            moved_pct = (entry - current_price) / dist_to_sl
                            freshness = "INVALID" if moved_pct >= 0.5 else "VALID"
                    else:
                        if current_price > entry:
                            moved_pct = (current_price - entry) / dist_to_sl
                            freshness = "INVALID" if moved_pct >= 0.5 else "VALID"
                                
            t_copy['freshness_status'] = freshness
            active_trades_data.append(t_copy)
            
        payload['active_trades'] = active_trades_data
            
        await manager.broadcast(json.dumps(payload))
        
    except Exception as e:
        print(f"[{tick.get('symbol', 'UNKNOWN')}] SMC Engine Error: {e}")
        traceback.print_exc()

@app.on_event("startup")
async def startup_event():
    symbols = ["XAU/USD"]
    data_provider.add_callback(run_smc_analysis)
    
    async def init_market_and_connect():
        for sym in symbols:
            try:
                print(f"[{sym}] Pre-loading historical candles (M15, M5, M1)...")
                m15 = await asyncio.to_thread(data_provider.get_historical_data, sym, interval="15min", use_csv=False)
                m5 = await asyncio.to_thread(data_provider.get_historical_data, sym, interval="5min", use_csv=False)
                m1 = await asyncio.to_thread(data_provider.get_historical_data, sym, interval="1min", use_csv=False)
                if not hasattr(app.state, 'market_data'):
                    app.state.market_data = {}
                app.state.market_data[sym] = {"m1": m1, "m5": m5, "m15": m15, "ltf": m1, "htf": m15}
                print(f"[{sym}] Pre-load complete! ({len(m15 or [])} M15, {len(m5 or [])} M5, {len(m1 or [])} M1). Ready for live stream.")
            except Exception as e:
                print(f"[{sym}] Pre-load warning: {e}")
                
        # Connect to TwelveData WebSocket
        await data_provider.connect_websocket(symbols)
        
    asyncio.create_task(init_market_and_connect())

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
    historical_data = data_provider.get_historical_data(symbol, interval=interval, use_csv=False)
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
    min_tp_pips: Optional[float] = 120.0
    max_tp_pips: Optional[float] = 250.0
    max_sl_pips: Optional[float] = 90.0
    min_sl_pips: Optional[float] = 50.0
    be_trigger_pips: Optional[float] = 70.0
    partial_tp_enabled: Optional[bool] = True
    partial_tp_pips: Optional[float] = 100.0
    partial_tp_ratio: Optional[float] = 0.5
    max_daily_trades: Optional[int] = 2

@app.get("/api/settings")
async def get_settings():
    return settings_manager.load_settings()

@app.post("/api/settings")
async def update_settings(settings: SettingsModel):
    new_settings = {
        "account_balance": settings.account_balance,
        "risk_percentage": settings.risk_percentage,
        "min_tp_pips": settings.min_tp_pips,
        "max_tp_pips": settings.max_tp_pips,
        "max_sl_pips": settings.max_sl_pips,
        "min_sl_pips": settings.min_sl_pips,
        "be_trigger_pips": settings.be_trigger_pips,
        "partial_tp_enabled": settings.partial_tp_enabled,
        "partial_tp_pips": settings.partial_tp_pips,
        "partial_tp_ratio": settings.partial_tp_ratio,
        "max_daily_trades": settings.max_daily_trades
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

@app.get("/api/dashboard")
async def get_dashboard_data():
    stats = db.get_statistics()
    historical = db.get_historical_signals(limit=1000)
    
    recent_trades = []
    equity_curve = []
    current_equity = 0.0
    
    historical_reversed = reversed(historical)
    peak = 0
    max_dd = 0
    
    for h in historical_reversed:
        if h.get('status') in ['WIN', 'LOSS', 'PARTIAL_WIN']:
            pnl = h.get('pnl', 0.0)
            if pnl is None: pnl = 0.0
            current_equity += pnl
            equity_curve.append({
                "time": h.get("timestamp"),
                "equity": current_equity
            })
            
            if current_equity > peak:
                peak = current_equity
            dd = peak - current_equity
            if dd > max_dd:
                max_dd = dd
                
    for h in historical[:100]:
        if h.get('status') in ['WIN', 'LOSS', 'PARTIAL_WIN']:
            recent_trades.append({
                "time": h.get("timestamp"),
                "type": h.get("type", ""),
                "status": h.get("status"),
                "pnl": round(h.get("pnl", 0.0) or 0.0, 2)
            })
            
    active_signals = []
    for t in trade_manager.tracked_trades:
        active_signals.append({
            "time": t.get("timestamp"),
            "type": t.get("type", ""),
            "status": t.get("status"),
            "entry": t.get("entry", 0),
            "sl": t.get("sl", 0),
            "tp": t.get("tp", 0)
        })
        
    dashboard_data = {
        "summary": {
            "total_trades": stats.get("total_trades", 0),
            "wins": stats.get("wins", 0),
            "losses": stats.get("losses", 0),
            "win_rate": stats.get("win_rate", 0),
            "total_pnl": current_equity,
            "max_drawdown": max_dd
        },
        "equity_curve": equity_curve,
        "recent_trades": recent_trades,
        "active_signals": active_signals
    }
    return dashboard_data

# Mount frontend (works both locally and in cloud container)
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if not os.path.exists(frontend_dir):
    frontend_dir = "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
