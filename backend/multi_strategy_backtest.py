import os
import sys
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional
import json

from database import Database
from trade_manager import TradeManager
from data_provider import DataProvider
from smc_engine import SMCEngine

# Import Strategies
from strategies.smc_strategy import SMCStrategy
from strategies.vwap_strategy import VWAPStrategy
from strategies.ema_pullback_strategy import EMAPullbackStrategy
from strategies.vol_breakout_strategy import VolatilityBreakoutStrategy
from strategies.session_breakout_strategy import SessionBreakoutStrategy

SPREAD = 2.0  # 2 pips spread / slippage simulation

async def run_multi_backtest():
    print("Mulai Multi-Strategy Backtest XAU/USD...")
    
    symbol = "XAU/USD"
    # Init Data Provider
    data_provider = DataProvider()
    
    df_h4 = data_provider.get_historical_data(symbol, interval="1day", outputsize=200)
    df_h1 = data_provider.get_historical_data(symbol, interval="4h", outputsize=400)
    df_m15 = data_provider.get_historical_data(symbol, interval="1h", outputsize=1000)
    df_m1 = data_provider.get_historical_data(symbol, interval="15min", outputsize=4000)
    
    if not (df_h4 and df_h1 and df_m15 and df_m1):
        print("Data tidak lengkap atau gagal diunduh.")
        return
        
    print(f"Data terkumpul: {len(df_h4)} H4, {len(df_h1)} H1, {len(df_m15)} M15, {len(df_m1)} M1.")
    
    # Initialize strategies
    strategies = [
        SMCStrategy(),
        VWAPStrategy(),
        EMAPullbackStrategy(),
        VolatilityBreakoutStrategy(),
        SessionBreakoutStrategy()
    ]
    
    # Initialize separate TradeManagers for each strategy
    class MockDB:
        def __init__(self):
            self.trades = []
        def get_tracked_trades(self, symbol):
            return [t for t in self.trades if t['status'] in ['PENDING', 'ACTIVE']]
        def save_trade_update(self, t):
            pass
        def get_blacklisted_zones(self, symbol):
            return []
        def get_historical_signals(self, limit=50):
            return []
        def update_signal_status(self, *args, **kwargs):
            pass
        def save_signal(self, signal):
            return {"id": 1}
            
    trade_managers = {}
    for strat in strategies:
        tm = TradeManager(MockDB())
        trade_managers[strat.name] = tm
        
    # Performance tracking
    results = {
        strat.name: {
            "Total Signals": 0,
            "Wins": 0,
            "Losses": 0,
            "BE": 0,
            "Total R": 0.0,
            "Trending Wins": 0,
            "Trending Losses": 0,
            "Ranging Wins": 0,
            "Ranging Losses": 0,
            "Max DD (R)": 0.0,
            "Current DD (R)": 0.0,
            "Max Cons Losses": 0,
            "Current Cons Losses": 0
        } for strat in strategies
    }

    symbol = "XAU/USD"
    start_time_idx = int(len(df_m1) * 0.2) # Skip first 20% to build indicators
    
    print("Menjalankan simulasi Walk-Forward (ini mungkin memakan waktu beberapa menit)...")
    
    for i in range(start_time_idx, len(df_m1)):
        tick = df_m1[i]
        current_time = tick['timestamp']
        current_price = tick['close']
        
        # Avoid lookahead bias by slicing up to current_time
        current_ltf_data = [c for c in df_m15 if c['timestamp'] <= current_time]
        current_h1_data = [c for c in df_h1 if c['timestamp'] <= current_time]
        current_h4_data = [c for c in df_h4 if c['timestamp'] <= current_time]
        
        if len(current_ltf_data) < 50:
            continue
            
        # Global ADX Regime Filter (using H1)
        engine_h1 = SMCEngine(current_h1_data)
        adx_val = engine_h1.calculate_adx(period=14)
        is_trending = adx_val is not None and adx_val >= 25
        
        for strat in strategies:
            tm = trade_managers[strat.name]
            
            # Fast synchronous tick simulation for currently active trades
            sim_tick = {
                'timestamp': tick['timestamp'],
                'price': current_price,
                'high': tick['high'],
                'low': tick['low']
            }
            
            closed_trades = []
            for t in tm.tracked_trades:
                if t['status'] == 'ACTIVE':
                    entry = t['entry_price']
                    sl = t['sl_price']
                    tp = t['tp_price']
                    
                    if "BUY" in t['type']:
                        if sim_tick['low'] <= sl:
                            t['status'] = 'CLOSED'
                            t['pnl'] = -1.0 # -1 R
                            closed_trades.append(t)
                            strat.add_to_blacklist(entry) # Blacklist failed level
                        elif sim_tick['high'] >= tp:
                            t['status'] = 'CLOSED'
                            reward = (tp - entry) / (entry - sl) if entry != sl else 0
                            t['pnl'] = reward
                            closed_trades.append(t)
                    else: # SELL
                        if sim_tick['high'] >= sl:
                            t['status'] = 'CLOSED'
                            t['pnl'] = -1.0 # -1 R
                            closed_trades.append(t)
                            strat.add_to_blacklist(entry)
                        elif sim_tick['low'] <= tp:
                            t['status'] = 'CLOSED'
                            reward = (entry - tp) / (sl - entry) if sl != entry else 0
                            t['pnl'] = reward
                            closed_trades.append(t)
                            
            # Process closed trades to update stats
            for t in closed_trades:
                pnl = t.get('pnl', 0)
                res = results[strat.name]
                
                if pnl > 0:
                    res['Wins'] += 1
                    res['Total R'] += pnl
                    res['Current Cons Losses'] = 0
                    if t.get('regime') == 'TRENDING':
                        res['Trending Wins'] += 1
                    else:
                        res['Ranging Wins'] += 1
                        
                    # Update DD
                    if res['Current DD (R)'] > 0:
                        res['Current DD (R)'] = max(0, res['Current DD (R)'] - pnl)
                        
                elif pnl < 0:
                    res['Losses'] += 1
                    res['Total R'] += pnl
                    res['Current Cons Losses'] += 1
                    if res['Current Cons Losses'] > res['Max Cons Losses']:
                        res['Max Cons Losses'] = res['Current Cons Losses']
                        
                    if t.get('regime') == 'TRENDING':
                        res['Trending Losses'] += 1
                    else:
                        res['Ranging Losses'] += 1
                        
                    res['Current DD (R)'] += abs(pnl)
                    if res['Current DD (R)'] > res['Max DD (R)']:
                        res['Max DD (R)'] = res['Current DD (R)']
                        
                tm.tracked_trades.remove(t)
                
            # If no active trade, evaluate
            if not tm.has_active_trade(symbol):
                # Regime filters
                if strat.name == "EMA Trend + Pullback" and not is_trending:
                    continue
                if strat.name == "VWAP Mean Reversion" and is_trending:
                    continue
                    
                # Await evaluation
                signal = await strat.evaluate(
                    symbol=symbol,
                    current_price=current_price,
                    current_time=current_time,
                    df_ltf=current_ltf_data,
                    df_htf=current_h1_data,
                    df_h1=current_h1_data,
                    df_h4=current_h4_data
                )
                
                if signal:
                    # Apply Spread Simulation
                    if "BUY" in signal['type']:
                        signal['entry_price'] = signal['entry'] + SPREAD
                        signal['sl_price'] = signal['sl']
                        signal['tp_price'] = signal['tp']
                    else:
                        signal['entry_price'] = signal['entry'] - SPREAD
                        signal['sl_price'] = signal['sl'] + SPREAD
                        signal['tp_price'] = signal['tp'] - SPREAD
                        
                    signal['status'] = 'ACTIVE'
                    signal['symbol'] = symbol
                    signal['regime'] = 'TRENDING' if is_trending else 'RANGING'
                    tm.tracked_trades.append(signal)
                    results[strat.name]['Total Signals'] += 1

    # Print Final Report
    print("\n" + "="*80)
    print("MULTI-STRATEGY BACKTEST REPORT (XAU/USD)")
    print("="*80)
    
    report_lines = []
    
    for strat_name, res in results.items():
        total_trades = res['Wins'] + res['Losses']
        wr = (res['Wins'] / total_trades * 100) if total_trades > 0 else 0
        avg_r = (res['Total R'] / res['Wins']) if res['Wins'] > 0 else 0
        
        line = f"Strategy: {strat_name}\n"
        line += f"Total Signals: {res['Total Signals']} | Executed: {total_trades}\n"
        line += f"Win Rate: {wr:.2f}% ({res['Wins']}W / {res['Losses']}L)\n"
        line += f"Total R (PnL): {res['Total R']:.2f} R\n"
        line += f"Avg RR per Win: {avg_r:.2f} R\n"
        line += f"Max Drawdown: {res['Max DD (R)']:.2f} R\n"
        line += f"Max Cons Losses: {res['Max Cons Losses']}\n"
        line += f"Trending (W/L): {res['Trending Wins']}/{res['Trending Losses']} | Ranging (W/L): {res['Ranging Wins']}/{res['Ranging Losses']}\n"
        line += "-"*50
        print(line)
        report_lines.append(line)
        
    with open("multi_strategy_report.txt", "w") as f:
        f.write("\n".join(report_lines))
        
    print("Report saved to multi_strategy_report.txt")

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_multi_backtest())
