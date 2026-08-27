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
        # Take max 3 reasons, join in a single line to keep it clean
        top_reasons = signal['reasons'][:3]
        reasons_text = ", ".join(top_reasons)
        if len(signal['reasons']) > 3:
            reasons_text += "..."
        embed["fields"].append({"name": "Confluence", "value": reasons_text, "inline": False})
        
    # Add Smart Scaling instructions so users execute it correctly
    entry = float(signal.get('entry', 0))
    sl = float(signal.get('sl', 0))
    atr_dist = abs(entry - sl) / 1.5 # Estimate ATR from SL distance
    partial_tp = entry + (0.5 * atr_dist) if "BUY" in signal.get('type', '') else entry - (0.5 * atr_dist)
    
    embed["fields"].append({
        "name": "⚙️ Smart Scaling Guide", 
        "value": f"1. Amankan **50% Profit** di area **{partial_tp:.2f}**.\n2. Segera geser SL ke **Break Even ({entry:.2f})** setelah partial.\n3. Biarkan sisa 50% *running* ke Final TP.", 
        "inline": False
    })
        
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
        
    color = 0x10B981 if new_status == "WIN" else (0x6B7280 if new_status in ["CANCELLED", "MISSED"] else 0xF43F5E)
    status_icon = "✅" if new_status == "WIN" else ("🗑️" if new_status in ["CANCELLED", "MISSED"] else "❌")
    
    if new_status == "WIN":
        result_text = "Take Profit (TP) 🎯"
    elif new_status == "CANCELLED":
        result_text = "Cancelled / Expired 🗑️"
    elif new_status == "MISSED":
        result_text = "Missed (Hit TP Before Entry) 🏃💨"
    else:
        result_text = "Stop Loss (SL) 🛑"
    
    embed = {
        "title": f"{status_icon} Trade Closed: {signal.get('symbol')} {new_status} {status_icon}",
        "description": f"The trade for {signal.get('symbol')} has hit its {result_text}." if new_status not in ["CANCELLED", "MISSED"] else f"The trade for {signal.get('symbol')} has been {result_text}.",
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
