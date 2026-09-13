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
    is_xau = "XAU" in symbol
    pip_unit = 0.10 if is_xau else 0.0001
    entry_f = float(signal.get('entry', 0.0))
    sl_f = float(signal.get('sl', 0.0))
    tp_f = float(signal.get('tp', 0.0))
    tp1_f = float(signal.get('tp1', tp_f))
    tp2_f = float(signal.get('tp2', tp_f))
    
    sl_pips = abs(entry_f - sl_f) / pip_unit if pip_unit > 0 else 0
    tp1_pips = abs(tp1_f - entry_f) / pip_unit if pip_unit > 0 else 0
    tp2_pips = abs(tp2_f - entry_f) / pip_unit if pip_unit > 0 else 0
    rr_tp1 = tp1_pips / sl_pips if sl_pips > 0 else 2.0
    rr_tp2 = tp2_pips / sl_pips if sl_pips > 0 else 3.5
    
    embed = {
        "title": f"💎 [PREMIUM A+] {signal.get('type')} Signal: {signal.get('symbol')} 💎",
        "description": "Institutional SMC Engine terdeteksi setup berprobabilitas tinggi untuk Daily Intraday Trading.",
        "color": color,
        "fields": [
            {"name": "🎯 Entry Price", "value": f"**{entry_f:.2f}**", "inline": True},
            {"name": "🛑 Stop Loss (SL)", "value": f"**{sl_f:.2f}** (-{sl_pips:.0f}p)", "inline": True},
            {"name": "⚖️ Risk : Reward", "value": f"**1 : {rr_tp2:.1f}** (TP2)", "inline": True},
            {"name": "🔒 TP1 (Amankan 50% & BE)", "value": f"**{tp1_f:.2f}** (+{tp1_pips:.0f}p | 1:{rr_tp1:.1f}R)", "inline": True},
            {"name": "🚀 TP2 (Swing Runner)", "value": f"**{tp2_f:.2f}** (+{tp2_pips:.0f}p | 1:{rr_tp2:.1f}R)", "inline": True},
            {"name": "📊 Lot Rekomendasi", "value": f"**{signal.get('lot_size', 0.01)} Lot** (Risiko 1%)", "inline": True},
        ],
        "footer": {
            "text": "Novaire EA • Premium Daily Trading Engine"
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if signal.get('reasons'):
        # Filter UI badge out of confluence list if needed, or format nicely
        clean_reasons = [r for r in signal['reasons'] if not r.startswith("[UI_BADGE")]
        reasons_text = "\n".join([f"• {r}" for r in clean_reasons[:4]])
        embed["fields"].append({"name": "🧠 SMC Confluence & Thesis", "value": reasons_text, "inline": False})
        
    be_target = entry_f + (0.5 * pip_unit if "BUY" in signal.get('type', '') else -0.5 * pip_unit)
    embed["fields"].append({
        "name": "📋 Intraday Trade Execution Plan", 
        "value": f"1. Pasang order sesuai tipe sinyal (**{signal.get('signal_type', 'CONFIRMED')}**).\n2. Saat harga mencapai **TP1 ({tp1_f:.2f})**, tutup **50% lot** dan geser SL ke **Break-Even ({be_target:.2f})**.\n3. Biarkan sisa 50% lot berlari hingga **TP2 ({tp2_f:.2f})** tanpa risiko kerugian modal.", 
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
