import asyncio
from data_provider import DataProvider
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
from trade_manager import TradeManager
import json
from datetime import datetime

class DummyDB:
    def update_signal_status(self, *args, **kwargs): pass
    def save_signal(self, signal): return {"id": signal.get('id', 1)}
    def get_historical_signals(self, limit=50): return []
    def save_blacklisted_zone(self, *args, **kwargs): pass
    def get_blacklisted_zones(self, *args, **kwargs): return set()

async def run_audit():
    print("Mulai Audit Backtest XAU/USD (M1 & M15)...")
    dp = DataProvider()
    sg = SignalGenerator(cooldown_minutes=15)
    db = DummyDB()
    tm = TradeManager(db)
    backtest_stats = {"WIN": 0, "LOSS": 0, "PARTIAL_WIN": 0, "pnl": 0.0}
    
    # Audit tracking
    completed_trades = []
    
    def on_close(trade, status, pnl):
        backtest_stats[status] = backtest_stats.get(status, 0) + 1
        backtest_stats["pnl"] += pnl
        
        completed_trades.append({
            "trade": trade,
            "status": status,
            "pnl": pnl
        })
        
    tm.on_trade_closed = on_close

    symbol = "XAU/USD"
    print("Mengambil data riwayat...")
    df_h4 = dp.get_historical_data(symbol, interval="4h", outputsize=2000)
    df_h1 = dp.get_historical_data(symbol, interval="1h", outputsize=2000)
    df_htf = dp.get_historical_data(symbol, interval="15min", outputsize=2000)
    df_ltf = dp.get_historical_data(symbol, interval="1min", outputsize=2000)
    
    if not df_htf or not df_ltf or not df_h1 or not df_h4:
        print("Gagal mengambil data!")
        return

    window_size = 500
    total_signals = 0
    
    print("Menjalankan simulasi audit...\n")
    
    for i in range(window_size, len(df_ltf), 5):
        current_ltf_data = df_ltf[i-window_size:i]
        current_candle = current_ltf_data[-1]
        current_time = current_candle['timestamp']
        current_price = current_candle['close']
        
        current_htf_data = [c for c in df_htf if c['timestamp'] <= current_time]
        if len(current_htf_data) < 50: continue
        current_htf_data = current_htf_data[-window_size:]
        
        engine_htf = SMCEngine(current_htf_data)
        htf_events = engine_htf.detect_bos_choch()
        htf_trend = "BULLISH" if htf_events and "BULLISH" in htf_events[-1]['type'] else "BEARISH" if htf_events else None
                
        current_h1_data = [c for c in df_h1 if c['timestamp'] <= current_time]
        h1_trend = "BULLISH" if (len(current_h1_data) >= 50 and SMCEngine(current_h1_data[-window_size:]).detect_bos_choch() and "BULLISH" in SMCEngine(current_h1_data[-window_size:]).detect_bos_choch()[-1]['type']) else "BEARISH"
                
        current_h4_data = [c for c in df_h4 if c['timestamp'] <= current_time]
        h4_trend = "BULLISH" if (len(current_h4_data) >= 50 and SMCEngine(current_h4_data[-window_size:]).detect_bos_choch() and "BULLISH" in SMCEngine(current_h4_data[-window_size:]).detect_bos_choch()[-1]['type']) else "BEARISH"
                
        engine_ltf = SMCEngine(current_ltf_data)
        
        sg.client = None
        sg.cooldown_minutes = 0
        
        signal = await sg.evaluate_confluence(
            symbol=symbol,
            current_price=current_price,
            events=engine_ltf.detect_bos_choch(),
            obs=engine_ltf.detect_order_blocks(engine_ltf.detect_bos_choch()),
            fvgs=engine_ltf.detect_fvg(),
            sweeps=engine_ltf.detect_liquidity_sweeps(),
            htf_trend=htf_trend,
            h1_trend=h1_trend,
            h4_trend=h4_trend,
            snr_zones=engine_ltf.detect_support_resistance(),
            snd_zones=engine_ltf.detect_supply_demand(),
            pd_zones=engine_ltf.detect_premium_discount(),
            breakers=engine_ltf.detect_breaker_blocks(engine_ltf.detect_bos_choch()),
            fibo_ote=engine_ltf.detect_fibo_ote(),
            poc_price=engine_ltf.calculate_volume_profile(lookback=100),
            amd_setups=engine_ltf.detect_amd(),
            atr=engine_ltf.calculate_atr(period=14),
            reversal_patterns=engine_ltf.detect_reversal_patterns(),
            engine_ltf=engine_ltf
        )
        
        if signal and not tm.has_active_trade(symbol):
            signal['id'] = total_signals + 1
            signal['generated_time'] = current_time
            tm.add_trade(signal)
            total_signals += 1
            
        for step_candle in df_ltf[i:min(i+5, len(df_ltf))]:
            o = step_candle['open']
            h = step_candle['high']
            l = step_candle['low']
            c = step_candle['close']
            
            ticks = [o]
            if c >= o:
                ticks.extend([l, h])
            else:
                ticks.extend([h, l])
            ticks.append(c)
            
            for t in ticks:
                await tm.process_tick({'symbol': symbol, 'price': t})

    print("\n" + "="*50)
    print("HASIL AUDIT BACKTEST (XAU/USD - Multi-Timeframe M1, M15, H1, H4)")
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
    
    print("\n--- ANALISIS SL & HOLDING DURATION ---")
    if total_closed > 0:
        total_sl_dist = 0
        valid_trades_for_dist = 0
        for ct in completed_trades:
            trade = ct['trade']
            if 'sl' in trade and 'entry' in trade:
                dist = abs(trade['entry'] - trade['sl'])
                total_sl_dist += dist
                valid_trades_for_dist += 1
        avg_sl = (total_sl_dist / valid_trades_for_dist) if valid_trades_for_dist > 0 else 0
        print(f"Rata-rata jarak SL : {avg_sl:.2f} poin")
    
    print("\n--- 5 CONTOH TRADE DETAIL ---")
    count = 0
    for ct in completed_trades:
        if ct['status'] in ['WIN', 'PARTIAL_WIN', 'LOSS']:
            trade = ct['trade']
            print(f"Trade #{trade.get('id')} - {trade['type']} @ {trade['entry']}")
            print(f"  Generated : {trade.get('generated_time')}")
            print(f"  SL: {trade['sl']} | TP: {trade['tp']}")
            print(f"  Status Akhir : {ct['status']} ({ct['pnl']} R)")
            print(f"  Konfirmasi : {', '.join(trade.get('reasons', []))}")
            print("-" * 40)
            count += 1
            if count >= 5:
                break

if __name__ == "__main__":
    asyncio.run(run_audit())
