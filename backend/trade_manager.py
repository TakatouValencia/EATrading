from database import Database
from typing import Dict, List

from datetime import datetime, timedelta

class TradeManager:
    def __init__(self, db: Database, on_trade_closed=None):
        self.db = db
        self.tracked_trades = []
        self.on_trade_closed = on_trade_closed
        self.on_be_triggered = None
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.daily_completed_trades = 0
        self.daily_blacklisted_pools = set()
        self.current_trading_day = None
        self.current_time_str = None
        self._load_tracked_trades()
        self._load_daily_stats()

    def _load_daily_stats(self):
        """Compute today's consecutive losses, completed trades and PnL from database to persist state across restarts."""
        try:
            today = datetime.now().date()
            self.current_trading_day = today
            today_signals = self.db.get_today_signals(today)
            # Sort signals by timestamp ascending
            today_signals.sort(key=lambda x: x.get('timestamp', ''))
            
            consec_losses = 0
            daily_pnl = 0.0
            completed_c = 0
            
            for s in today_signals:
                status = s.get('status')
                pnl = float(s.get('pnl', 0.0) or 0.0)
                daily_pnl += pnl
                if status in ['WIN', 'LOSS', 'BREAK_EVEN', 'PARTIAL_WIN']:
                    completed_c += 1
                if status == 'WIN':
                    consec_losses = 0
                elif status == 'LOSS' or pnl < 0:
                    consec_losses += 1
            
            self.consecutive_losses = consec_losses
            self.daily_pnl = daily_pnl
            self.daily_completed_trades = completed_c
            print(f"[RISK] Initialized daily stats: PnL = {self.daily_pnl:.2f}R, Consecutive Losses = {self.consecutive_losses}/3, Completed Trades = {self.daily_completed_trades}")
        except Exception as e:
            print(f"[RISK] Error initializing daily stats: {e}")

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
            print(f"[RISK] New trading day detected ({today}). Resetting daily stats.")
            self.daily_pnl = 0.0
            self.consecutive_losses = 0
            self.daily_completed_trades = 0
            self.daily_blacklisted_pools = set()
            self.current_trading_day = today

    def is_pool_blacklisted(self, pool_name: str) -> bool:
        """Check if liquidity pool was already hit with SL today (Anti-Revenge / Zone Lockout)."""
        return pool_name in self.daily_blacklisted_pools

    def _update_stats(self, won: bool, pnl: float):
        self._check_daily_reset()
        self.daily_pnl += pnl
        self.daily_completed_trades += 1
        if won:
            self.consecutive_losses = 0
            print(f"[RISK] Trade Won! Consecutive losses reset to 0. Daily PnL: {self.daily_pnl:.2f}R | Completed: {self.daily_completed_trades}")
        elif pnl == 0.0:
            print(f"[RISK] Trade closed at Break-Even (0 PnL). Consecutive losses remain {self.consecutive_losses}/3. Daily PnL: {self.daily_pnl:.2f}R | Completed: {self.daily_completed_trades}")
        else:
            if pnl < 0:
                self.consecutive_losses += 1
                print(f"[RISK] Trade Lost (SL). Consecutive losses: {self.consecutive_losses}/3. Daily PnL: {self.daily_pnl:.2f}R | Completed: {self.daily_completed_trades}")
                if self.consecutive_losses >= 3:
                    print(f"[CIRCUIT BREAKER TRIGGERED] 3 Consecutive Stop Losses hit today! Trading is PAUSED until tomorrow.")

    def check_trading_allowed(self) -> tuple[bool, str]:
        """Check if trading is allowed based on psychological risk limits and daily trade limits."""
        self._check_daily_reset()
        if self.daily_pnl <= -3.0: # -3% max drawdown (assuming 1R = 1%)
            return False, f"Daily Drawdown Limit Reached ({self.daily_pnl:.2f}R / -3.0R)"
        if self.consecutive_losses >= 3: # 3 max consecutive losses
            return False, f"Max Consecutive Losses Reached ({self.consecutive_losses}/3 SLs today). Trading paused."
        try:
            import settings_manager
            cfg = settings_manager.load_settings()
            max_daily = int(cfg.get("max_daily_trades", 2))
            if self.daily_completed_trades >= max_daily:
                return False, f"Daily Trades Quota Reached ({self.daily_completed_trades}/{max_daily} trades today). Paused for quality trade harian."
        except Exception:
            pass
        return True, "Allowed"

    def _load_tracked_trades(self):
        recent_signals = self.db.get_historical_signals(limit=50)
        self.tracked_trades = [s for s in recent_signals if s['status'] in ('PENDING', 'ACTIVE')]
        print(f"[DEBUG] Loaded tracked trades: {len(self.tracked_trades)}")

    def add_trade(self, signal: Dict):
        # Instant execution on confirmation candle: Activate trade immediately (no missed/cancelled order)
        signal['status'] = 'ACTIVE'
        signal['entry_timestamp'] = str(self.current_time_str) if self.current_time_str else datetime.now().isoformat()
        signal['partial_taken'] = False
        signal['is_be'] = False
        signal['initial_sl'] = signal.get('sl')
            
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
        """Cancel all PENDING trades for a symbol silently (no discord cancel spam)."""
        for trade in self.tracked_trades[:]:
            if trade['symbol'] == symbol and trade['status'] == 'PENDING':
                trade['status'] = 'CANCELLED'
                if trade.get('id'):
                    self.db.update_signal_status(trade['id'], 'CANCELLED')
                self.tracked_trades.remove(trade)

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
                # Check for entry trigger
                triggered = False
                if is_buy and price <= entry:
                    triggered = True
                elif not is_buy and price >= entry:
                    triggered = True
                elif (is_buy and price > entry) or (not is_buy and price < entry):
                    # Market follow-through / front-run trigger
                    triggered = True
                    
                if triggered:
                    trade['status'] = 'ACTIVE'
                    trade['entry_timestamp'] = str(self.current_time_str) if self.current_time_str else datetime.now().isoformat()
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

                # Check for Time-Based Exit (> 48 hours)
                try:
                    entry_ts = trade.get('entry_timestamp')
                    if entry_ts:
                        entry_ts_obj = datetime.fromisoformat(entry_ts.replace('Z', '+00:00'))
                        if self.current_time_str:
                            now = datetime.fromisoformat(str(self.current_time_str).replace('Z', '+00:00'))
                        else:
                            now = datetime.now(entry_ts_obj.tzinfo)
                        if now.tzinfo is not None and entry_ts_obj.tzinfo is None:
                            now = now.replace(tzinfo=None)
                        elif now.tzinfo is None and entry_ts_obj.tzinfo is not None:
                            entry_ts_obj = entry_ts_obj.replace(tzinfo=None)
                        if now - entry_ts_obj > timedelta(hours=48):
                            won = (is_buy and price > entry) or (not is_buy and price < entry)
                            new_status = 'WIN' if won else 'LOSS'
                            initial_sl = float(trade.get('initial_sl', sl))
                            risk_dist = abs(entry - initial_sl) if abs(entry - initial_sl) > 0.01 else 1.0
                            pnl = abs(price - entry) / risk_dist if won else -abs(price - entry) / risk_dist
                            trade['status'] = new_status
                            self._update_stats(won, pnl)
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

                # Currency settings
                is_xau = "XAU" in symbol
                pip_unit = 0.10 if is_xau else 0.0001
                favorable_move = (price - entry) if is_buy else (entry - price)
                initial_sl = float(trade.get('initial_sl', sl))
                risk_dist = abs(entry - initial_sl) if abs(entry - initial_sl) > 0 else 0.0001

                # -------------------------------------------------------------
                # Multi-Stage Risk Management & Profit Banking:
                # Stage 1: +50 pips -> Trailing Stop moved to Break-Even (+0.5 pip)
                # Stage 2: +70 pips -> Bank 50% lot profit & protect remainder
                # Stage 3: Target TP (>120 - 250 pips) -> Full Institutional Winner
                # -------------------------------------------------------------
                be_trigger_dist = 5.0 * pip_unit if is_xau else 0.0005 # 50 pips
                partial_dist = 7.0 * pip_unit if is_xau else 0.0007    # 70 pips

                # Stage 1: Auto Break-Even Protection at +50 pips
                if favorable_move >= be_trigger_dist and not trade.get('is_be', False):
                    new_sl = round(entry + (0.5 * pip_unit) if is_buy else entry - (0.5 * pip_unit), 2 if is_xau else 5)
                    trade['sl_price'] = new_sl
                    trade['sl'] = new_sl
                    trade['is_be'] = True
                    sl = new_sl
                    print(f"[BE PROTECTION] {symbol} moved +{favorable_move/pip_unit:.0f}p. SL moved to Break-Even ({new_sl})")
                    if self.on_be_triggered:
                        import asyncio
                        if asyncio.iscoroutinefunction(self.on_be_triggered):
                            await self.on_be_triggered(trade, new_sl)
                        else:
                            self.on_be_triggered(trade, new_sl)

                # Stage 2: Bank 50% Profit at +70 pips / 1.0R
                if favorable_move >= partial_dist and not trade.get('partial_taken', False):
                    trade['partial_taken'] = True
                    locked_r = 0.5 * (favorable_move / risk_dist)
                    trade['locked_pnl'] = locked_r
                    print(f"[PARTIAL PROFIT (+70p)] {symbol} banked 50% lot (+{locked_r:.2f}R). Runner chasing Target TP.")

                # -------------------------------------------------------------
                # Stage 3: Check for Target TP / SL
                # -------------------------------------------------------------
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
                    if won:
                        if trade.get('partial_taken', False):
                            runner_r = 0.5 * (abs(tp - entry) / risk_dist)
                            pnl = round(trade.get('locked_pnl', 0.0) + runner_r, 2)
                        else:
                            pnl = round(abs(tp - entry) / risk_dist, 2)
                        new_status = 'WIN'
                    else: # lost (hit SL / BE)
                        if trade.get('partial_taken', False):
                            new_status = 'WIN'
                            pnl = round(trade.get('locked_pnl', 0.0), 2)
                            won = True # Counted as WIN because cash profit was secured!
                        elif trade.get('is_be', False):
                            new_status = 'BREAK_EVEN'
                            pnl = 0.0
                        else:
                            new_status = 'LOSS'
                            pnl = -1.0
                            # Zone Lockout: Blacklist pool for the rest of today
                            if 'sweep_pool' in trade and trade['sweep_pool']:
                                self.daily_blacklisted_pools.add(trade['sweep_pool'])
                        
                    mfe_dist = abs(trade['mfe_price'] - entry)
                    if (is_buy and trade['mfe_price'] > entry) or (not is_buy and trade['mfe_price'] < entry):
                        trade['mfe_r'] = mfe_dist / risk_dist
                    else:
                        trade['mfe_r'] = 0.0
                            
                    trade['status'] = new_status
                    self._update_stats(won, pnl)
                    
                    if new_status in ['WIN', 'LOSS', 'PARTIAL_WIN', 'BREAK_EVEN'] and 'poi_signature' in trade:
                        try:
                            self.db.save_blacklisted_zone(
                                symbol=trade['symbol'], 
                                signature=trade['poi_signature'], 
                                invalidated_at=datetime.now().isoformat()
                            )
                        except Exception as e:
                            pass
                    
                    if trade_id:
                        self.db.update_signal_status(trade_id, new_status, pnl)
                        
                    self.tracked_trades.remove(trade)
                    
                    if self.on_trade_closed:
                        import asyncio
                        if asyncio.iscoroutinefunction(self.on_trade_closed):
                            await self.on_trade_closed(trade, new_status, pnl)
                        else:
                            self.on_trade_closed(trade, new_status, pnl)
