import os
import requests
from datetime import datetime
import asyncio

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# In-memory deduplication cache: (symbol, type, entry) -> timestamp
_recent_alerts = {}

def _send_webhook(payload):
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"Error sending Discord alert: {e}")

async def send_discord_alert(signal: dict):
    if not DISCORD_WEBHOOK_URL:
        return
        
    symbol = signal.get('symbol', 'UNKNOWN')
    sig_type = signal.get('type', 'UNKNOWN')
    try:
        entry = round(float(signal.get('entry', 0.0)), 2)
    except Exception:
        entry = signal.get('entry', 0.0)
        
    now_ts = datetime.now().timestamp()
    dedup_key = (symbol, sig_type, entry)
    
    # Suppress duplicate alerts for identical setups within 5 minutes (300 seconds)
    last_sent = _recent_alerts.get(dedup_key, 0)
    if now_ts - last_sent < 300:
        print(f"[DISCORD] Anti-spam active: Suppressed duplicate alert for {symbol} {sig_type} at {entry} (sent {now_ts - last_sent:.0f}s ago).")
        return
        
    _recent_alerts[dedup_key] = now_ts
    
    # Prune old cache if too large
    if len(_recent_alerts) > 50:
        for k in list(_recent_alerts.keys()):
            if now_ts - _recent_alerts[k] > 600:
                del _recent_alerts[k]
        
    color = 0x10B981 if "BUY" in signal.get('type', '') else 0xF43F5E 
    
    embed = {
        "title": f"🚨 {signal.get('type')} Signal: {signal.get('symbol')} 🚨",
        "description": "SMC Engine detected a new valid trading setup.",
        "color": color,
        "fields": [
            {"name": "Entry Price", "value": f"**{signal.get('entry')}**", "inline": True},
            {"name": "TP1 (Amankan 50%)", "value": f"**{signal.get('tp1', signal.get('tp'))}** (+70p)", "inline": True},
            {"name": "TP2 (Swing Target)", "value": f"**{signal.get('tp2', signal.get('tp'))}**", "inline": True},
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
    tp = float(signal.get('tp', 0))
    tp1 = float(signal.get('tp1', tp))
    is_xau = "XAU" in symbol
    pip_unit = 0.10 if is_xau else 0.0001
    sl_pips = abs(entry - sl) / pip_unit
    tp_pips = abs(tp - entry) / pip_unit
    be_target = entry + (5.0 if is_xau else 0.0050) if "BUY" in signal.get('type', '') else entry - (5.0 if is_xau else 0.0050)
    
    embed["fields"].append({
        "name": "⚙️ Execution & Risk Guide (Partial TP Engine)", 
        "value": f"• **TP1 (+70 Pips)**: **{tp1:.2f}** -> Amankan 50% lot dan otomatis geser SL ke BE!\n• **TP2 (Runner)**: **{tp_pips:.0f} Pips** ({tp:.2f}) -> Biarkan 50% lot lari tanpa risiko.\n• **Stop Loss**: **{sl_pips:.0f} Pips** ({sl:.2f}, Maks 70 Pips)\n• **Proteksi BE**: Geser SL ke **Entry ({entry:.2f})** saat harga capai **{be_target:.2f} (+50 pips)**.", 
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
        
    if new_status == "WIN":
        color = 0x10B981
        status_icon = "✅"
        result_text = "Take Profit (TP) 🎯"
    elif new_status == "BREAK_EVEN":
        color = 0x3B82F6
        status_icon = "🛡️"
        result_text = "Break-Even Protection (0 Loss) 🛡️"
    elif new_status == "CANCELLED":
        color = 0x6B7280
        status_icon = "🗑️"
        result_text = "Cancelled / Expired 🗑️"
    elif new_status == "MISSED":
        color = 0x6B7280
        status_icon = "🏃💨"
        result_text = "Missed (Hit TP Before Entry) 🏃💨"
    else:
        color = 0xF43F5E
        status_icon = "🛑"
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

async def send_circuit_breaker_alert(reason: str, details: str = ""):
    """Send Discord alert when Risk Circuit Breaker triggers (e.g. 3 consecutive SLs)."""
    if not DISCORD_WEBHOOK_URL:
        return
        
    embed = {
        "title": "🚨 CIRCUIT BREAKER TRIGGERED: TRADING PAUSED 🚨",
        "description": f"**Alasan Proteksi**: {reason}\n\nAI menghentikan pengiriman sinyal baru untuk hari ini guna melindungi modal akun dan menghindari overtrading/revenge trading.",
        "color": 0xEF4444, # Bright Red
        "fields": [
            {"name": "Status", "value": "🛑 **SISTEM DIKUNCI HINGGA BESOK**", "inline": True},
            {"name": "Maksimal SL Beruntun", "value": "**3x Per Hari**", "inline": True},
            {"name": "Catatan", "value": details or "Reset otomatis akan dilakukan saat pergantian hari pasar baru (00:00 UTC).", "inline": False}
        ],
        "footer": {
            "text": "Novaire EA Risk Management Engine"
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    payload = {
        "username": "Novaire EA Guard",
        "content": "⚠️ @everyone **PERINGATAN MANAJEMEN RISIKO:** Batas Harian Tercapai!",
        "embeds": [embed]
    }
    
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _send_webhook, payload)
