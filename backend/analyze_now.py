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
    df_h4 = dp.get_historical_data(symbol, interval="4h")
    df_h1 = dp.get_historical_data(symbol, interval="1h")
    df_htf = dp.get_historical_data(symbol, interval="15min")
    df_ltf = dp.get_historical_data(symbol, interval="1min")
    
    if not df_htf or not df_ltf or not df_h1 or not df_h4:
        print("Failed to fetch data.")
        return
        
    print(f"Got {len(df_h4)} H4, {len(df_h1)} H1, {len(df_htf)} M15 and {len(df_ltf)} M1 candles.")
    
    # Run SMC Engine on H4
    h4_trend = None
    if df_h4:
        h4_events = SMCEngine(df_h4).detect_bos_choch()
        if h4_events:
            h4_trend = "BULLISH" if "BULLISH" in h4_events[-1]['type'] else "BEARISH"
            
    # Run SMC Engine on H1
    h1_trend = None
    if df_h1:
        h1_events = SMCEngine(df_h1).detect_bos_choch()
        if h1_events:
            h1_trend = "BULLISH" if "BULLISH" in h1_events[-1]['type'] else "BEARISH"
            
    # Run SMC Engine on M15 (HTF)
    engine_htf = SMCEngine(df_htf)
    htf_events = engine_htf.detect_bos_choch()
    htf_trend = None
    if htf_events:
        last_htf_event = htf_events[-1]
        if "BULLISH" in last_htf_event['type']:
            htf_trend = "BULLISH"
        elif "BEARISH" in last_htf_event['type']:
            htf_trend = "BEARISH"
    print(f"M15 (HTF) Trend: {htf_trend}")
    
    # Run SMC Engine on M1 (LTF)
    engine_ltf = SMCEngine(df_ltf)
    events = engine_ltf.detect_bos_choch()
    fvgs = engine_ltf.detect_fvg()
    obs = engine_ltf.detect_order_blocks(events)
    sweeps = engine_ltf.detect_liquidity_sweeps()
    snr_zones = engine_ltf.detect_support_resistance()
    snd_zones = engine_ltf.detect_supply_demand()
    
    current_price = df_ltf[-1]['close']
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
                
    atr = engine_ltf.calculate_atr(period=14)
    reversal_patterns = engine_ltf.detect_reversal_patterns()
    
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
        dxy_trend=dxy_trend,
        atr=atr,
        reversal_patterns=reversal_patterns,
        engine_ltf=engine_ltf
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
        print(f"Latest structure event on M1: {events[-1]['type'] if events else 'None'}")
        print(f"Unmitigated OBs: {len([ob for ob in obs if not ob['mitigated']])}")
        print(f"Unmitigated FVGs: {len([fvg for fvg in fvgs if not fvg['mitigated']])}")
        print(f"SNR Zones: {len(snr_zones)}")
        print(f"SND Zones: {len(snd_zones)}")

if __name__ == "__main__":
    asyncio.run(main())
