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

    def add_trade(self, signal: Dict):
        # Reload to make sure we have the DB ID
        self._load_tracked_trades()

    def has_active_trade(self, symbol: str) -> bool:
        """Check if there is an ongoing PENDING or ACTIVE trade for the symbol."""
        for trade in self.tracked_trades:
            if trade['symbol'] == symbol and trade['status'] in ('PENDING', 'ACTIVE'):
                return True
        return False
    def process_tick(self, tick: Dict):
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
                # Check for entry trigger
                triggered = False
                if is_buy and price <= entry:
                    triggered = True
                elif not is_buy and price >= entry:
                    triggered = True
                    
                if triggered:
                    trade['status'] = 'ACTIVE'
                    if trade_id:
                        self.db.update_signal_status(trade_id, 'ACTIVE')
                        
            elif status == 'ACTIVE':
                # Check for TP / SL
                won = False
                lost = False
                
                if is_buy:
                    if price >= tp:
                        won = True
                    elif price <= sl:
                        lost = True
                else:
                    if price <= tp:
                        won = True
                    elif price >= sl:
                        lost = True
                        
                if won or lost:
                    new_status = 'WIN' if won else 'LOSS'
                    trade['status'] = new_status
                    
                    # Calculate simple RR PnL (assuming 1% risk and 3% RR for this MVP)
                    # Ideally this reads from lot_size and actual pip diff
                    pnl = 3.0 if won else -1.0 
                    
                    if trade_id:
                        self.db.update_signal_status(trade_id, new_status, pnl)
                        
                    self.tracked_trades.remove(trade)
                    
                    if self.on_trade_closed:
                        import asyncio
                        if asyncio.iscoroutinefunction(self.on_trade_closed):
                            asyncio.create_task(self.on_trade_closed(trade, new_status, pnl))
                        else:
                            self.on_trade_closed(trade, new_status, pnl)
