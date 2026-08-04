import sqlite3

conn = sqlite3.connect('local_trading.db')
cursor = conn.cursor()
cursor.execute("UPDATE signals SET status = 'CANCELLED' WHERE status = 'PENDING'")
conn.commit()
conn.close()
print("All PENDING signals set to CANCELLED")
