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
    backtest_stats = {
        "IS": {"WIN": 0, "LOSS": 0, "PARTIAL_WIN": 0, "pnl": 0.0},
        "OOS": {"WIN": 0, "LOSS": 0, "PARTIAL_WIN": 0, "pnl": 0.0}
    }
    
    def on_close(trade, status, pnl):
        phase = trade.get('phase', 'IS')
        backtest_stats[phase][status] = backtest_stats[phase].get(status, 0) + 1
        backtest_stats[phase]["pnl"] += pnl
        
    tm.on_trade_closed = on_close

    symbol = "XAU/USD"
    print("Mengambil data riwayat 60 Hari (M15)...")
    # Karena M15, 60 hari = ~4000 candle
    df_h4 = dp.get_historical_data(symbol, interval="1d", outputsize=100)
    df_h1 = dp.get_historical_data(symbol, interval="4h", outputsize=500)
    df_htf = dp.get_historical_data(symbol, interval="1h", outputsize=2000)
    df_ltf = dp.get_historical_data(symbol, interval="15min", outputsize=4000)
    
    if not df_htf or not df_ltf or not df_h1 or not df_h4:
        print("Gagal mengambil data!")
        return

    print(f"Data terkumpul: {len(df_h4)} candle 1D, {len(df_h1)} candle 4H, {len(df_htf)} candle 1H, {len(df_ltf)} candle M15.")
    
    # Split Data: 50% In-Sample, 50% Out-of-Sample
    split_idx = len(df_ltf) // 2
    
    window_size = 200 # ukuran window untuk analisa SMC (dikurangi agar lebih cepat di M15)
    total_signals_in = 0
    total_signals_out = 0
    
    # Cost Parameters
    spread_points = 3.0 # 30 pips
    slippage_points = 1.0 # 10 pips
    
    print("Menjalankan simulasi Walk-Forward (ini mungkin memakan waktu beberapa menit)...")
    
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
            is_out_of_sample = i >= split_idx
            
            # Tambahkan slippage ke Entry (lebih buruk dari ideal)
            if "BUY" in signal['type']:
                signal['entry'] += spread_points  # Buy at Ask price
            else:
                signal['entry'] -= spread_points  # Sell at Bid price
                
            # Tambahkan slippage ke SL (eksekusi SL selalu lebih buruk saat volatility)
            if "BUY" in signal['type']:
                signal['sl'] -= slippage_points
            else:
                signal['sl'] += slippage_points
                
            signal['id'] = total_signals_in + total_signals_out + 1
            signal['phase'] = "OOS" if is_out_of_sample else "IS"
            print(f"\n[{signal['phase']}] SIGNAL PADA: {current_time} | Tren: {htf_trend} | Entry: {signal['entry']:.1f} | SL: {signal['sl']:.1f} | TP: {signal['tp']:.1f}")
            tm.add_trade(signal)
            
            if is_out_of_sample:
                total_signals_out += 1
            else:
                total_signals_in += 1
            
        for step_candle in df_ltf[i:min(i+2, len(df_ltf))]:
            # Simulate Ask price for Buy TP/SL and Bid price for Sell TP/SL (approximated by adding/subtracting spread from high/low)
            await tm.process_tick({'symbol': symbol, 'price': step_candle['low']})
            await tm.process_tick({'symbol': symbol, 'price': step_candle['high']})
            await tm.process_tick({'symbol': symbol, 'price': step_candle['close']})

    print("\n" + "="*50)
    print("HASIL OUT-OF-SAMPLE BACKTEST (M15, 60 Hari)")
    print("Cost: Spread 3.0 pts (30 pips), Slippage 1.0 pts (10 pips)")
    print("="*50)
    print(f"Total Sinyal (In-Sample)   : {total_signals_in}")
    print(f"Total Sinyal (Out-Sample)  : {total_signals_out}")
    
    def print_stats(phase_name, phase_key, total_sig):
        win = backtest_stats[phase_key]["WIN"] + backtest_stats[phase_key]["PARTIAL_WIN"]
        loss = backtest_stats[phase_key]["LOSS"]
        total_closed = win + loss
        
        print(f"\n--- {phase_name} ---")
        print(f"Total Sinyal            : {total_sig}")
        print(f"Wins (termasuk Partial) : {win}")
        print(f"Losses                  : {loss}")
        
        if total_closed > 0:
            wr = (win / total_closed) * 100
            print(f"Win Rate (WR)           : {wr:.2f}%")
        else:
            print("Win Rate (WR)           : 0.00% (Belum ada trade tertutup)")
            
        print(f"Total PNL (Estimasi RR) : {backtest_stats[phase_key]['pnl']:.2f} R")

    print_stats("IN-SAMPLE (Bulan 1)", "IS", total_signals_in)
    print_stats("OUT-OF-SAMPLE (Bulan 2)", "OOS", total_signals_out)
    print("="*50)

if __name__ == "__main__":
    asyncio.run(run_backtest())
