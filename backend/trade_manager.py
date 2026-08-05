from database import Database
from typing import Dict, List

class TradeManager:
    def __init__(self, db: Database, on_trade_closed=None):
        self.db = db
        self.tracked_trades = []
        self.on_trade_closed = on_trade_closed
        self._load_tracked_trades()

    def _load_tracked_trades(self):
        recent_signals = self.db.get_historical_signals(limit=50)
        self.tracked_trades = [s for s in recent_signals if s['status'] in ('PENDING', 'ACTIVE')]
        print(f"[DEBUG] Loaded tracked trades: {len(self.tracked_trades)}")

    def add_trade(self, signal: Dict):
        # Instead of reloading from DB, append directly to prevent state wipe on DB lock
        if signal not in self.tracked_trades:
            self.tracked_trades.append(signal)

    def has_active_trade(self, symbol: str) -> bool:
        """Check if there is an ongoing PENDING or ACTIVE trade for the symbol."""
        for trade in self.tracked_trades:
            if trade['symbol'] == symbol and trade['status'] in ('PENDING', 'ACTIVE'):
                return True
        return False
        
    def has_running_trade(self, symbol: str) -> bool:
        """Check if there is an ongoing ACTIVE (already triggered) trade. Ignores PENDING."""
        for trade in self.tracked_trades:
            if trade['symbol'] == symbol and trade['status'] == 'ACTIVE':
                return True
        return False

    async def cancel_pending_trades(self, symbol: str):
        """Cancel all PENDING trades for a symbol."""
        for trade in self.tracked_trades[:]:
            if trade['symbol'] == symbol and trade['status'] == 'PENDING':
                trade['status'] = 'CANCELLED'
                if trade.get('id'):
                    self.db.update_signal_status(trade['id'], 'CANCELLED')
                self.tracked_trades.remove(trade)
                if self.on_trade_closed:
                    import asyncio
                    if asyncio.iscoroutinefunction(self.on_trade_closed):
                        await self.on_trade_closed(trade, 'CANCELLED', 0)
                    else:
                        self.on_trade_closed(trade, 'CANCELLED', 0)
    async def process_tick(self, tick: Dict):
        """Evaluate tracked trades against current market price."""
        symbol = tick['symbol']
        price = tick['price']
        
        for trade in self.tracked_trades[:]:
            if trade['symbol'] != symbol:
                continue
                
            is_buy = "BUY" in trade['type']
            status = trade['status']
            
            entry = float(trade['entry_price']) if 'entry_price' in trade else float(trade['entry'])
            sl = float(trade['sl_price']) if 'sl_price' in trade else float(trade['sl'])
            tp = float(trade['tp_price']) if 'tp_price' in trade else float(trade['tp'])
            trade_id = trade.get('id')
            
            if status == 'PENDING':
                # Check for expiration (cancel if pending for > 4 hours)
                try:
                    from datetime import datetime, timedelta
                    ts = trade.get('timestamp', '')
                    if ts:
                        # Handle ISO formats
                        ts_obj = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        # Use naive datetime for comparison if ts_obj is naive, else aware
                        now = datetime.now(ts_obj.tzinfo)
                        if now - ts_obj > timedelta(hours=12):
                            trade['status'] = 'CANCELLED'
                            if trade_id:
                                self.db.update_signal_status(trade_id, 'CANCELLED')
                            self.tracked_trades.remove(trade)
                            
                            # Notify frontend
                            if self.on_trade_closed:
                                import asyncio
                                if asyncio.iscoroutinefunction(self.on_trade_closed):
                                    await self.on_trade_closed(trade, 'CANCELLED', 0)
                                else:
                                    self.on_trade_closed(trade, 'CANCELLED', 0)
                            continue
                except Exception as e:
                    print(f"Error checking expiration: {e}")

                # Check for entry trigger
                triggered = False
                if is_buy and price <= entry:
                    triggered = True
                elif not is_buy and price >= entry:
                    triggered = True
                    
                if triggered:
                    trade['status'] = 'ACTIVE'
                    from datetime import datetime
                    trade['entry_timestamp'] = datetime.now().isoformat()
                    trade['partial_taken'] = False
                    if trade_id:
                        self.db.update_signal_status(trade_id, 'ACTIVE')
                        
            elif status == 'ACTIVE':
                # Check for Time-Based Exit (> 4 hours)
                try:
                    from datetime import datetime, timedelta
                    entry_ts = trade.get('entry_timestamp')
                    if entry_ts:
                        entry_ts_obj = datetime.fromisoformat(entry_ts.replace('Z', '+00:00'))
                        now = datetime.now(entry_ts_obj.tzinfo)
                        if now - entry_ts_obj > timedelta(hours=4):
                            print(f"[{symbol}] Time-based exit for trade. Closing at market.")
                            won = (is_buy and price > entry) or (not is_buy and price < entry)
                            new_status = 'WIN' if won else 'LOSS'
                            pnl = 0.5 if won else -0.5
                            
                            trade['status'] = new_status
                            if trade_id:
                                self.db.update_signal_status(trade_id, new_status, pnl)
                            self.tracked_trades.remove(trade)
                            
                            if self.on_trade_closed:
                                import asyncio
                                if asyncio.iscoroutinefunction(self.on_trade_closed):
                                    await self.on_trade_closed(trade, new_status, pnl)
                                else:
                                    self.on_trade_closed(trade, new_status, pnl)
                            continue
                except Exception as e:
                    print(f"Error checking time-based exit: {e}")

                # Check for Smart Scaling Out (1R)
                if not trade.get('partial_taken', False):
                    one_r_dist = abs(entry - sl)
                    reached_1r = False
                    if is_buy and price >= (entry + one_r_dist):
                        reached_1r = True
                    elif not is_buy and price <= (entry - one_r_dist):
                        reached_1r = True
                        
                    if reached_1r:
                        print(f"[{symbol}] 1R Reached! Taking 50% partial profit and moving SL to Break Even.")
                        trade['partial_taken'] = True
                        if 'sl_price' in trade:
                            trade['sl_price'] = entry
                        else:
                            trade['sl'] = entry

                # Re-read SL because it might have been updated to BE
                current_sl = float(trade.get('sl_price', trade.get('sl')))

                # Check for TP / SL
                won = False
                lost = False
                
                if is_buy:
                    if price >= tp:
                        won = True
                    elif price <= current_sl:
                        lost = True
                else:
                    if price <= tp:
                        won = True
                    elif price >= current_sl:
                        lost = True
                        
                if won or lost:
                    new_status = 'WIN' if won else 'LOSS'
                    trade['status'] = new_status
                    
                    if won:
                        pnl = 2.0 if trade.get('partial_taken') else 3.0
                    else:
                        pnl = 0.5 if trade.get('partial_taken') else -1.0
                    
                    if trade_id:
                        self.db.update_signal_status(trade_id, new_status, pnl)
                        
                    self.tracked_trades.remove(trade)
                    
                    if self.on_trade_closed:
                        import asyncio
                        if asyncio.iscoroutinefunction(self.on_trade_closed):
                            await self.on_trade_closed(trade, new_status, pnl)
                        else:
                            self.on_trade_closed(trade, new_status, pnl)
