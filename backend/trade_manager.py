from database import Database
from typing import Dict, List

from datetime import datetime, timedelta

class TradeManager:
    def __init__(self, db: Database, on_trade_closed=None):
        self.db = db
        self.tracked_trades = []
        self.on_trade_closed = on_trade_closed
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.current_trading_day = None
        self.current_time_str = None
        self._load_tracked_trades()

    def _check_daily_reset(self):
        if not self.current_time_str:
            return
            
        if isinstance(self.current_time_str, (int, float)):
            today = datetime.fromtimestamp(self.current_time_str).date()
        elif isinstance(self.current_time_str, str):
            try:
                today = datetime.fromisoformat(self.current_time_str.replace('Z', '+00:00')).date()
            except Exception:
                today = datetime.now().date()
        else:
            today = datetime.now().date()

        if self.current_trading_day is None:
            self.current_trading_day = today
            
        if today != self.current_trading_day:
            self.daily_pnl = 0.0
            self.consecutive_losses = 0
            self.current_trading_day = today

    def _update_stats(self, won: bool, pnl: float):
        self._check_daily_reset()
        self.daily_pnl += pnl
        if won:
            self.consecutive_losses = 0
        else:
            if pnl < 0:
                self.consecutive_losses += 1

    def check_trading_allowed(self) -> tuple[bool, str]:
        """Check if trading is allowed based on psychological risk limits."""
        self._check_daily_reset()
        if self.daily_pnl <= -3.0: # -3% max drawdown (assuming 1R = 1%)
            return False, f"Daily Drawdown Limit Reached ({self.daily_pnl}R)"
        if self.consecutive_losses >= 3: # 3 max consecutive losses
            return False, f"Max Consecutive Losses Reached ({self.consecutive_losses})"
        return True, "Allowed"

    def _load_tracked_trades(self):
        recent_signals = self.db.get_historical_signals(limit=50)
        self.tracked_trades = [s for s in recent_signals if s['status'] in ('PENDING', 'ACTIVE')]
        print(f"[DEBUG] Loaded tracked trades: {len(self.tracked_trades)}")

    def add_trade(self, signal: Dict):
        # Instead of reloading from DB, append directly to prevent state wipe on DB lock
        if signal.get('signal_type') == 'CONFIRMED' and signal['status'] == 'PENDING':
            signal['status'] = 'ACTIVE'
            from datetime import datetime
            signal['entry_timestamp'] = datetime.now().isoformat()
            signal['partial_taken'] = False
            
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
        if 'timestamp' in tick:
            self.current_time_str = tick['timestamp']
            self._check_daily_reset()
        
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
                        if now - ts_obj > timedelta(minutes=30):
                            print(f"[{symbol}] PENDING trade expired (> 30 mins). Cancelling.")
                            trade['status'] = 'CANCELLED'
                            
                            if 'poi_signature' in trade:
                                try:
                                    self.db.save_blacklisted_zone(
                                        symbol=trade['symbol'], 
                                        signature=trade['poi_signature'], 
                                        invalidated_at=datetime.now().isoformat()
                                    )
                                except Exception as e:
                                    print(f"Error blacklisting zone on expiry: {e}")
                                    
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

                # Check for Missed Trade (price hits TP before Entry)
                missed = False
                if is_buy and price >= tp:
                    missed = True
                elif not is_buy and price <= tp:
                    missed = True
                    
                if missed:
                    print(f"[{symbol}] PENDING trade missed (hit TP before Entry). Cancelling.")
                    trade['status'] = 'MISSED'
                    
                    if 'poi_signature' in trade:
                        try:
                            from datetime import datetime
                            self.db.save_blacklisted_zone(
                                symbol=trade['symbol'], 
                                signature=trade['poi_signature'], 
                                invalidated_at=datetime.now().isoformat()
                            )
                        except Exception as e:
                            print(f"Error blacklisting zone on miss: {e}")
                            
                    if trade_id:
                        self.db.update_signal_status(trade_id, 'MISSED')
                    self.tracked_trades.remove(trade)
                    if self.on_trade_closed:
                        import asyncio
                        if asyncio.iscoroutinefunction(self.on_trade_closed):
                            await self.on_trade_closed(trade, 'MISSED', 0)
                        else:
                            self.on_trade_closed(trade, 'MISSED', 0)
                    continue

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
                    trade['mfe_price'] = price
                    if trade_id:
                        self.db.update_signal_status(trade_id, 'ACTIVE')
                        
            if trade['status'] == 'ACTIVE':
                if 'mfe_price' not in trade:
                    trade['mfe_price'] = price
                if is_buy:
                    trade['mfe_price'] = max(trade['mfe_price'], price)
                else:
                    trade['mfe_price'] = min(trade['mfe_price'], price)

                # Check for Time-Based Exit (> 4 hours)
                try:
                    from datetime import datetime, timedelta
                    entry_ts = trade.get('entry_timestamp')
                    if entry_ts:
                        entry_ts_obj = datetime.fromisoformat(entry_ts.replace('Z', '+00:00'))
                        now = datetime.now(entry_ts_obj.tzinfo)
                        if now - entry_ts_obj > timedelta(hours=48):
                            print(f"[{symbol}] Time-based exit for trade. Closing at market.")
                            won = (is_buy and price > entry) or (not is_buy and price < entry)
                            new_status = 'WIN' if won else 'LOSS'
                            
                            risk_dist = abs(entry - sl) if abs(entry - sl) > 0 else 0.0001
                            pnl = abs(price - entry) / risk_dist if won else -abs(price - entry) / risk_dist
                            
                            mfe_dist = abs(trade['mfe_price'] - entry)
                            if (is_buy and trade['mfe_price'] > entry) or (not is_buy and trade['mfe_price'] < entry):
                                trade['mfe_r'] = mfe_dist / risk_dist
                            else:
                                trade['mfe_r'] = 0.0
                            
                            trade['status'] = new_status
                            self._update_stats(won, pnl)
                            
                            if new_status == 'LOSS' and 'poi_signature' in trade:
                                self.db.save_blacklisted_zone(
                                    symbol=trade['symbol'], 
                                    signature=trade['poi_signature'], 
                                    invalidated_at=datetime.now().isoformat()
                                )

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

                atr = float(trade.get('atr', abs(entry - sl) / 1.5)) # fallback to inferred ATR
                
                # Check for TP / SL
                won = False
                lost = False
                
                if is_buy:
                    if price <= sl:
                        lost = True
                    elif price >= tp:
                        won = True
                else:
                    if price >= sl:
                        lost = True
                    elif price <= tp:
                        won = True
                        
                if won or lost:
                    risk_dist = abs(entry - sl) if abs(entry - sl) > 0 else 0.0001
                    if won:
                        new_status = 'WIN'
                        pnl = abs(tp - entry) / risk_dist
                    else:
                        new_status = 'LOSS'
                        pnl = -1.0
                        
                    mfe_dist = abs(trade['mfe_price'] - entry)
                    if (is_buy and trade['mfe_price'] > entry) or (not is_buy and trade['mfe_price'] < entry):
                        trade['mfe_r'] = mfe_dist / risk_dist
                    else:
                        trade['mfe_r'] = 0.0
                            
                    trade['status'] = new_status
                    
                    self._update_stats(won, pnl)
                    
                    if new_status in ['WIN', 'LOSS', 'PARTIAL_WIN'] and 'poi_signature' in trade:
                        try:
                            from datetime import datetime
                            self.db.save_blacklisted_zone(
                                symbol=trade['symbol'], 
                                signature=trade['poi_signature'], 
                                invalidated_at=datetime.now().isoformat()
                            )
                        except Exception as e:
                            print(f"Error blacklisting zone: {e}")
                    
                    if trade_id:
                        self.db.update_signal_status(trade_id, new_status, pnl)
                        
                    self.tracked_trades.remove(trade)
                    
                    if self.on_trade_closed:
                        import asyncio
                        if asyncio.iscoroutinefunction(self.on_trade_closed):
                            await self.on_trade_closed(trade, new_status, pnl)
                        else:
                            self.on_trade_closed(trade, new_status, pnl)
