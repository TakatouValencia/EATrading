import os
import sqlite3
import json
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

class Database:
    def __init__(self):
        self.supabase: Client = None
        self.use_sqlite = False
        self.db_path = os.path.join(os.path.dirname(__file__), 'local_trading.db')
        
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
                print("Connected to Supabase")
            except Exception as e:
                print(f"Failed to connect to Supabase: {e}")
                self._init_sqlite()
        else:
            print("WARNING: Supabase credentials missing. Falling back to SQLite local database.")
            self._init_sqlite()

    def _init_sqlite(self):
        self.use_sqlite = True
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
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
                pnl REAL
            )
        ''')
        conn.commit()
        conn.close()

    def save_signal(self, signal: dict):
        """Save a new signal to the database."""
        if self.use_sqlite:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO signals (symbol, type, entry_price, sl_price, tp_price, reasons, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    signal['symbol'],
                    signal['type'],
                    signal['entry'],
                    signal['sl'],
                    signal['tp'],
                    json.dumps(signal['reasons']),
                    signal['status'],
                    signal['timestamp']
                ))
                last_id = cursor.lastrowid
                conn.commit()
                conn.close()
                return {"id": last_id, "success": True}
            except Exception as e:
                print(f"Error saving signal to SQLite: {e}")
                return None
        elif self.supabase:
            try:
                # Assuming table name is 'signals'
                data, count = self.supabase.table('signals').insert({
                    "symbol": signal['symbol'],
                    "type": signal['type'],
                    "entry_price": signal['entry'],
                    "sl_price": signal['sl'],
                    "tp_price": signal['tp'],
                    "reasons": signal['reasons'],
                    "status": signal['status'],
                    "created_at": signal['timestamp']
                }).execute()
                return data
            except Exception as e:
                print(f"Error saving signal: {e}")
                
    def get_historical_signals(self, limit: int = 100):
        """Fetch historical signals for track record."""
        if self.use_sqlite:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM signals ORDER BY created_at DESC LIMIT ?', (limit,))
                rows = cursor.fetchall()
                conn.close()
                
                results = []
                for row in rows:
                    r_dict = dict(row)
                    r_dict['reasons'] = json.loads(r_dict['reasons']) if r_dict['reasons'] else []
                    r_dict['entry'] = r_dict.pop('entry_price', 0)
                    r_dict['sl'] = r_dict.pop('sl_price', 0)
                    r_dict['tp'] = r_dict.pop('tp_price', 0)
                    r_dict['timestamp'] = r_dict.pop('created_at', '')
                    results.append(r_dict)
                return results
            except Exception as e:
                print(f"Error fetching historical signals from SQLite: {e}")
                return []
        elif self.supabase:
            try:
                response = self.supabase.table('signals').select("*").order('created_at', desc=True).limit(limit).execute()
                results = []
                for r_dict in response.data:
                    r_dict['entry'] = r_dict.pop('entry_price', 0)
                    r_dict['sl'] = r_dict.pop('sl_price', 0)
                    r_dict['tp'] = r_dict.pop('tp_price', 0)
                    r_dict['timestamp'] = r_dict.pop('created_at', '')
                    results.append(r_dict)
                return results
            except Exception as e:
                print(f"Error fetching historical signals: {e}")
                return []
        return []

    def update_signal_status(self, signal_id: int, status: str, pnl: float = None):
        """Update signal status (e.g., WIN, LOSS, CANCELLED)."""
        if self.use_sqlite:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                if pnl is not None:
                    cursor.execute('UPDATE signals SET status = ?, pnl = ? WHERE id = ?', (status, pnl, signal_id))
                else:
                    cursor.execute('UPDATE signals SET status = ? WHERE id = ?', (status, signal_id))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"Error updating signal in SQLite: {e}")
        elif self.supabase:
            try:
                update_data = {"status": status}
                if pnl is not None:
                    update_data["pnl"] = pnl
                self.supabase.table('signals').update(update_data).eq("id", signal_id).execute()
            except Exception as e:
                print(f"Error updating signal: {e}")

    def get_statistics(self):
        """Calculate statistics like win rate."""
        if self.use_sqlite:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT status, COUNT(*) FROM signals WHERE status IN ('WIN', 'LOSS') GROUP BY status")
                rows = cursor.fetchall()
                conn.close()
                wins = 0
                losses = 0
                for status, count in rows:
                    if status == 'WIN':
                        wins = count
                    elif status == 'LOSS':
                        losses = count
                
                total = wins + losses
                win_rate = (wins / total * 100) if total > 0 else 0
                return {
                    "win_rate": round(win_rate, 1),
                    "total_trades": total,
                    "wins": wins,
                    "losses": losses
                }
            except Exception as e:
                print(f"Error fetching stats from SQLite: {e}")
                return {"win_rate": 0, "total_trades": 0, "wins": 0, "losses": 0}
        elif self.supabase:
            try:
                response = self.supabase.table('signals').select('status').in_('status', ['WIN', 'LOSS']).execute()
                wins = sum(1 for r in response.data if r['status'] == 'WIN')
                losses = sum(1 for r in response.data if r['status'] == 'LOSS')
                total = wins + losses
                win_rate = (wins / total * 100) if total > 0 else 0
                return {
                    "win_rate": round(win_rate, 1),
                    "total_trades": total,
                    "wins": wins,
                    "losses": losses
                }
            except Exception as e:
                print(f"Error fetching stats: {e}")
                return {"win_rate": 0, "total_trades": 0, "wins": 0, "losses": 0}
        return {"win_rate": 0, "total_trades": 0, "wins": 0, "losses": 0}
