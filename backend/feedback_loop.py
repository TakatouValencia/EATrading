import os
import json
from collections import defaultdict
from database import Database

def run_feedback_loop():
    print("=== RUNNING WEEKLY AI FEEDBACK LOOP ===")
    db = Database()
    
    # Fetch last 500 signals to get a good sample size
    signals = db.get_historical_signals(limit=500)
    
    if not signals:
        print("Not enough data to run feedback loop.")
        return
        
    wins = 0
    losses = 0
    
    # Group by grade
    grade_stats = {"A": {"wins": 0, "losses": 0}, "B": {"wins": 0, "losses": 0}, "NONE": {"wins": 0, "losses": 0}}
    
    # Group by session (Approximation based on timestamp hour)
    # Asia: 00:00 - 08:00 UTC
    # London: 08:00 - 13:00 UTC
    # NY: 13:00 - 21:00 UTC
    session_stats = {
        "Asia": {"wins": 0, "losses": 0},
        "London": {"wins": 0, "losses": 0},
        "New York": {"wins": 0, "losses": 0},
        "Other": {"wins": 0, "losses": 0}
    }
    
    # Group by symbol
    symbol_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
    
    for sig in signals:
        status = sig.get('status')
        if status not in ('WIN', 'LOSS'):
            continue
            
        is_win = status == 'WIN'
        
        if is_win:
            wins += 1
        else:
            losses += 1
            
        # Grade
        grade = sig.get('grade', 'NONE')
        if grade in grade_stats:
            if is_win:
                grade_stats[grade]["wins"] += 1
            else:
                grade_stats[grade]["losses"] += 1
                
        # Symbol
        symbol = sig.get('symbol', 'UNKNOWN')
        if is_win:
            symbol_stats[symbol]["wins"] += 1
        else:
            symbol_stats[symbol]["losses"] += 1
            
        # Session
        ts_str = sig.get('timestamp')
        if ts_str:
            try:
                from datetime import datetime
                ts_obj = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                hour = ts_obj.hour
                
                if 0 <= hour < 8:
                    sess = "Asia"
                elif 8 <= hour < 13:
                    sess = "London"
                elif 13 <= hour < 21:
                    sess = "New York"
                else:
                    sess = "Other"
                    
                if is_win:
                    session_stats[sess]["wins"] += 1
                else:
                    session_stats[sess]["losses"] += 1
            except:
                pass
                
    total_completed = wins + losses
    if total_completed == 0:
        print("No completed trades to analyze.")
        return
        
    overall_wr = (wins / total_completed) * 100
    
    print(f"\n--- OVERALL PERFORMANCE ---")
    print(f"Total Completed Trades: {total_completed}")
    print(f"Overall Win Rate: {overall_wr:.1f}%")
    
    print(f"\n--- PERFORMANCE BY GRADE ---")
    for g, s in grade_stats.items():
        t = s["wins"] + s["losses"]
        if t > 0:
            wr = (s["wins"] / t) * 100
            print(f"Grade {g}: {wr:.1f}% Win Rate ({s['wins']}W / {s['losses']}L)")
            
    print(f"\n--- PERFORMANCE BY SESSION ---")
    for sess, s in session_stats.items():
        t = s["wins"] + s["losses"]
        if t > 0:
            wr = (s["wins"] / t) * 100
            print(f"{sess}: {wr:.1f}% Win Rate ({s['wins']}W / {s['losses']}L)")
            
    print(f"\n--- AI RECOMMENDATIONS ---")
    recommendations = []
    
    # Generate insights
    for sess, s in session_stats.items():
        t = s["wins"] + s["losses"]
        if t >= 5: # Minimum sample size
            wr = (s["wins"] / t) * 100
            if wr < 40:
                recommendations.append(f"Consider halting trading during the {sess} session due to poor win rate ({wr:.1f}%).")
            elif wr > 65:
                recommendations.append(f"The {sess} session is highly profitable ({wr:.1f}%). Consider increasing risk slightly here.")
                
    grade_b = grade_stats.get("B", {})
    t_b = grade_b.get("wins", 0) + grade_b.get("losses", 0)
    if t_b >= 5:
        wr_b = (grade_b["wins"] / t_b) * 100
        if wr_b < 35:
            recommendations.append(f"Grade B setups are performing poorly ({wr_b:.1f}%). Consider turning them off or lowering risk further.")
            
    if not recommendations:
        print("- System is performing within expected parameters. Keep collecting data.")
    else:
        for r in recommendations:
            print(f"- {r}")
            
    # In a fully autonomous system, this script would now automatically update settings.json
    # with the new parameters based on these recommendations.
    print("\nFeedback loop analysis complete.")

if __name__ == "__main__":
    run_feedback_loop()
