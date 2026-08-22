import asyncio
import os
import json
from dotenv import load_dotenv
from data_provider import DataProvider
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
from trade_manager import TradeManager
from database import Database

# Load env variables for LLM
load_dotenv()

async def run_pass(name, df_ltf, df_htf, df_h1, df_h4, split_idx, spread, slippage, use_llm, use_blacklist, dynamic_adx):
    # Initialize DB
    if use_blacklist:
        test_db_name = 'test_backtest.db'
        test_db_path = os.path.join(os.path.dirname(__file__), test_db_name)
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
        db = Database(db_name=test_db_name)
        # Ensure sqlite tables exist
        db._init_sqlite()
    else:
        class DummyDB:
            def update_signal_status(self, *args, **kwargs): pass
            def save_signal(self, signal): return {"id": 1}
            def get_historical_signals(self, limit=50): return []
            def save_blacklisted_zone(self, *args, **kwargs): pass
            def get_blacklisted_zones(self, *args, **kwargs): return set()
        db = DummyDB()
        
    sg = SignalGenerator(cooldown_minutes=0)
    if not use_llm:
        sg.client = None # bypass LLM
        
    tm = TradeManager(db)
    
    stats = {
        "IS": {"WIN": 0, "LOSS": 0, "pnl": 0.0, "current_consec_loss": 0, "max_consec_loss": 0, "peak_pnl": 0.0, "max_drawdown": 0.0},
        "OOS": {"WIN": 0, "LOSS": 0, "pnl": 0.0, "current_consec_loss": 0, "max_consec_loss": 0, "peak_pnl": 0.0, "max_drawdown": 0.0}
    }
    current_equity = 0.0
    
    def on_close(trade, status, pnl):
        nonlocal current_equity
        if status not in ["WIN", "LOSS"]:
            return
        phase = trade.get('phase', 'IS')
        phase_stats = stats[phase]
        phase_stats[status] = phase_stats.get(status, 0) + 1
        phase_stats["pnl"] += pnl
        
        current_equity += pnl
        if status == "LOSS":
            phase_stats["current_consec_loss"] += 1
            if phase_stats["current_consec_loss"] > phase_stats["max_consec_loss"]:
                phase_stats["max_consec_loss"] = phase_stats["current_consec_loss"]
        else:
            phase_stats["current_consec_loss"] = 0
            
        if phase_stats["pnl"] > phase_stats["peak_pnl"]:
            phase_stats["peak_pnl"] = phase_stats["pnl"]
        
        current_dd = phase_stats["peak_pnl"] - phase_stats["pnl"]
        if current_dd > phase_stats["max_drawdown"]:
            phase_stats["max_drawdown"] = current_dd
            
    tm.on_trade_closed = on_close
    
    window_size = 200
    total_signals_in = 0
    total_signals_out = 0
    symbol = "XAU/USD"
    
    print(f"\n--- Memulai {name} ---")
    print(f"Parameter: Spread={spread}, Slippage={slippage}, LLM={use_llm}, Blacklist={use_blacklist}, Dynamic ADX={dynamic_adx}")
    
    for i in range(window_size, len(df_ltf), 5):
        current_ltf_data = df_ltf[i-window_size:i]
        current_candle = current_ltf_data[-1]
        current_time = current_candle['timestamp']
        current_price = current_candle['close']
        
        # fast htf slicing
        current_htf_data = [c for c in df_htf if c['timestamp'] <= current_time][-window_size:]
        current_h1_data = [c for c in df_h1 if c['timestamp'] <= current_time][-window_size:]
        current_h4_data = [c for c in df_h4 if c['timestamp'] <= current_time][-window_size:]
        
        if len(current_htf_data) < 50 or len(current_h1_data) < 50:
            continue
            
        # Analysis
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
        
        # HTF Trend
        htf_events = SMCEngine(current_htf_data).detect_bos_choch()
        htf_trend = "BULLISH" if htf_events and "BULLISH" in htf_events[-1]['type'] else "BEARISH" if htf_events else None
        
        h1_events = SMCEngine(current_h1_data).detect_bos_choch()
        h1_trend = "BULLISH" if h1_events and "BULLISH" in h1_events[-1]['type'] else "BEARISH" if h1_events else None
        
        h4_events = SMCEngine(current_h4_data).detect_bos_choch() if len(current_h4_data) >= 50 else []
        h4_trend = "BULLISH" if h4_events and "BULLISH" in h4_events[-1]['type'] else "BEARISH" if h4_events else None
        
        adx_h1 = 25.0
        adx_h4 = 25.0
        if dynamic_adx:
            adx_1 = SMCEngine(current_h1_data).calculate_adx(period=14)
            if adx_1: adx_h1 = adx_1
            if len(current_h4_data) >= 50:
                adx_4 = SMCEngine(current_h4_data).calculate_adx(period=14)
                if adx_4: adx_h4 = adx_4

        tm.current_time_str = current_time
        tm._check_daily_reset()
        
        if not tm.has_active_trade(symbol):
            allowed, _ = tm.check_trading_allowed()
            if allowed:
                # Get blacklisted zones if active
                if use_blacklist:
                    sg.blacklisted_zones = db.get_blacklisted_zones(symbol)
                else:
                    sg.blacklisted_zones = set()
                    
                # Evaluate confluence
                signal = await sg.evaluate_confluence(
                    symbol=symbol, current_price=current_price, events=events, obs=obs, fvgs=fvgs, sweeps=sweeps,
                    htf_trend=htf_trend, h1_trend=h1_trend, h4_trend=h4_trend, snr_zones=snr_zones, snd_zones=snd_zones,
                    pd_zones=pd_zones, breakers=breakers, fibo_ote=fibo_ote, poc_price=poc_price, amd_setups=amd_setups,
                    atr=atr, reversal_patterns=reversal_patterns, engine_ltf=engine_ltf, adx_h1=adx_h1, adx_h4=adx_h4
                )
                
                if signal and signal.get('status') != 'SKIPPED':
                    is_out_of_sample = i >= split_idx
                    if "BUY" in signal['type']:
                        signal['entry'] += spread
                        signal['sl'] -= slippage
                    else:
                        signal['entry'] -= spread
                        signal['sl'] += slippage
                        
                    signal['id'] = total_signals_in + total_signals_out + 1
                    signal['phase'] = "OOS" if is_out_of_sample else "IS"
                    tm.add_trade(signal)
                    
                    if is_out_of_sample: total_signals_out += 1
                    else: total_signals_in += 1
                    
        for step_candle in df_ltf[i:min(i+2, len(df_ltf))]:
            # Simulate Tick execution (low, high, close)
            # Send real simulated timestamps instead of relying on datetime.now()
            # Also to avoid instant 48h timeout because datetime.now() inside TradeManager vs tick time
            tick_time = step_candle['timestamp']
            await tm.process_tick({'symbol': symbol, 'price': step_candle['low'], 'timestamp': tick_time})
            await tm.process_tick({'symbol': symbol, 'price': step_candle['high'], 'timestamp': tick_time})
            await tm.process_tick({'symbol': symbol, 'price': step_candle['close'], 'timestamp': tick_time})
            
    return {
        "name": name,
        "IS": stats["IS"],
        "OOS": stats["OOS"],
        "total_in": total_signals_in,
        "total_out": total_signals_out
    }

