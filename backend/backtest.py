import asyncio
from data_provider import DataProvider
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
from trade_manager import TradeManager
import json

class DummyDB:
    def update_signal_status(self, *args, **kwargs): pass
    def save_signal(self, signal): return {"id": 1}
    def get_historical_signals(self, limit=50): return []

async def run_backtest():
    print("Mulai Backtest XAU/USD (M1 & M15)...")
    dp = DataProvider()
    sg = SignalGenerator(cooldown_minutes=15)
    db = DummyDB()
    tm = TradeManager(db)
    backtest_stats = {"WIN": 0, "LOSS": 0, "PARTIAL_WIN": 0, "pnl": 0.0}
    
    def on_close(trade, status, pnl):
        backtest_stats[status] = backtest_stats.get(status, 0) + 1
        backtest_stats["pnl"] += pnl
        
    tm.on_trade_closed = on_close

    symbol = "XAU/USD"
    # Ambil data maksimum yang diizinkan (maksimal 5000 untuk Twelve Data API, kita ambil 2000 untuk performa)
    print("Mengambil data riwayat...")
    df_h4 = dp.get_historical_data(symbol, interval="4h", outputsize=2000)
    df_h1 = dp.get_historical_data(symbol, interval="1h", outputsize=2000)
    df_htf = dp.get_historical_data(symbol, interval="15min", outputsize=2000)
    df_ltf = dp.get_historical_data(symbol, interval="1min", outputsize=2000)
    
    if not df_htf or not df_ltf or not df_h1 or not df_h4:
        print("Gagal mengambil data!")
        return

    print(f"Data terkumpul: {len(df_h4)} candle H4, {len(df_h1)} candle H1, {len(df_htf)} candle M15, {len(df_ltf)} candle M1.")
    
    window_size = 500 # ukuran window untuk analisa SMC
    total_signals = 0
    
    print("Menjalankan simulasi (ini mungkin memakan waktu beberapa menit)...")
    
    for i in range(window_size, len(df_ltf), 5):
        # Current time in backtest
        current_ltf_data = df_ltf[i-window_size:i]
        current_candle = current_ltf_data[-1]
        current_time = current_candle['timestamp']
        current_price = current_candle['close']
        
        # Cari data M15 yang valid (timestamp <= current_time)
        current_htf_data = [c for c in df_htf if c['timestamp'] <= current_time]
        if len(current_htf_data) < 50:
            continue
            
        current_htf_data = current_htf_data[-window_size:]
        
        # Analisa HTF M15
        engine_htf = SMCEngine(current_htf_data)
        htf_events = engine_htf.detect_bos_choch()
        htf_trend = None
        if htf_events:
            if "BULLISH" in htf_events[-1]['type']:
                htf_trend = "BULLISH"
            elif "BEARISH" in htf_events[-1]['type']:
                htf_trend = "BEARISH"
                
        # Analisa H1
        current_h1_data = [c for c in df_h1 if c['timestamp'] <= current_time]
        h1_trend = None
        if len(current_h1_data) >= 50:
            h1_events = SMCEngine(current_h1_data[-window_size:]).detect_bos_choch()
            if h1_events:
                h1_trend = "BULLISH" if "BULLISH" in h1_events[-1]['type'] else "BEARISH"
                
        # Analisa H4
        current_h4_data = [c for c in df_h4 if c['timestamp'] <= current_time]
        h4_trend = None
        if len(current_h4_data) >= 50:
            h4_events = SMCEngine(current_h4_data[-window_size:]).detect_bos_choch()
            if h4_events:
                h4_trend = "BULLISH" if "BULLISH" in h4_events[-1]['type'] else "BEARISH"
                
        # Analisa LTF M1
        engine_ltf = SMCEngine(current_ltf_data)
        events = engine_ltf.detect_bos_choch()
        fvgs = engine_ltf.detect_fvg()
        obs = engine_ltf.detect_order_blocks(events)
        sweeps = engine_ltf.detect_liquidity_sweeps()
        snr_zones = engine_ltf.detect_support_resistance()
        snd_zones = engine_ltf.detect_supply_demand()
        pd_zones = engine_ltf.detect_premium_discount()
        breakers = engine_ltf.detect_breaker_blocks(events)
        fibo_ote = engine_ltf.detect_fibo_ote()
        poc_price = engine_ltf.calculate_volume_profile(lookback=100)
        amd_setups = engine_ltf.detect_amd()
        atr = engine_ltf.calculate_atr(period=14)
        reversal_patterns = engine_ltf.detect_reversal_patterns()
        
        # Bypass LLM by overriding self.client momentarily to avoid API limits and speed up
        sg.client = None
        # Disable real-time cooldowns during backtest
        sg.cooldown_minutes = 0
        
        # Evaluate confluence
        signal = await sg.evaluate_confluence(
            symbol=symbol,
            current_price=current_price,
            events=events,
            obs=obs,
            fvgs=fvgs,
            sweeps=sweeps,
            htf_trend=htf_trend,
            h1_trend=h1_trend,
            h4_trend=h4_trend,
            snr_zones=snr_zones,
            snd_zones=snd_zones,
            pd_zones=pd_zones,
            breakers=breakers,
            fibo_ote=fibo_ote,
            poc_price=poc_price,
            amd_setups=amd_setups,
            atr=atr,
            reversal_patterns=reversal_patterns,
            engine_ltf=engine_ltf
        )
        
        if signal and not tm.has_active_trade(symbol):
            signal['id'] = total_signals + 1
            print(f"\n[+] SIGNAL DITEMUKAN PADA: {current_time} | Tren: {htf_trend} | Entry: {signal['entry']} | SL: {signal['sl']} | TP: {signal['tp']}")
            tm.add_trade(signal) # Changed from add_signal to add_trade
            total_signals += 1
            
        for step_candle in df_ltf[i:min(i+5, len(df_ltf))]:
            o = step_candle['open']
            h = step_candle['high']
            l = step_candle['low']
            c = step_candle['close']
            
            await tm.process_tick({'symbol': symbol, 'price': o})
            if c >= o:
                # Bullish: Open -> Low -> High -> Close
                await tm.process_tick({'symbol': symbol, 'price': l})
                await tm.process_tick({'symbol': symbol, 'price': h})
            else:
                # Bearish: Open -> High -> Low -> Close
                await tm.process_tick({'symbol': symbol, 'price': h})
                await tm.process_tick({'symbol': symbol, 'price': l})
            await tm.process_tick({'symbol': symbol, 'price': c})

    print("\n" + "="*50)
    print("HASIL BACKTEST (XAU/USD - Multi-Timeframe M1, M15, H1, H4)")
    print("="*50)
    print(f"Total Sinyal            : {total_signals}")
    
    win = backtest_stats["WIN"] + backtest_stats["PARTIAL_WIN"]
    loss = backtest_stats["LOSS"]
    open_trades = len([t for t in tm.tracked_trades if t['status'] in ('PENDING', 'ACTIVE')])
    
    total_closed = win + loss
    
    print(f"Wins (termasuk Partial) : {win}")
    print(f"Losses murni            : {loss}")
    print(f"Floating / Open         : {open_trades}")
    
    if total_closed > 0:
        wr = (win / total_closed) * 100
        print(f"Win Rate (WR)           : {wr:.2f}%")
    else:
        print("Win Rate (WR)           : 0.00% (Belum ada trade tertutup)")
        
    pnl_str = f"+{backtest_stats['pnl']:.2f}" if backtest_stats['pnl'] > 0 else f"{backtest_stats['pnl']:.2f}"
    print(f"Total PNL (Estimasi RR) : {pnl_str} R")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(run_backtest())
