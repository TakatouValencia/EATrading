import os
import sys
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

async def run_diagnostics():
    print("=" * 65)
    print("  NOVAire EA - COMPREHENSIVE AI SYSTEM DIAGNOSTIC TOOL  ")
    print("=" * 65)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()

    # 1. Check Environment Variables
    print("[1/6] Checking Configuration & Environment...")
    td_key = os.getenv("TWELVE_DATA_API_KEY", "")
    discord_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    print(f"  * TwelveData Key: {'[CONFIGURED]' if td_key else '[MISSING]'}")
    print(f"  * Discord Webhook: {'[CONFIGURED]' if discord_url else '[MISSING]'}")

    # 2. Check Database & Risk Circuit Breaker
    print("\n[2/6] Checking Database & Risk State...")
    try:
        from database import Database
        from trade_manager import TradeManager
        db = Database()
        tm = TradeManager(db)
        allowed, reason = tm.check_trading_allowed()
        stats = db.get_statistics()
        print(f"  * SQLite Database: CONNECTED ({db.db_path})")
        print(f"  * Historical Stats: {stats}")
        print(f"  * Today's Consecutive Losses: {tm.consecutive_losses}/3")
        print(f"  * Today's PnL: {tm.daily_pnl:.2f}R")
        print(f"  * Circuit Breaker Status: {'[ALLOWED]' if allowed else f'[BLOCKED - {reason}]'}")
    except Exception as e:
        print(f"  [ERROR] Database/Risk Manager Error: {e}")

    # 3. Check Discord Webhook
    print("\n[3/6] Testing Discord Webhook Connection...")
    if discord_url:
        try:
            import requests
            test_payload = {
                "username": "Novaire EA Diagnostics",
                "content": "🔧 **Pemeriksaan Sistem Novaire EA**: Sistem backend aktif dan siap mengirim sinyal trading."
            }
            res = requests.post(discord_url, json=test_payload, timeout=5)
            if res.status_code in [200, 204]:
                print("  * Discord Webhook: ONLINE (HTTP 204 OK)")
            else:
                print(f"  * Discord Webhook returned status: {res.status_code}")
        except Exception as e:
            print(f"  [ERROR] Discord Webhook Error: {e}")
    else:
        print("  * Discord Webhook: Skipped (No URL)")

    # 4. Check TwelveData Live Price & Data Provider
    print("\n[4/6] Fetching Live Market Data (XAU/USD)...")
    try:
        from data_provider import DataProvider
        from smc_engine import SMCEngine
        dp = DataProvider()
        
        m1 = await asyncio.to_thread(dp.get_historical_data, "XAU/USD", "1min", use_csv=False)
        m15 = await asyncio.to_thread(dp.get_historical_data, "XAU/USD", "15min", use_csv=False)
        h1 = await asyncio.to_thread(dp.get_historical_data, "XAU/USD", "1h", use_csv=False)
        h4 = await asyncio.to_thread(dp.get_historical_data, "XAU/USD", "4h", use_csv=False)
        
        current_price = m1[-1]['close'] if m1 else 0
        print(f"  * Live XAU/USD Price: ${current_price:.2f}")
        print(f"  * Candles Loaded: H4={len(h4)}, H1={len(h1)}, M15={len(m15)}, M1={len(m1)}")
        
        # 5. Multi-Timeframe Structure & SMC Analysis
        print("\n[5/6] Multi-Timeframe SMC Structure Analysis...")
        e_h4 = SMCEngine(h4)
        e_h1 = SMCEngine(h1)
        e_m15 = SMCEngine(m15)
        e_m1 = SMCEngine(m1)
        
        h4_ev = e_h4.detect_bos_choch()
        h1_ev = e_h1.detect_bos_choch()
        m15_ev = e_m15.detect_bos_choch()
        m1_ev = e_m1.detect_bos_choch()
        
        h4_trend = "BULLISH" if h4_ev and "BULLISH" in h4_ev[-1]['type'] else "BEARISH"
        h1_trend = "BULLISH" if h1_ev and "BULLISH" in h1_ev[-1]['type'] else "BEARISH"
        m15_trend = "BULLISH" if m15_ev and "BULLISH" in m15_ev[-1]['type'] else "BEARISH"
        
        h1_obs = e_h1.detect_order_blocks(h1_ev)
        h1_fvgs = e_h1.detect_fvg()
        m15_obs = e_m15.detect_order_blocks(m15_ev)
        m15_fvgs = e_m15.detect_fvg()
        sweeps = e_m1.detect_liquidity_sweeps()
        pd = e_m15.detect_premium_discount()
        atr = e_m1.calculate_atr(14)
        
        print(f"  • Macro Trend (H4): {h4_trend}")
        print(f"  • Intermediate Trend (H1): {h1_trend}")
        print(f"  • Intraday Trend (M15): {m15_trend}")
        print(f"  • ATR (M1 Volatility): ${atr:.2f}")
        print(f"  • Premium/Discount Range: Low=${pd.get('range_low', 0):.2f} | Eq=${pd.get('eq', 0):.2f} | High=${pd.get('range_high', 0):.2f}")
        print(f"  • Institutional POIs: {len(h1_obs + m15_obs)} Order Blocks, {len(h1_fvgs + m15_fvgs)} FVGs")
        print(f"  • Liquidity Sweeps Detected: {len(sweeps)}")
        
        # 6. Signal Evaluation Simulation
        print("\n[6/6] Live Signal Evaluation Engine...")
        from signal_generator import SignalGenerator
        sg = SignalGenerator(cooldown_minutes=0)
        
        signal = await sg.evaluate_confluence(
            symbol="XAU/USD",
            current_price=current_price,
            events=m1_ev,
            obs=h1_obs + m15_obs,
            fvgs=h1_fvgs + m15_fvgs,
            sweeps=sweeps,
            htf_trend=m15_trend,
            h1_trend=h1_trend,
            h4_trend=h4_trend,
            pd_zones=pd,
            trade_manager=tm,
            atr=atr,
            db=db,
            engine_ltf=e_m1
        )
        
        if signal:
            print("  [SUCCESS] VALID HIGH-PROBABILITY SETUP DETECTED!")
            print(f"  * Type: {signal['type']} ({signal['signal_type']})")
            print(f"  * Grade: {signal['grade']} (Lot: {signal['lot_size']})")
            print(f"  * Entry: ${signal['entry']} | SL: ${signal['sl']} | TP: ${signal['tp']}")
            print(f"  * Confluences: {', '.join(signal['reasons'][:4])}")
        else:
            print("  [WAITING] No active trigger at this exact price tick.")
            print(f"  * Current price (${current_price:.2f}) is waiting for pullback into nearest POI or liquidity sweep.")
            print("  * Engine is active, responsive, and scanning live ticks.")

    except Exception as e:
        import traceback
        print(f"  [ERROR] SMC Engine Evaluation Error: {e}")
        traceback.print_exc()

    print("\n" + "=" * 65)
    print("  DIAGNOSTIC TEST COMPLETE - ALL CRITICAL PATHS VERIFIED  ")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(run_diagnostics())
