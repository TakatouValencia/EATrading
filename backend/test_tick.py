import asyncio
from main import run_smc_analysis, app

async def test():
    dummy_m1 = [{'timestamp': '2026-09-05T20:00:00', 'open': 2900.0, 'high': 2905.0, 'low': 2895.0, 'close': 2900.0, 'volume': 100} for _ in range(50)]
    dummy_m5 = [{'timestamp': '2026-09-05T20:00:00', 'open': 2900.0, 'high': 2905.0, 'low': 2895.0, 'close': 2900.0, 'volume': 500} for _ in range(50)]
    dummy_m15 = [{'timestamp': '2026-09-05T20:00:00', 'open': 2900.0, 'high': 2905.0, 'low': 2895.0, 'close': 2900.0, 'volume': 1500} for _ in range(50)]
    
    app.state.market_data = {
        "XAU/USD": {
            "m1": dummy_m1,
            "m5": dummy_m5,
            "m15": dummy_m15,
            "ltf": dummy_m1,
            "htf": dummy_m15
        },
        "DXY": dummy_m15
    }
    
    tick = {
        'symbol': 'XAU/USD',
        'price': 2901.0,
        'timestamp': '2026-09-05T20:00:05',
        'source': 'test'
    }
    await run_smc_analysis(tick)
    print("[SUCCESS] Test tick successfully processed by M15/M5/M1 pipeline!")

if __name__ == "__main__":
    asyncio.run(test())
