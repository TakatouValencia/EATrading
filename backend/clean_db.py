import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), 'local_trading.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("DELETE FROM signals WHERE status IN ('CANCELLED', 'MISSED', 'PENDING')")
cursor.execute("DELETE FROM signals WHERE symbol != 'XAU/USD'")
conn.commit()
conn.close()
print("All CANCELLED, MISSED, PENDING, and non-XAU/USD signals permanently cleaned.")
