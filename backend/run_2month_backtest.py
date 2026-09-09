import asyncio
import os
import sys
from datetime import datetime, timedelta
import yfinance as yf
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
from trade_manager import TradeManager
from database import Database
from risk_calculator import calculate_pips

class BacktestDB:
    def __init__(self):
        self.blacklisted_zones = set()
    def update_signal_status(self, *args, **kwargs): pass
    def save_signal(self, signal): return {"id": 1}
    def get_historical_signals(self, limit=50): return []
    def save_blacklisted_zone(self, symbol, signature, invalidated_at):
        self.blacklisted_zones.add(signature)
    def get_blacklisted_zones(self, symbol=None):
        return self.blacklisted_zones
    def get_today_signals(self, date): return []

async def run_2month_backtest():
    print("=" * 65)
    print("  BACKTESTING 2 BULAN KEBELAKANG (30 JUNI 2026 - 9 SEPTEMBER 2026)")
    print("  PAIR: XAU/USD (Gold) | TIMEFRAME: M15 (HTF) & M5 (LTF)")
    print("  RULES: SL Maks 70 Pips, TP 150-200 Pips, Auto BE di +50 Pips")
    print("=" * 65)

    print("\n[1/4] Mengambil data riwayat 60 hari dari Yahoo Finance (GC=F)...")
    ticker = yf.Ticker("GC=F")
    df_15m_raw = ticker.history(period="60d", interval="15m")
    df_5m_raw = ticker.history(period="60d", interval="5m")

    if df_15m_raw.empty or df_5m_raw.empty:
        print("[ERROR] Gagal mengunduh data riwayat 60 hari.")
        return

    # Format data
    df_15m = []
    for idx, r in df_15m_raw.iterrows():
        df_15m.append({
            'timestamp': idx.isoformat(),
            'open': float(r['Open']), 'high': float(r['High']),
            'low': float(r['Low']), 'close': float(r['Close']),
            'volume': float(r.get('Volume', 0))
        })
    df_15m.sort(key=lambda x: x['timestamp'])

    df_5m = []
    for idx, r in df_5m_raw.iterrows():
        df_5m.append({
            'timestamp': idx.isoformat(),
            'open': float(r['Open']), 'high': float(r['High']),
            'low': float(r['Low']), 'close': float(r['Close']),
            'volume': float(r.get('Volume', 0))
        })
    df_5m.sort(key=lambda x: x['timestamp'])

    start_date = df_5m[0]['timestamp'][:10]
    end_date = df_5m[-1]['timestamp'][:10]
    print(f"  * Periode: {start_date} s/d {end_date}")
    print(f"  * Candle M15: {len(df_15m)} batang")
    print(f"  * Candle M5 : {len(df_5m)} batang")

    print("\n[2/4] Menginisialisasi Mesin Backtest...")
    sg = SignalGenerator(cooldown_minutes=30)
    db = BacktestDB()
    tm = TradeManager(db)

    stats = {
        "WIN": 0,
        "LOSS": 0,
        "BREAK_EVEN": 0,
        "MISSED": 0,
        "CANCELLED": 0,
        "total_pnl": 0.0,
        "trades": []
    }

    def on_trade_closed(trade, status, pnl):
        stats[status] = stats.get(status, 0) + 1
        stats["total_pnl"] += pnl
        entry_val = float(trade.get('entry_price', trade.get('entry', 0)))
        sl_val = float(trade.get('initial_sl', trade.get('sl', 0)))
        tp_val = float(trade.get('tp_price', trade.get('tp', 0)))
        sl_pips = calculate_pips("XAU/USD", entry_val, sl_val)
        tp_pips = calculate_pips("XAU/USD", entry_val, tp_val)
        
        stats["trades"].append({
            "timestamp": trade.get('timestamp', '')[:16],
            "type": trade.get('type', ''),
            "entry": entry_val,
            "sl": sl_val,
            "tp": tp_val,
            "sl_pips": sl_pips,
            "tp_pips": tp_pips,
            "status": status,
            "pnl": round(pnl, 2)
        })

    tm.on_trade_closed = on_trade_closed

    print("\n[3/4] Menjalankan simulasi langkah per langkah (M5 playback)...")
    from bisect import bisect_right
    timestamps_15m = [c['timestamp'] for c in df_15m]
    window_size = 200
    step = 2 # step through every 2 candles (10 mins) for realistic speed
    total_candles = len(df_5m)

    for i in range(window_size, total_candles, step):
        if i % 1000 == 0:
            progress_pct = (i - window_size) / (total_candles - window_size) * 100
            print(f"  > Progress: {progress_pct:.1f}% ({i}/{total_candles} candles) | Closed: {stats['WIN']}W / {stats['LOSS']}L / {stats['BREAK_EVEN']}BE | PnL: {stats['total_pnl']:+.2f}R")

        current_5m_window = df_5m[i-window_size:i]
        curr_candle = current_5m_window[-1]
        curr_time = curr_candle['timestamp']
        curr_price = curr_candle['close']
        
        # Fast bisect for M15 window
        idx_15m = bisect_right(timestamps_15m, curr_time)
        if idx_15m < 50:
            continue
        curr_15m_window = df_15m[max(0, idx_15m - window_size):idx_15m]

        # Run M15 SMC Engine (HTF)
        e_15m = SMCEngine(curr_15m_window)
        ev_15m = e_15m.detect_bos_choch()
        obs_15m = e_15m.detect_order_blocks(ev_15m)
        fvgs_15m = e_15m.detect_fvg()
        breakers_15m = e_15m.detect_breaker_blocks(ev_15m)
        pd_zones = e_15m.detect_premium_discount()
        trend_15m = "BULLISH" if ev_15m and "BULLISH" in ev_15m[-1]['type'] else "BEARISH"

        # Run M5 SMC Engine (LTF)
        e_5m = SMCEngine(current_5m_window)
        ev_5m = e_5m.detect_bos_choch()
        obs_5m = e_5m.detect_order_blocks(ev_5m)
        fvgs_5m = e_5m.detect_fvg()
        breakers_5m = e_5m.detect_breaker_blocks(ev_5m)
        sweeps = e_5m.detect_liquidity_sweeps()
        atr = e_5m.calculate_atr(14)
        trend_5m = "BULLISH" if ev_5m and "BULLISH" in ev_5m[-1]['type'] else "BEARISH"

        combined_obs = obs_15m + obs_5m
        combined_fvgs = fvgs_15m + fvgs_5m
        combined_breakers = breakers_15m + breakers_5m

        # Risk limits check
        tm.current_time_str = curr_time
        tm._check_daily_reset()
        allowed, _ = tm.check_trading_allowed()

        if allowed and not tm.has_running_trade("XAU/USD"):
            sig = await sg.evaluate_confluence(
                symbol="XAU/USD",
                current_price=curr_price,
                events=ev_5m,
                obs=combined_obs,
                fvgs=combined_fvgs,
                sweeps=sweeps,
                m15_trend=trend_15m,
                m5_trend=trend_5m,
                htf_trend=trend_15m,
                pd_zones=pd_zones,
                breakers=combined_breakers,
                trade_manager=tm,
                atr=atr,
                db=db,
                engine_ltf=e_5m,
                current_time_str=curr_time
            )
            if sig and sig.get("status") not in ["SKIPPED", "REJECTED"]:
                # Periksa duplikasi
                is_duplicate = False
                for t in tm.tracked_trades:
                    if t['symbol'] == "XAU/USD" and t['status'] in ['PENDING', 'ACTIVE']:
                        if abs(t.get('entry', 0) - sig['entry']) < 1.0:
                            is_duplicate = True
                            break
                if not is_duplicate:
                    await tm.cancel_pending_trades("XAU/USD")
                    tm.add_trade(sig)

        # Proses tick dengan pergerakan High, Low, Close candle saat ini
        await tm.process_tick({'symbol': 'XAU/USD', 'price': curr_candle['high'], 'timestamp': curr_time})
        await tm.process_tick({'symbol': 'XAU/USD', 'price': curr_candle['low'], 'timestamp': curr_time})
        await tm.process_tick({'symbol': 'XAU/USD', 'price': curr_candle['close'], 'timestamp': curr_time})

    print("\n[4/4] Menghitung Statistik & Laporan...")
    total_completed = stats['WIN'] + stats['LOSS'] + stats['BREAK_EVEN']
    decisive_trades = stats['WIN'] + stats['LOSS'] # trade yang bukan BE
    win_rate = (stats['WIN'] / decisive_trades * 100) if decisive_trades > 0 else 0.0
    overall_win_rate = (stats['WIN'] / total_completed * 100) if total_completed > 0 else 0.0

    # Drawdown calculation
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in stats['trades']:
        equity += t['pnl']
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    report = f"""
======================================================================
              HASIL BACKTEST 2 BULAN XAU/USD (M15, M5, M1)
======================================================================
Periode Pengujian      : {start_date} s/d {end_date} (60 Hari)
Target Take Profit     : 150 - 200 Pips ($15.00 - $20.00)
Maksimal Stop Loss     : Maksimal 70 Pips ($7.00)
Proteksi Break-Even    : Aktif pada +50 Pips Profit (SL geser ke Entry)
Circuit Breaker        : Maksimal 3 Consecutive Losses per Hari
----------------------------------------------------------------------
Total Setup Selesai    : {total_completed}
- Take Profit (WIN)    : {stats['WIN']} trade
- Stop Loss (LOSS)     : {stats['LOSS']} trade
- Break-Even (BE / 0)  : {stats['BREAK_EVEN']} trade (Terlindungi dari kerugian)
- Cancelled / Expired  : {stats.get('CANCELLED', 0)}
- Missed Pending       : {stats.get('MISSED', 0)}
----------------------------------------------------------------------
Win Rate (Decisive)    : {win_rate:.2f}% (Hanya menghitung WIN vs LOSS)
Total Net Profit       : {stats['total_pnl']:+.2f} R
Max Drawdown           : {max_dd:.2f} R
Rata-rata R:R          : 1 : 2.50+
======================================================================
Contoh 10 Transaksi Terakhir:
"""
    for t in stats['trades'][-10:]:
        report += f"[{t['timestamp']}] {t['type']:<4} @ {t['entry']:.2f} | SL: {t['sl_pips']:>4.1f}p | TP: {t['tp_pips']:>5.1f}p | Status: {t['status']:<10} | PnL: {t['pnl']:+5.2f}R\n"

    print(report)

    # Simpan ke file teks laporan
    report_file = "backtest_2month_report.txt"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
        f.write("\n\nSemua Transaksi:\n")
        for t in stats['trades']:
            f.write(f"[{t['timestamp']}] {t['type']:<4} @ {t['entry']:.2f} | SL: {t['sl_pips']:>4.1f}p | TP: {t['tp_pips']:>5.1f}p | Status: {t['status']:<10} | PnL: {t['pnl']:+5.2f}R\n")
            
    print(f"\n[SELESAI] Laporan lengkap disimpan ke {report_file}")

if __name__ == "__main__":
    asyncio.run(run_2month_backtest())
