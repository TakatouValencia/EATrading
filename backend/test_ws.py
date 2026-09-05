import asyncio
import websockets
import json
import os
from dotenv import load_dotenv

load_dotenv()

async def test_ws():
    api_key = os.getenv("TWELVE_DATA_API_KEY", "")
    if not api_key:
        print("No API key")
        return
        
    url = f"wss://ws.twelvedata.com/v1/quotes/price?apikey={api_key}"
    print(f"Connecting to {url}")
    
    try:
        async with websockets.connect(url) as ws:
            subscribe_msg = {
                "action": "subscribe",
                "params": {
                    "symbols": "XAU/USD"
                }
            }
            await ws.send(json.dumps(subscribe_msg))
            print("Sent subscribe message")
            
            for _ in range(3):
                message = await asyncio.wait_for(ws.recv(), timeout=10)
                print("Received:", message)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(test_ws())
