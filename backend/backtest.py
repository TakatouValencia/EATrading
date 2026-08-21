import asyncio
from data_provider import DataProvider
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
from trade_manager import TradeManager
import json
from dotenv import load_dotenv

load_dotenv()

class DummyDB:
    def __init__(self):
        self.blacklisted_zones = set()

    def update_signal_status(self, *args, **kwargs): pass
    def save_signal(self, signal): return {"id": 1}
    def get_historical_signals(self, limit=50): return []
    def save_blacklisted_zone(self, symbol, signature, invalidated_at):
        self.blacklisted_zones.add(signature)
    def get_blacklisted_zones(self, symbol=None):
        return self.blacklisted_zones

async def run_backtest():
    print("Mulai Backtest XAU/USD (M1 & M15)...")
    dp = DataProvider()
    sg = SignalGenerator(cooldown_minutes=15)
    db = DummyDB()
    tm = TradeManager(db)
    backtest_stats = {
        "WIN": 0, "LOSS": 0, "PARTIAL_WIN": 0, "CANCELLED": 0, "pnl": 0.0, 
        "ZONE_BLACKLISTED": 0, "RANGING_MARKET": 0,
        "LIMIT_WIN": 0, "LIMIT_LOSS": 0, "LIMIT_PARTIAL": 0, "LIMIT_PNL": 0.0,
        "CONF_WIN": 0, "CONF_LOSS": 0, "CONF_PARTIAL": 0, "CONF_PNL": 0.0,
        "TOTAL_LIMIT": 0, "TOTAL_CONF": 0
    }
    
    def on_close(trade, status, pnl):
        backtest_stats[status] = backtest_stats.get(status, 0) + 1
        backtest_stats["pnl"] += pnl
        
        # Track by signal type
        st = trade.get('signal_type', 'CONFIRMED')
        if status == 'WIN':
            if st == 'LIMIT': backtest_stats['LIMIT_WIN'] += 1
            else: backtest_stats['CONF_WIN'] += 1
        elif status == 'LOSS':
            if st == 'LIMIT': backtest_stats['LIMIT_LOSS'] += 1
            else: backtest_stats['CONF_LOSS'] += 1
        elif status == 'PARTIAL_WIN':
            if st == 'LIMIT': backtest_stats['LIMIT_PARTIAL'] += 1
            else: backtest_stats['CONF_PARTIAL'] += 1
            
        if status != 'CANCELLED':
            if st == 'LIMIT': backtest_stats['LIMIT_PNL'] += pnl
            else: backtest_stats['CONF_PNL'] += pnl
        
    tm.on_trade_closed = on_close

    symbol = "XAU/USD"
    # Ambil data maksimum yang diizinkan (maksimal 5000 untuk Twelve Data API, kita ambil 2000 untuk performa)
    print("Mengambil data riwayat...")
    df_h4 = dp.get_historical_data(symbol, interval="4h", outputsize=None)
    df_h1 = dp.get_historical_data(symbol, interval="1h", outputsize=None)
    df_htf = dp.get_historical_data(symbol, interval="15min", outputsize=None)
    df_ltf = dp.get_historical_data(symbol, interval="1min", outputsize=None)
    
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
        adx_h1 = 25.0
        if len(current_h1_data) >= 50:
            engine_h1 = SMCEngine(current_h1_data[-window_size:])
            h1_events = engine_h1.detect_bos_choch()
            adx_h1 = engine_h1.calculate_adx(period=14)
            if h1_events:
                h1_trend = "BULLISH" if "BULLISH" in h1_events[-1]['type'] else "BEARISH"
                
        # Analisa H4
        current_h4_data = [c for c in df_h4 if c['timestamp'] <= current_time]
        h4_trend = None
        adx_h4 = 25.0
        if len(current_h4_data) >= 50:
            engine_h4 = SMCEngine(current_h4_data[-window_size:])
            h4_events = engine_h4.detect_bos_choch()
            adx_h4 = engine_h4.calculate_adx(period=14)
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
        
        # Disable real-time cooldowns during backtest
        sg.cooldown_minutes = 0
        
        # Delay to avoid hitting rate limits when calling real LLM API
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
            engine_ltf=engine_ltf,
            db=db,
            adx_h1=adx_h1,
            adx_h4=adx_h4
        )
        
        if signal and signal.get('status') == 'SKIPPED':
            reason = signal.get('reasons', [''])[0]
            if "Blacklisted Zone" in reason:
                backtest_stats["ZONE_BLACKLISTED"] += 1
            elif "Ranging Market" in reason:
                backtest_stats["RANGING_MARKET"] += 1
            continue
            
        if signal and not tm.has_active_trade(symbol):
            signal['id'] = total_signals + 1
            if signal.get('status') == "REJECTED":
                print(f"\n[-] LLM REJECTED PADA: {current_time} | Tren: {htf_trend} | Reason: {signal.get('reasons')[-1]}")
            else:
                st = signal.get('signal_type', 'CONFIRMED')
                if st == 'LIMIT':
                    backtest_stats['TOTAL_LIMIT'] += 1
                else:
                    backtest_stats['TOTAL_CONF'] += 1
                print(f"\n[+] {st} SIGNAL PADA: {current_time} | Tren: {htf_trend} | Entry: {signal['entry']} | SL: {signal['sl']} | TP: {signal['tp']}")
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
    print(f"Total Evaluasi          : {total_signals}")
    
    full_win = backtest_stats.get("WIN", 0)
    partial_win = backtest_stats.get("PARTIAL_WIN", 0)
    loss = backtest_stats.get("LOSS", 0)
    cancelled = backtest_stats.get("CANCELLED", 0)
    rejected = len([t for t in tm.tracked_trades if t.get('status') == 'REJECTED' or t.get('grade') == 'REJECTED_BY_LLM'])
    
    open_trades = len([t for t in tm.tracked_trades if t['status'] in ('PENDING', 'ACTIVE')])
    total_unique_approved = full_win + partial_win + loss + cancelled + open_trades
    
    total_closed = full_win + partial_win + loss
    
    print(f"Unique Approved Setups  : {total_unique_approved}")
    print(f"LLM Rejections          : {rejected}")
    print(f"Skipped (Blacklisted POI): {backtest_stats.get('ZONE_BLACKLISTED', 0)}")
    print(f"Skipped (Ranging Market) : {backtest_stats.get('RANGING_MARKET', 0)}")
    print(f"Cancelled / Missed      : {cancelled}")
    print("-" * 50)
    print(f"Total LIMIT Signals     : {backtest_stats['TOTAL_LIMIT']}")
    print(f"Total CONFIRMED Signals : {backtest_stats['TOTAL_CONF']}")
    print("-" * 50)
    
    # LIMIT Stats
    l_win = backtest_stats['LIMIT_WIN']
    l_loss = backtest_stats['LIMIT_LOSS']
    l_part = backtest_stats['LIMIT_PARTIAL']
    l_closed = l_win + l_loss + l_part
    l_wr = ((l_win + l_part) / l_closed * 100) if l_closed > 0 else 0
    l_pnl_str = f"+{backtest_stats['LIMIT_PNL']:.2f}" if backtest_stats['LIMIT_PNL'] > 0 else f"{backtest_stats['LIMIT_PNL']:.2f}"
    
    print(f"[LIMIT STATS]")
    print(f"Wins: {l_win} | Partial: {l_part} | Losses: {l_loss}")
    print(f"Win Rate: {l_wr:.2f}% | PNL: {l_pnl_str} R")
    print("-" * 50)
    
    # CONFIRMED Stats
    c_win = backtest_stats['CONF_WIN']
    c_loss = backtest_stats['CONF_LOSS']
    c_part = backtest_stats['CONF_PARTIAL']
    c_closed = c_win + c_loss + c_part
    c_wr = ((c_win + c_part) / c_closed * 100) if c_closed > 0 else 0
    c_pnl_str = f"+{backtest_stats['CONF_PNL']:.2f}" if backtest_stats['CONF_PNL'] > 0 else f"{backtest_stats['CONF_PNL']:.2f}"

    print(f"[CONFIRMED STATS]")
    print(f"Wins: {c_win} | Partial: {c_part} | Losses: {c_loss}")
    print(f"Win Rate: {c_wr:.2f}% | PNL: {c_pnl_str} R")
    print("-" * 50)
    
    # Overall Output
    print(f"[OVERALL STATS]")
    print(f"Full Wins (+TP)         : {full_win}")
    print(f"Partial Wins (+1R to BE): {partial_win}")
    print(f"Losses Murni (-1R)      : {loss}")
    print(f"Floating / Open         : {open_trades}")
    
    if total_closed > 0:
        wr = ((full_win + partial_win) / total_closed) * 100
        print(f"Overall Win Rate (WR)   : {wr:.2f}%")
    else:
        print("Overall Win Rate (WR)   : 0.00% (Belum ada trade tertutup)")
        
    pnl_str = f"+{backtest_stats['pnl']:.2f}" if backtest_stats['pnl'] > 0 else f"{backtest_stats['pnl']:.2f}"
    print(f"Total Overall PNL       : {pnl_str} R")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(run_backtest())
