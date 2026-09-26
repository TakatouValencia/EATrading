import asyncio
from data_provider import DataProvider
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
import json
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    dp = DataProvider()
    sg = SignalGenerator(cooldown_minutes=0)
    symbol = "XAU/USD"
    print(f"Fetching data for {symbol}...")
    df_d1 = dp.get_historical_data(symbol, interval="1day", use_csv=True)
    df_h4 = dp.get_historical_data(symbol, interval="4h", use_csv=True)
    df_h1 = dp.get_historical_data(symbol, interval="1h", use_csv=True)
    df_m15 = dp.get_historical_data(symbol, interval="15min", use_csv=False)
    df_m5 = dp.get_historical_data(symbol, interval="5min", use_csv=False)
    df_m1 = dp.get_historical_data(symbol, interval="1min", use_csv=False)
    
    if not df_m15 or not df_m5 or not df_m1:
        print("Failed to fetch data.")
        return
        
    print(f"Got {len(df_d1 or [])} D1, {len(df_h4 or [])} H4, {len(df_h1 or [])} H1, {len(df_m15)} M15, {len(df_m5)} M5, and {len(df_m1)} M1 candles.")
    
    # Run SMC Engine on D1 & H4 (HTF)
    engine_d1 = SMCEngine(df_d1) if df_d1 else None
    d1_events = engine_d1.detect_bos_choch() if engine_d1 else []
    d1_trend = ("BULLISH" if "BULLISH" in d1_events[-1]['type'] else "BEARISH") if d1_events else None
    
    engine_h4 = SMCEngine(df_h4) if df_h4 else None
    h4_events = engine_h4.detect_bos_choch() if engine_h4 else []
    h4_trend = ("BULLISH" if "BULLISH" in h4_events[-1]['type'] else "BEARISH") if h4_events else None
    h4_choch = None
    if h4_events:
        for ev in reversed(h4_events[-10:]):
            if "CHOCH" in ev.get('type', ''):
                h4_choch = ev['type']
                break
    print(f"D1 Trend: {d1_trend} | H4 Trend: {h4_trend} (H4 CHoCH: {h4_choch})")

    # Run SMC Engine on H1
    engine_h1 = SMCEngine(df_h1) if df_h1 else None
    h1_events = engine_h1.detect_bos_choch() if engine_h1 else []
    h1_obs = engine_h1.detect_order_blocks(h1_events) if engine_h1 else []
    h1_fvgs = engine_h1.detect_fvg() if engine_h1 else []
    h1_pd = engine_h1.detect_premium_discount() if engine_h1 else None
    h1_trend = ("BULLISH" if "BULLISH" in h1_events[-1]['type'] else "BEARISH") if h1_events else None

    # Run SMC Engine on M15 (HTF)
    engine_m15 = SMCEngine(df_m15)
    m15_events = engine_m15.detect_bos_choch()
    m15_obs = engine_m15.detect_order_blocks(m15_events)
    m15_fvgs = engine_m15.detect_fvg()
    m15_breakers = engine_m15.detect_breaker_blocks(m15_events)
    pd_zones = engine_m15.detect_premium_discount()
    liquidity_pools = engine_m15.detect_liquidity_pools(is_xau=("XAU" in symbol))
    sweeps = engine_m15.detect_liquidity_sweeps(liquidity_pools=liquidity_pools)
    m15_trend = None
    if m15_events:
        last_m15_event = m15_events[-1]
        m15_trend = "BULLISH" if "BULLISH" in last_m15_event['type'] else "BEARISH"
    print(f"M15 Trend: {m15_trend}")
    
    # Run SMC Engine on M5 (MTF)
    engine_m5 = SMCEngine(df_m5)
    m5_events = engine_m5.detect_bos_choch()
    m5_obs = engine_m5.detect_order_blocks(m5_events)
    m5_fvgs = engine_m5.detect_fvg()
    m5_breakers = engine_m5.detect_breaker_blocks(m5_events)
    sweeps = sweeps + engine_m5.detect_liquidity_sweeps(liquidity_pools=liquidity_pools)
    m5_trend = None
    if m5_events:
        last_m5_event = m5_events[-1]
        m5_trend = "BULLISH" if "BULLISH" in last_m5_event['type'] else "BEARISH"
    print(f"M5 Trend: {m5_trend}")
    
    # Combine H1, M15 and M5 institutional POIs
    combined_obs = h1_obs + m15_obs + m5_obs
    combined_fvgs = h1_fvgs + m15_fvgs + m5_fvgs
    combined_breakers = m15_breakers + m5_breakers
    combined_qms = engine_m15.detect_quasimodo() + engine_m5.detect_quasimodo()
    combined_rbs = engine_m15.detect_rbs_sbr() + engine_m5.detect_rbs_sbr()
    combined_crts = engine_m15.detect_crt() + engine_m5.detect_crt()

    # Run SMC Engine on M1 (LTF)
    engine_m1 = SMCEngine(df_m1)
    events = engine_m1.detect_bos_choch()
    snr_zones = engine_m1.detect_support_resistance()
    snd_zones = engine_m1.detect_supply_demand()
    fibo_ote = engine_m1.detect_fibo_ote()
    poc_price = engine_m1.calculate_volume_profile(lookback=100)
    
    current_price = df_m1[-1]['close']
    print(f"Current M1 Price: {current_price}")
    
    # Evaluate confluence
    dxy_trend = None
    if "XAU" in symbol:
        print("Fetching DXY data for Intermarket Correlation...")
        df_dxy = dp.get_historical_data("DXY", interval="15min")
        if df_dxy:
            engine_dxy = SMCEngine(df_dxy)
            dxy_events = engine_dxy.detect_bos_choch()
            if dxy_events:
                dxy_trend = "BULLISH" if "BULLISH" in dxy_events[-1]['type'] else "BEARISH"
                print(f"DXY HTF Trend: {dxy_trend}")
                
    atr = engine_m15.calculate_atr(period=14)
    reversal_patterns = engine_m15.detect_reversal_patterns()
    adx_m15 = engine_m15.calculate_adx(14)
    adx_h1 = engine_h1.calculate_adx(14) if engine_h1 else 25.0
    
    signal = await sg.evaluate_confluence(
        symbol=symbol,
        current_price=current_price,
        events=m15_events + (m5_events if m5_events else []),
        obs=combined_obs,
        fvgs=combined_fvgs,
        sweeps=sweeps,
        m15_trend=m15_trend,
        m5_trend=m5_trend,
        htf_trend=h4_trend or h1_trend or m15_trend,
        h1_trend=h1_trend,
        h4_trend=h4_trend,
        d1_trend=d1_trend,
        h4_choch=h4_choch,
        snr_zones=snr_zones,
        snd_zones=snd_zones,
        pd_zones=pd_zones,
        breakers=combined_breakers,
        dxy_trend=dxy_trend,
        fibo_ote=fibo_ote,
        poc_price=poc_price,
        atr=atr,
        reversal_patterns=reversal_patterns,
        engine_ltf=engine_m15,
        engine_htf=engine_h4 or engine_h1,
        qm_patterns=combined_qms,
        rbs_sbr=combined_rbs,
        h1_obs=h1_obs,
        h1_pd_zones=h1_pd,
        crt_patterns=combined_crts,
        adx_m15=adx_m15,
        adx_h1=adx_h1,
        liquidity_pools=liquidity_pools
    )
    
    if signal:
        print("\n=== SIGNAL FOUND ===")
        print(json.dumps(signal, indent=2))
        
        # Save to DB and Send to Discord
        from database import Database
        from discord_notifier import send_discord_alert
        
        db = Database()
        result = db.save_signal(signal)
        if result and "id" in result:
            signal["id"] = result["id"]
            
        print("Saving to Database...")
        await send_discord_alert(signal)
        print("Signal sent to Discord and Database successfully!")
        
    else:
        print("\n=== NO VALID SIGNAL FOUND ===")
        print(f"Latest structure event on M15: {m15_events[-1]['type'] if m15_events else 'None'}")
        print(f"Unmitigated OBs: {len([ob for ob in combined_obs if not ob.get('mitigated', False)])}")
        print(f"Unmitigated FVGs: {len([fvg for fvg in combined_fvgs if not fvg.get('mitigated', False)])}")
        print(f"SNR Zones: {len(snr_zones)}")
        print(f"SND Zones: {len(snd_zones)}")

if __name__ == "__main__":
    asyncio.run(main())
