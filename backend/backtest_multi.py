import asyncio
from data_provider import DataProvider
from trade_manager import TradeManager
from strategies.smc_strategy import SMCStrategy
from strategies.vwap_strategy import VWAPStrategy
from strategies.ema_pullback_strategy import EMAPullbackStrategy
from strategies.vol_breakout_strategy import VolatilityBreakoutStrategy
from strategies.session_breakout_strategy import SessionBreakoutStrategy
import json

class DummyDB:
    def update_signal_status(self, *args, **kwargs): pass
    def save_signal(self, signal): return {"id": 1}
    def get_historical_signals(self, limit=50): return []
    def save_blacklisted_zone(self, *args, **kwargs): pass
    def get_blacklisted_zones(self, *args, **kwargs): return set()

async def run_multi_backtest():
    print("Mulai Backtest Multi-Strategi XAU/USD (M15)...")
    dp = DataProvider()
    symbol = "XAU/USD"
    
    # Init strategies
    strategies = [
        SMCStrategy(),
        VWAPStrategy(),
        EMAPullbackStrategy(),
        VolatilityBreakoutStrategy(),
        SessionBreakoutStrategy()
    ]
    
    # Init state per strategy
    strategy_states = {}
    for strat in strategies:
        tm = TradeManager(DummyDB())
        
        # Capture stats per phase
        stats = {
            "IS": {"WIN": 0, "LOSS": 0, "pnl": 0.0, "current_consec_loss": 0, "max_consec_loss": 0, "peak_pnl": 0.0, "max_drawdown": 0.0, "total_signals": 0},
            "OOS": {"WIN": 0, "LOSS": 0, "pnl": 0.0, "current_consec_loss": 0, "max_consec_loss": 0, "peak_pnl": 0.0, "max_drawdown": 0.0, "total_signals": 0}
        }
        
        def make_on_close(st_stats):
            def on_close(trade, status, pnl):
                phase = trade.get('phase', 'IS')
                s = st_stats[phase]
                s[status] = s.get(status, 0) + 1
                s["pnl"] += pnl
                
                if status == "LOSS":
                    s["current_consec_loss"] += 1
                    if s["current_consec_loss"] > s["max_consec_loss"]:
                        s["max_consec_loss"] = s["current_consec_loss"]
                else:
                    s["current_consec_loss"] = 0
                    
                if s["pnl"] > s["peak_pnl"]:
                    s["peak_pnl"] = s["pnl"]
                
                current_dd = s["peak_pnl"] - s["pnl"]
                if current_dd > s["max_drawdown"]:
                    s["max_drawdown"] = current_dd
            return on_close
            
        tm.on_trade_closed = make_on_close(stats)
        
        strategy_states[strat.name] = {
            "strategy": strat,
            "tm": tm,
            "stats": stats
        }

    print("Mengambil data riwayat (M15)...")
    df_h4 = dp.get_historical_data(symbol, interval="1d", outputsize=100)
    df_h1 = dp.get_historical_data(symbol, interval="4h", outputsize=500)
    df_htf = dp.get_historical_data(symbol, interval="1h", outputsize=2000)
    df_ltf = dp.get_historical_data(symbol, interval="15min", outputsize=4000)
    
    if not df_htf or not df_ltf or not df_h1 or not df_h4:
        print("Gagal mengambil data!")
        return

    print(f"Data terkumpul: {len(df_h4)} candle 1D, {len(df_h1)} candle 4H, {len(df_htf)} candle 1H, {len(df_ltf)} candle M15.")
    
    split_idx = len(df_ltf) // 2
    window_size = 200
    spread_points = 3.0
    slippage_points = 1.0
    
    print("Menjalankan simulasi Walk-Forward paralel...")
    
    for i in range(window_size, len(df_ltf), 5):
        current_ltf_data = df_ltf[i-window_size:i]
        current_candle = current_ltf_data[-1]
        current_time = current_candle['timestamp']
        current_price = current_candle['close']
        
        current_htf_data = [c for c in df_htf if c['timestamp'] <= current_time][-window_size:]
        current_h1_data = [c for c in df_h1 if c['timestamp'] <= current_time][-window_size:]
        current_h4_data = [c for c in df_h4 if c['timestamp'] <= current_time][-window_size:]
        
        if len(current_htf_data) < 50: continue
        is_out_of_sample = i >= split_idx
        phase = "OOS" if is_out_of_sample else "IS"
        
        # Evaluate each strategy independently
        for name, state in strategy_states.items():
            strat = state['strategy']
            tm = state['tm']
            stats = state['stats']
            
            # Check for signal if no active trade
            if not tm.has_active_trade(symbol):
                signal = await strat.evaluate(symbol, current_price, current_time, 
                                              current_ltf_data, current_htf_data, 
                                              current_h1_data, current_h4_data)
                if signal:
                    # Apply slippage
                    if "BUY" in signal['type']:
                        signal['entry'] += spread_points
                        signal['sl'] -= slippage_points
                    else:
                        signal['entry'] -= spread_points
                        signal['sl'] += slippage_points
                        
                    signal['phase'] = phase
                    signal['id'] = stats[phase]["total_signals"] + 1
                    signal['symbol'] = symbol
                    signal['timestamp'] = current_time
                    
                    # If entry is at current price (accounting for spread), it's a market order (ACTIVE)
                    # If it's SMC with a limit order, it's PENDING
                    if abs(signal['entry'] - current_price) <= spread_points + 0.1:
                        signal['status'] = "ACTIVE"
                        signal['entry_timestamp'] = current_time
                        signal['mfe_price'] = current_price
                        signal['partial_taken'] = False
                    else:
                        signal['status'] = "PENDING"
                    
                    tm.add_trade(signal)
                    stats[phase]["total_signals"] += 1
                    
            # Process ticks for open trades (simulate O-H-L-C movement)
            for step_candle in df_ltf[i:min(i+2, len(df_ltf))]:
                await tm.process_tick({'symbol': symbol, 'price': step_candle['low']})
                await tm.process_tick({'symbol': symbol, 'price': step_candle['high']})
                await tm.process_tick({'symbol': symbol, 'price': step_candle['close']})

    # Output Results Table
    print("\n" + "="*80)
    print("HASIL KOMPARASI MULTI-STRATEGI (WALK-FORWARD)")
    print("="*80)
    
    # Helper to print phase
    def print_phase(phase_name, phase_key):
        print(f"\n[{phase_name}]")
        print(f"{'Strategi':<30} | {'Sinyal':<6} | {'Win Rate':<10} | {'Total R':<10} | {'Avg RR':<8} | {'Max DD':<8}")
        print("-" * 80)
        for name, state in strategy_states.items():
            stats = state['stats'][phase_key]
            total_sig = stats["total_signals"]
            win = stats.get("WIN", 0)
            loss = stats.get("LOSS", 0)
            total_closed = win + loss
            
            wr = (win / total_closed * 100) if total_closed > 0 else 0.0
            total_r = stats["pnl"]
            avg_rr = (total_r / win) if win > 0 else 0.0
            max_dd = stats["max_drawdown"]
            
            print(f"{name:<30} | {total_sig:<6} | {wr:>5.1f}%     | {total_r:>7.2f} R | {avg_rr:>6.2f} | {max_dd:>6.2f} R")
            
    print_phase("IN-SAMPLE (Bulan 1)", "IS")
    print_phase("OUT-OF-SAMPLE (Bulan 2)", "OOS")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(run_multi_backtest())
