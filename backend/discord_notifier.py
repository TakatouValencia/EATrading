import os
import requests
from datetime import datetime
import asyncio

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

def _send_webhook(payload):
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"Error sending Discord alert: {e}")

async def send_discord_alert(signal: dict):
    if not DISCORD_WEBHOOK_URL:
        return
        
    color = 0x10B981 if "BUY" in signal.get('type', '') else 0xF43F5E 
    
    embed = {
        "title": f"🚨 {signal.get('type')} Signal: {signal.get('symbol')} 🚨",
        "description": "SMC Engine detected a new valid trading setup.",
        "color": color,
        "fields": [
            {"name": "Entry Price", "value": f"**{signal.get('entry')}**", "inline": True},
            {"name": "Take Profit (TP)", "value": f"**{signal.get('tp')}**", "inline": True},
            {"name": "Stop Loss (SL)", "value": f"**{signal.get('sl')}**", "inline": True},
        ],
        "footer": {
            "text": "Novaire EA SMC Engine"
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if signal.get('reasons'):
        reasons_text = "\n".join([f"- {r}" for r in signal['reasons']])
        embed["fields"].append({"name": "Confluence Reasons", "value": reasons_text, "inline": False})
        
    payload = {
        "username": "Novaire EA",
        "content": "🔔 @everyone Sinyal Baru Terdeteksi!",
        "embeds": [embed]
    }
    
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _send_webhook, payload)

async def send_discord_trade_update(signal: dict, new_status: str, pnl: float):
    if not DISCORD_WEBHOOK_URL:
        return
        
    color = 0x10B981 if new_status == "WIN" else 0xF43F5E 
    status_icon = "✅" if new_status == "WIN" else "❌"
    result_text = "Take Profit (TP) 🎯" if new_status == "WIN" else "Stop Loss (SL) 🛑"
    
    embed = {
        "title": f"{status_icon} Trade Closed: {signal.get('symbol')} {new_status} {status_icon}",
        "description": f"The trade for {signal.get('symbol')} has hit its {result_text}.",
        "color": color,
        "fields": [
            {"name": "Type", "value": f"**{signal.get('type')}**", "inline": True},
            {"name": "Entry", "value": f"**{signal.get('entry')}**", "inline": True},
            {"name": "PnL", "value": f"**{pnl}R**", "inline": True},
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    payload = {
        "username": "Novaire EA",
        "content": "🔔 @everyone Update Trade!",
        "embeds": [embed]
    }
    
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _send_webhook, payload)
