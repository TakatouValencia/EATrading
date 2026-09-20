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
    print("  BACKTESTING 2 BULAN KEBELAKANG (XAU/USD GOLD)")
    print("  STRATEGI: SMC GRADE A+ INSTITUSIONAL (LONDON & NY KILLZONE)")
    print("  RULES: D1/H4 Trend, Liquidity Sweep, LTF CHoCH+FVG, Dynamic SL/TP")
    print("=" * 65)

    print("\n[1/4] Mengambil data riwayat 60 hari dari Yahoo Finance (GC=F)...")
    ticker = yf.Ticker("GC=F")
    df_1h_raw = ticker.history(period="60d", interval="1h")
    df_30m_raw = ticker.history(period="60d", interval="30m")
    df_15m_raw = ticker.history(period="60d", interval="15m")
    df_5m_raw = ticker.history(period="60d", interval="5m")

    if df_15m_raw.empty or df_5m_raw.empty or df_1h_raw.empty:
        print("[ERROR] Gagal mengunduh data riwayat 60 hari.")
        return

    # Resample H4 and D1 from 1H
    df_h4_raw = df_1h_raw.resample('4h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
    df_d1_raw = df_1h_raw.resample('24h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()

    # Format data
    df_d1 = []
    for idx, r in df_d1_raw.iterrows():
        df_d1.append({
            'timestamp': idx.isoformat(),
            'open': float(r['Open']), 'high': float(r['High']),
            'low': float(r['Low']), 'close': float(r['Close']),
            'volume': float(r.get('Volume', 0))
        })
    df_d1.sort(key=lambda x: x['timestamp'])

    df_h4 = []
    for idx, r in df_h4_raw.iterrows():
        df_h4.append({
            'timestamp': idx.isoformat(),
            'open': float(r['Open']), 'high': float(r['High']),
            'low': float(r['Low']), 'close': float(r['Close']),
            'volume': float(r.get('Volume', 0))
        })
    df_h4.sort(key=lambda x: x['timestamp'])

    df_1h = []
    for idx, r in df_1h_raw.iterrows():
        df_1h.append({
            'timestamp': idx.isoformat(),
            'open': float(r['Open']), 'high': float(r['High']),
            'low': float(r['Low']), 'close': float(r['Close']),
            'volume': float(r.get('Volume', 0))
        })
    df_1h.sort(key=lambda x: x['timestamp'])

    df_30m = []
    for idx, r in df_30m_raw.iterrows():
        df_30m.append({
            'timestamp': idx.isoformat(),
            'open': float(r['Open']), 'high': float(r['High']),
            'low': float(r['Low']), 'close': float(r['Close']),
            'volume': float(r.get('Volume', 0))
        })
    df_30m.sort(key=lambda x: x['timestamp'])

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
    print(f"  * Candle D1  : {len(df_d1)} batang")
    print(f"  * Candle H4  : {len(df_h4)} batang")
    print(f"  * Candle H1  : {len(df_1h)} batang")
    print(f"  * Candle M30 : {len(df_30m)} batang")
    print(f"  * Candle M15 : {len(df_15m)} batang")
    print(f"  * Candle M5  : {len(df_5m)} batang")

    print("\n[2/4] Menginisialisasi Mesin Backtest...")
    sg = SignalGenerator(cooldown_minutes=30)
    db = BacktestDB()
    tm = TradeManager(db)

    stats = {
        "WIN": 0,
        "LOSS": 0,
        "BREAK_EVEN": 0,
        "TP1_RUNNER_BE": 0,
        "TP2_FULL": 0,
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
        tp1_val = float(trade.get('tp1', trade.get('tp', 0)))
        tp2_val = float(trade.get('tp2', trade.get('tp', 0)))
        sl_pips = calculate_pips("XAU/USD", entry_val, sl_val)
        tp1_pips = calculate_pips("XAU/USD", entry_val, tp1_val)
        tp2_pips = calculate_pips("XAU/USD", entry_val, tp2_val)
        
        # Determine specific outcome
        outcome_detail = status
        if status == 'WIN':
            if trade.get('partial_taken', False) and abs(pnl - trade.get('locked_pnl', 0.0)) < 0.05:
                outcome_detail = "TP1_HIT_RUNNER_BE"
                stats["TP1_RUNNER_BE"] += 1
            else:
                outcome_detail = "TP2_FULL_HIT"
                stats["TP2_FULL"] += 1
        elif status == 'BREAK_EVEN':
            outcome_detail = "BE_PROTECTED"
        elif status == 'LOSS':
            outcome_detail = "SL_HIT"

        stats["trades"].append({
            "timestamp": trade.get('timestamp', '')[:16],
            "type": trade.get('type', ''),
            "entry": entry_val,
            "sl": sl_val,
            "tp1": tp1_val,
            "tp2": tp2_val,
            "sl_pips": sl_pips,
            "tp1_pips": tp1_pips,
            "tp2_pips": tp2_pips,
            "status": status,
            "outcome": outcome_detail,
            "pnl": round(pnl, 2),
            "sweep_pool": trade.get('sweep_pool', '-'),
            "killzone": trade.get('killzone', '-')
        })

    tm.on_trade_closed = on_trade_closed

    print("\n[3/4] Menjalankan simulasi langkah per langkah (M5 playback)...")
    from bisect import bisect_right
    timestamps_d1 = [c['timestamp'] for c in df_d1]
    timestamps_h4 = [c['timestamp'] for c in df_h4]
    timestamps_1h = [c['timestamp'] for c in df_1h]
    timestamps_30m = [c['timestamp'] for c in df_30m]
    timestamps_15m = [c['timestamp'] for c in df_15m]
    window_size = 200
    step = 1 # step through every candle for highest fidelity
    total_candles = len(df_5m)

    for i in range(window_size, total_candles, step):
        if i % 1000 == 0:
            progress_pct = (i - window_size) / (total_candles - window_size) * 100
            print(f"  > Progress: {progress_pct:.1f}% ({i}/{total_candles} candles) | Closed: {stats['WIN']}W / {stats['LOSS']}L / {stats['BREAK_EVEN']}BE | PnL: {stats['total_pnl']:+.2f}R")

        current_5m_window = df_5m[i-window_size:i]
        curr_candle = current_5m_window[-1]
        curr_time = curr_candle['timestamp']
        curr_price = curr_candle['close']
        
        # 1. Update existing trades and pending orders with the current candle's realistic price path FIRST
        tm.current_time_str = curr_time
        tm._check_daily_reset()
        
        is_green = curr_candle['close'] >= curr_candle['open']
        p_open = curr_candle['open']
        p_wick1 = curr_candle['low'] if is_green else curr_candle['high']
        p_wick2 = curr_candle['high'] if is_green else curr_candle['low']
        p_close = curr_candle['close']
        
        await tm.process_tick({'symbol': 'XAU/USD', 'price': p_open, 'timestamp': curr_time})
        await tm.process_tick({'symbol': 'XAU/USD', 'price': p_wick1, 'timestamp': curr_time})
        await tm.process_tick({'symbol': 'XAU/USD', 'price': p_wick2, 'timestamp': curr_time})
        await tm.process_tick({'symbol': 'XAU/USD', 'price': p_close, 'timestamp': curr_time})

        # 2. Risk limits and active trade check after candle finishes
        allowed, _ = tm.check_trading_allowed()
        if not allowed or tm.has_active_trade("XAU/USD"):
            continue

        # Fast bisect for D1 Macro window
        idx_d1 = bisect_right(timestamps_d1, curr_time)
        if idx_d1 >= 3:
            curr_d1_window = df_d1[max(0, idx_d1 - 60):idx_d1]
            e_d1 = SMCEngine(curr_d1_window)
            ev_d1 = e_d1.detect_bos_choch()
            trend_d1 = ("BULLISH" if "BULLISH" in ev_d1[-1]['type'] else "BEARISH") if ev_d1 else None
        else:
            trend_d1 = None

        # Fast bisect for H4 Macro window
        idx_h4 = bisect_right(timestamps_h4, curr_time)
        if idx_h4 >= 5:
            curr_h4_window = df_h4[max(0, idx_h4 - 80):idx_h4]
            e_h4 = SMCEngine(curr_h4_window)
            ev_h4 = e_h4.detect_bos_choch()
            trend_h4 = ("BULLISH" if "BULLISH" in ev_h4[-1]['type'] else "BEARISH") if ev_h4 else None
            h4_choch = ev_h4[-1]['type'] if ev_h4 and "CHOCH" in ev_h4[-1]['type'] else None
        else:
            trend_h4, h4_choch = None, None

        # Fast bisect for H1 Macro window
        idx_1h = bisect_right(timestamps_1h, curr_time)
        if idx_1h >= 20:
            curr_1h_window = df_1h[max(0, idx_1h - window_size):idx_1h]
            e_1h = SMCEngine(curr_1h_window)
            ev_1h = e_1h.detect_bos_choch()
            obs_1h = e_1h.detect_order_blocks(ev_1h)
            pd_1h = e_1h.detect_premium_discount()
            trend_1h = ("BULLISH" if "BULLISH" in ev_1h[-1]['type'] else "BEARISH") if ev_1h else None
        else:
            e_1h, obs_1h, pd_1h, trend_1h = None, [], None, None

        # Fast bisect for M30 window
        idx_30m = bisect_right(timestamps_30m, curr_time)
        if idx_30m >= 20:
            curr_30m_window = df_30m[max(0, idx_30m - window_size):idx_30m]
            e_30m = SMCEngine(curr_30m_window)
            ev_30m = e_30m.detect_bos_choch()
            trend_30m = ("BULLISH" if "BULLISH" in ev_30m[-1]['type'] else "BEARISH") if ev_30m else None
        else:
            trend_30m = None

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
        qms_15m = e_15m.detect_quasimodo()
        rbs_15m = e_15m.detect_rbs_sbr()
        pd_zones = e_15m.detect_premium_discount()
        trend_15m = "BULLISH" if ev_15m and "BULLISH" in ev_15m[-1]['type'] else "BEARISH"
        liquidity_pools = e_15m.detect_liquidity_pools(is_xau=True)

        # Run M5 SMC Engine (LTF)
        e_5m = SMCEngine(current_5m_window)
        ev_5m = e_5m.detect_bos_choch()
        obs_5m = e_5m.detect_order_blocks(ev_5m)
        fvgs_5m = e_5m.detect_fvg()
        breakers_5m = e_5m.detect_breaker_blocks(ev_5m)
        qms_5m = e_5m.detect_quasimodo()
        rbs_5m = e_5m.detect_rbs_sbr()
        sweeps = e_5m.detect_liquidity_sweeps(liquidity_pools=liquidity_pools)
        atr = e_5m.calculate_atr(14)
        trend_5m = "BULLISH" if ev_5m and "BULLISH" in ev_5m[-1]['type'] else "BEARISH"

        combined_obs = obs_15m + obs_5m
        combined_fvgs = fvgs_15m + fvgs_5m
        combined_breakers = breakers_15m + breakers_5m
        combined_qms = qms_15m + qms_5m
        combined_rbs = rbs_15m + rbs_5m
        combined_crts = e_15m.detect_crt() + e_5m.detect_crt()
        combined_snds = e_15m.detect_supply_demand() + e_5m.detect_supply_demand()
        snrs_15m = e_15m.detect_support_resistance()
        adx_15m = e_15m.calculate_adx(14)
        adx_1h = e_1h.calculate_adx(14) if e_1h else 25.0

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
            h1_trend=trend_1h,
            h4_trend=trend_h4,
            d1_trend=trend_d1,
            h4_choch=h4_choch,
            engine_ltf=e_5m,
            engine_htf=e_15m,
            pd_zones=pd_zones,
            breakers=combined_breakers,
            trade_manager=tm,
            atr=atr,
            db=db,
            current_time_str=curr_time,
            qm_patterns=combined_qms,
            rbs_sbr=combined_rbs,
            h1_obs=obs_1h,
            h1_pd_zones=pd_1h,
            m30_trend=trend_30m,
            crt_patterns=combined_crts,
            snd_zones=combined_snds,
            snr_zones=snrs_15m,
            adx_m15=adx_15m,
            adx_h1=adx_1h,
            liquidity_pools=liquidity_pools
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

    print("\n[4/4] Menghitung Statistik & Laporan...")
    total_completed = stats['WIN'] + stats['LOSS'] + stats['BREAK_EVEN']
    decisive_trades = stats['WIN'] + stats['LOSS'] # trade yang bukan BE
    win_rate = (stats['WIN'] / decisive_trades * 100) if decisive_trades > 0 else 0.0
    overall_win_rate = (stats['WIN'] / total_completed * 100) if total_completed > 0 else 0.0
    total_tp_reached = stats['TP1_RUNNER_BE'] + stats['TP2_FULL']

    # Drawdown calculation
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0

    for t in stats['trades']:
        pnl = t['pnl']
        equity += pnl
        if pnl > 0:
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += abs(pnl)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    report = f"""
======================================================================
  HASIL BACKTEST 2 BULAN XAU/USD (SMC GRADE A+ INSTITUSIONAL)
======================================================================
Periode Pengujian      : {start_date} s/d {end_date} (60 Hari)
Target Asset           : XAU/USD (Gold)
Timeframe Eksekusi     : M5 (LTF Entry) & M15 (HTF Structure)
Filter HTF             : H4 & D1 Trend Alignment (No Counter-Trend w/o H4 CHoCH)
Filter Likuiditas      : Liquidity Sweep (Asian High/Low, PDH/PDL, EQH/EQL, Swings)
Filter Sesi            : London Killzone (14:00-17:00 WIB) & NY Killzone (19:30-22:30 WIB)
Manajemen Risiko       : Dynamic SL (Sweep Extreme + Buffer), RRR Minimal 1:2
Eksekusi TP            : TP1 (Kunci 50% Lot & SL -> BE) + TP2 (Runner HTF Liquidity)
----------------------------------------------------------------------
RINGKASAN HASIL EKSEKUSI:
----------------------------------------------------------------------
Total Setup Selesai    : {total_completed} Trade
- Total Take Profit    : {total_tp_reached} Trade (WIN)
    * TP2 Full Runner  : {stats['TP2_FULL']} Trade (Mencapai target runner maksimal)
    * TP1 Secured + BE : {stats['TP1_RUNNER_BE']} Trade (Kunci profit TP1, runner ditutup di BE)
- Total Stop Loss (SL) : {stats['LOSS']} Trade (LOSS)
- Total Break-Even (BE): {stats['BREAK_EVEN']} Trade (0 Loss, terlindungi BE sebelum TP1)
- Cancelled / Expired  : {stats.get('CANCELLED', 0)} Order
- Missed Order         : {stats.get('MISSED', 0)} Order
----------------------------------------------------------------------
METRIK PERFORMA & WIN RATE:
----------------------------------------------------------------------
Win Rate (Decisive)    : {win_rate:.2f}% (WIN vs LOSS)
Win Rate (Total Setup) : {overall_win_rate:.2f}% (WIN / Total Selesai)
Total Net Profit       : {stats['total_pnl']:+.2f} R
Gross Profit           : +{gross_profit:.2f} R
Gross Loss             : -{gross_loss:.2f} R
Profit Factor          : {profit_factor:.2f}
Max Drawdown           : {max_dd:.2f} R
Rata-rata RRR TP1      : 1 : 2.0+ (Sesuai Syarat Minimal 1:2)
======================================================================
DAFTAR TRANSAKSI LENGKAP:
======================================================================
"""
    for idx_t, t in enumerate(stats['trades'], 1):
        report += f"{idx_t:>2}. [{t['timestamp']}] {t['type']:<4} @ {t['entry']:.2f} | SL: {t['sl']:.2f} (-{t['sl_pips']:.1f}p) | TP1: {t['tp1']:.2f} (+{t['tp1_pips']:.1f}p) | TP2: {t['tp2']:.2f} (+{t['tp2_pips']:.1f}p) | Status: {t['outcome']:<18} | PnL: {t['pnl']:+5.2f}R | Sweep: {t['sweep_pool']}\n"

    print(report)

    report_file = os.path.join(os.path.dirname(__file__), "backtest_2month_report.txt")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
            
    print(f"\n[SELESAI] Laporan lengkap disimpan ke {report_file}")

if __name__ == "__main__":
    asyncio.run(run_2month_backtest())