async def main():
    print("Membaca data historis untuk Backtest Full Parity (60 Hari)...")
    dp = DataProvider()
    symbol = "XAU/USD"
    df_h4 = dp.get_historical_data(symbol, interval="1day", outputsize=100)
    df_h1 = dp.get_historical_data(symbol, interval="4h", outputsize=500)
    df_htf = dp.get_historical_data(symbol, interval="1h", outputsize=2000)
    df_ltf = dp.get_historical_data(symbol, interval="15min", outputsize=4000)
    
    if not df_htf or not df_ltf or not df_h1 or not df_h4:
        print("Gagal mengambil data!")
        return
        
    split_idx = len(df_ltf) // 2
    
    results = []
    
    # PASS A: Mekanikal murni, spread 30 pips (Baseline Lama)
    res_a = await run_pass("Pass A (Baseline 30 Pips)", df_ltf, df_htf, df_h1, df_h4, split_idx, 
                           spread=3.0, slippage=1.0, use_llm=False, use_blacklist=False, dynamic_adx=False)
    results.append(res_a)
    
    # PASS B: Mekanikal murni, spread realistis
    res_b = await run_pass("Pass B (Spread Realistis 3 Pips)", df_ltf, df_htf, df_h1, df_h4, split_idx, 
                           spread=0.3, slippage=0.1, use_llm=False, use_blacklist=False, dynamic_adx=False)
    results.append(res_b)
    
    # PASS C: Full safeguard aktif + spread realistis
    res_c = await run_pass("Pass C (Full Parity Live)", df_ltf, df_htf, df_h1, df_h4, split_idx, 
                           spread=0.3, slippage=0.1, use_llm=True, use_blacklist=True, dynamic_adx=True)
    results.append(res_c)
    
    report = []
    report.append("="*80)
    report.append("LAPORAN KOMPARASI BACKTEST SMC (60 Hari)")
    report.append("="*80)
    
    for r in results:
        report.append(f"\n[{r['name']}]")
        for phase in ["IS", "OOS"]:
            s = r[phase]
            tot = s['WIN'] + s['LOSS']
            wr = s['WIN'] / tot * 100 if tot > 0 else 0
            avg_r = s['pnl'] / s['WIN'] if s['WIN'] > 0 else 0
            sig = r['total_in'] if phase == "IS" else r['total_out']
            
            report.append(f"  {phase} (Signals: {sig} | Executed: {tot})")
            report.append(f"    Win Rate : {wr:.1f}% ({s['WIN']}W / {s['LOSS']}L)")
            report.append(f"    Total PnL: {s['pnl']:.2f} R")
            report.append(f"    Avg R/Win: {avg_r:.2f} R")
            report.append(f"    Max DD   : {s['max_drawdown']:.2f} R")
            report.append(f"    Max ConsL: {s['max_consec_loss']}")
            
    report_text = "\n".join(report)
    print("\n" + report_text)
    
    with open("backtest_comparison_report.txt", "w") as f:
        f.write(report_text)
        
    print("\nLaporan tersimpan di backtest_comparison_report.txt")

if __name__ == "__main__":
    asyncio.run(main())
