import sqlite3
import json
import traceback

try:
    conn = sqlite3.connect('local_trading.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM signals WHERE status IN ('PENDING', 'ACTIVE')")
    rows = cursor.fetchall()
    print("OPEN TRADES:")
    print(json.dumps(rows, indent=2))
    
    # Also let's check recent closed trades
    cursor.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 5")
    recent = cursor.fetchall()
    print("RECENT TRADES:")
    print(json.dumps(recent, indent=2))
    
    conn.close()
except Exception as e:
    print("Error:", e)
    traceback.print_exc()
