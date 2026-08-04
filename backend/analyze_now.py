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
    sg = SignalGenerator(cooldown_hours=0)
    symbol = "XAU/USD"
    print(f"Fetching data for {symbol}...")
    
    # User wants M15 for analysis, M5 for confirmation.
    # In standard multi-timeframe SMC, M15 is HTF (analysis) and M5 is LTF (entry/confirmation)
    df_htf = dp.get_historical_data(symbol, interval="15min")
    df_ltf = dp.get_historical_data(symbol, interval="5min")
    
    if not df_htf or not df_ltf:
        print("Failed to fetch data.")
        return
        
    print(f"Got {len(df_htf)} M15 candles and {len(df_ltf)} M5 candles.")
    
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
    
    # Run SMC Engine on M5 (LTF)
    engine_ltf = SMCEngine(df_ltf)
    events = engine_ltf.detect_bos_choch()
    fvgs = engine_ltf.detect_fvg()
    obs = engine_ltf.detect_order_blocks(events)
    sweeps = engine_ltf.detect_liquidity_sweeps()
    snr_zones = engine_ltf.detect_support_resistance()
    snd_zones = engine_ltf.detect_supply_demand()
    
    current_price = df_ltf[-1]['close']
    print(f"Current M5 Price: {current_price}")
    
    # Evaluate confluence
    signal = await sg.evaluate_confluence(
        symbol=symbol,
        current_price=current_price,
        events=events,
        obs=obs,
        fvgs=fvgs,
        sweeps=sweeps,
        htf_trend=htf_trend,
        snr_zones=snr_zones,
        snd_zones=snd_zones
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
        print(f"Latest structure event on M5: {events[-1]['type'] if events else 'None'}")
        print(f"Unmitigated OBs: {len([ob for ob in obs if not ob['mitigated']])}")
        print(f"Unmitigated FVGs: {len([fvg for fvg in fvgs if not fvg['mitigated']])}")
        print(f"SNR Zones: {len(snr_zones)}")
        print(f"SND Zones: {len(snd_zones)}")

if __name__ == "__main__":
    asyncio.run(main())
