import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), 'local_trading.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("UPDATE signals SET status = 'CANCELLED' WHERE status = 'PENDING'")
cursor.execute("DELETE FROM signals WHERE symbol != 'XAU/USD'")
conn.commit()
conn.close()
print("All PENDING signals set to CANCELLED and non-XAU/USD signals deleted")
