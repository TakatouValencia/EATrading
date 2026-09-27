import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), 'local_trading.db')
conn = sqlite3.connect(db_path)
c = conn.cursor()

c.execute('''
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    type TEXT,
    entry_price REAL,
    sl_price REAL,
    tp_price REAL,
    reasons TEXT,
    status TEXT,
    created_at TEXT,
    pnl REAL,
    poi_signature TEXT
)
''')

# Clear old signals table
c.execute('DELETE FROM signals')

trades = [
    ('XAU/USD', 'SELL', 4417.00, 4422.00, 4396.40, '["SMC Grade A+"]', 'WIN', '2026-09-21T01:55:00', 3.12, 'poi_1'),
    ('XAU/USD', 'SELL', 4393.50, 4400.20, 4379.06, '["SMC Grade A+"]', 'WIN', '2026-09-21T15:35:00', 0.78, 'poi_2'),
    ('XAU/USD', 'SELL', 4368.90, 4375.90, 4351.80, '["SMC Grade A+"]', 'WIN', '2026-09-22T10:15:00', 1.94, 'poi_3'),
    ('XAU/USD', 'SELL', 4371.70, 4376.70, 4351.80, '["SMC Grade A+"]', 'BREAK_EVEN', '2026-09-22T13:25:00', 0.00, 'poi_4'),
    ('XAU/USD', 'SELL', 4370.50, 4375.50, 4351.80, '["SMC Grade A+"]', 'WIN', '2026-09-22T15:30:00', 1.07, 'poi_5'),
    ('XAU/USD', 'SELL', 4335.00, 4340.00, 4319.00, '["SMC Grade A+"]', 'WIN', '2026-09-24T01:15:00', 2.66, 'poi_6'),
    ('XAU/USD', 'SELL', 4327.90, 4333.00, 4314.38, '["SMC Grade A+"]', 'BREAK_EVEN', '2026-09-25T01:40:00', 0.00, 'poi_7'),
    ('XAU/USD', 'BUY', 4296.64, 4284.64, 4314.64, '["H4 Macro with H1 Pullback into POI"]', 'LOSS', '2026-09-25T16:08:00', -1.00, 'poi_8')
]

c.executemany('''
    INSERT INTO signals (symbol, type, entry_price, sl_price, tp_price, reasons, status, created_at, pnl, poi_signature)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
''', trades)

conn.commit()
conn.close()
print("Populated local_trading.db with 8 real weekly trades (including Friday 9/25 BUY SL).")
