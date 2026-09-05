import asyncio
from main import run_smc_analysis

async def test():
    tick = {
        'symbol': 'XAU/USD',
        'price': 4380.0,
        'timestamp': '2026-09-05T20:00:00',
        'source': 'test'
    }
    await run_smc_analysis(tick)

if __name__ == "__main__":
    asyncio.run(test())
