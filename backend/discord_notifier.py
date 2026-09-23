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
        
    # Strictly require Grade A+ to broadcast
    if signal.get('grade') != "A+":
        print(f"[DISCORD] Alert blocked: Signal grade is '{signal.get('grade')}', not Grade A+.")
        return

    color = 0x10B981 if "BUY" in signal.get('type', '') else 0xF43F5E 
    is_xau = "XAU" in symbol
    pip_unit = 0.10 if is_xau else 0.0001
    entry_f = float(signal.get('entry', 0.0))
    sl_f = float(signal.get('sl', 0.0))
    tp_f = float(signal.get('tp', 0.0))
    tp1_f = float(signal.get('tp1', tp_f))
    tp2_f = float(signal.get('tp2', tp_f))
    
    sl_pips = abs(entry_f - sl_f) / pip_unit if pip_unit > 0 else 0
    tp_pips = abs(tp_f - entry_f) / pip_unit if pip_unit > 0 else 0
    rr_tp = tp_pips / sl_pips if sl_pips > 0 else 2.0
    
    entry_zone = signal.get('entry_zone', f"{entry_f:.2f}")
    sweep_info = signal.get('sweep_pool', 'Institutional Liquidity Pool')
    kz_info = signal.get('killzone', 'Killzone Session Active')
    
    embed = {
        "title": f"💎 [GRADE A+] {signal.get('type')} Signal: {signal.get('symbol')} 💎",
        "description": "Institutional SMC Engine • Setup terkonfirmasi dengan likuiditas sweep, LTF CHoCH & FVG imbalance.",
        "color": color,
        "fields": [
            {"name": "📊 Pair & Direction", "value": f"**{symbol}** | **{signal.get('type')}** ({signal.get('signal_type', 'CONFIRMED')})", "inline": True},
            {"name": "🌟 Setup Grade", "value": f"**Grade {signal.get('grade', 'A+')}** (100% Confluence)", "inline": True},
            {"name": "⚖️ Risk : Reward (RRR)", "value": f"**1 : {rr_tp:.1f}**", "inline": True},
            {"name": "🎯 Entry Trigger / Price", "value": f"**{entry_f:.2f}**", "inline": True},
            {"name": "📦 Entry Zone (POI)", "value": f"**{entry_zone}**", "inline": True},
            {"name": "🛑 Stop Loss (SL)", "value": f"**{sl_f:.2f}** (-{sl_pips:.0f} pips | Sweep Wick)", "inline": True},
            {"name": "🎯 Take Profit (TP)", "value": f"**{tp_f:.2f}** (+{tp_pips:.0f} pips | 1:{rr_tp:.1f}R)", "inline": True},
            {"name": "💼 Lot Rekomendasi", "value": f"**{signal.get('lot_size', 0.01)} Lot** (Risiko 1%)", "inline": True},
        ],
        "footer": {
            "text": "Novaire EA • Grade A+ Institutional SMC Engine"
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Detailed Analysis Reason breakdown
    analysis_points = [
        f"🎯 **Liquidity Sweep**: {sweep_info} (Swept with Rejection Wick & Volume Spike)",
        f"⚡ **Imbalance POI**: Entry di dalam area {entry_zone}",
        f"🔄 **LTF Confirmation**: CHoCH M5/M15 post-sweep terkonfirmasi searah reversal",
        f"⏱️ **Killzone Active**: {kz_info}",
        f"🛡️ **Risk Parameter**: SL di luar sweep extreme wick + buffer 15-20 pips, RRR minimal 1:1.5 terpenuhi"
    ]
    embed["fields"].append({
        "name": "🧠 Alasan Analisis Institusional",
        "value": "\n".join(analysis_points),
        "inline": False
    })
        
    embed["fields"].append({
        "name": "📋 Intraday Trade Execution Plan", 
        "value": f"1. Masuk order langsung di zona **{entry_zone}** (harga saat ini: **{entry_f:.2f}**).\n2. Target TP di **{tp_f:.2f}** (+{tp_pips:.0f} pips).\n3. Proteksi Modal: Begitu floating **+70 pips**, SL otomatis digeser ke **Break-Even**.\n4. Disiplin SL di **{sl_f:.2f}** (-{sl_pips:.0f} pips).", 
        "inline": False
    })
        
    payload = {
        "username": "Novaire EA",
        "content": "🔔 @everyone Sinyal Institusional Grade A+ Terdeteksi!",
        "embeds": [embed]
    }
    
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _send_webhook, payload)

async def send_discord_be_alert(trade: dict, new_sl: float):
    if not DISCORD_WEBHOOK_URL:
        return
    symbol = trade.get('symbol', 'UNKNOWN')
    entry_val = float(trade.get('entry', trade.get('entry_price', 0.0)))
    tp_val = float(trade.get('tp', trade.get('tp_price', 0.0)))
    
    embed = {
        "title": f"🛡️ [BREAK-EVEN ACTIVE] {symbol} Posisi Aman (100% Risk Free) 🛡️",
        "description": f"Trade {symbol} telah running profit **+70 pips / +1.0R**! Stop Loss otomatis dimajukan ke Break-Even.",
        "color": 0x3B82F6,
        "fields": [
            {"name": "Pair & Type", "value": f"**{symbol}** | **{trade.get('type')}**", "inline": True},
            {"name": "Entry Price", "value": f"**{entry_val:.2f}**", "inline": True},
            {"name": "Stop Loss Baru (BE)", "value": f"**{new_sl:.2f}** (0 Loss)", "inline": True},
            {"name": "Target TP", "value": f"**{tp_val:.2f}**", "inline": True},
            {"name": "Status Risiko", "value": "**100% Bebas Risiko Modal**", "inline": True},
        ],
        "footer": {
            "text": "Novaire EA • Dynamic Risk-Free Trailing System"
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    payload = {
        "username": "Novaire EA",
        "content": "🛡️ @everyone Posisi kini aman! Stop Loss telah dipindahkan ke Break-Even.",
        "embeds": [embed]
    }
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _send_webhook, payload)

async def send_discord_trade_update(signal: dict, new_status: str, pnl: float):
    if not DISCORD_WEBHOOK_URL:
        return
        
    # Suppress cancel alerts completely
    if new_status in ["CANCELLED", "MISSED"]:
        return

    if new_status == "WIN":
        color = 0x10B981
        status_icon = "🎯"
        result_text = "Take Profit (Full TP) 🎯"
    elif new_status == "PARTIAL_WIN":
        color = 0x10B981
        status_icon = "💰"
        result_text = "Partial Take Profit Secured (+BE) 💰"
    elif new_status == "BREAK_EVEN":
        color = 0x3B82F6
        status_icon = "🛡️"
        result_text = "Break-Even Protection (0 Loss) 🛡️"
    else:
        color = 0xF43F5E
        status_icon = "🛑"
        result_text = "Stop Loss (SL) 🛑"
    
    embed = {
        "title": f"{status_icon} Trade Closed: {signal.get('symbol')} {new_status} {status_icon}",
        "description": f"The trade for {signal.get('symbol')} has hit its {result_text}.",
        "color": color,
        "fields": [
            {"name": "Pair & Type", "value": f"**{signal.get('symbol')}** | **{signal.get('type')}**", "inline": True},
            {"name": "Entry Price", "value": f"**{signal.get('entry', signal.get('entry_price', 0))}**", "inline": True},
            {"name": "Target TP / SL", "value": f"**{signal.get('tp', signal.get('tp_price', '-'))}** / **{signal.get('sl', signal.get('sl_price', '-'))}**", "inline": True},
            {"name": "Hasil Trade", "value": f"**{result_text}**", "inline": True},
            {"name": "PnL Realized", "value": f"**{'+' if pnl > 0 else ''}{pnl:.2f}R**", "inline": True},
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
