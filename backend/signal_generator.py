from typing import Dict, List, Optional
from datetime import datetime, timedelta
from risk_calculator import calculate_pips, calculate_lot_size
import settings_manager

class SignalGenerator:
    def __init__(self, cooldown_hours: int = 1):
        self.active_signals = {}  # symbol -> signal_dict
        self.cooldowns = {}       # symbol -> expiration_time
        self.cooldown_hours = cooldown_hours

    def evaluate_confluence(self, symbol: str, current_price: float, 
                            events: List[Dict], obs: List[Dict], fvgs: List[Dict], sweeps: List[Dict] = None, htf_trend: str = None) -> Optional[Dict]:
        """
        Evaluate if a new signal should be generated based on SMC confluence.
        Rules:
        - Must have BOS/CHoCH in signal direction.
        - Plus 2 of: OB proximity, FVG unmitigated, Liquidity Sweep, HTF alignment.
        """
        
        # Check Cooldown
        if symbol in self.cooldowns:
            if datetime.now() < self.cooldowns[symbol]:
                return None
            else:
                del self.cooldowns[symbol]
                
        if not events:
            return None
            
        last_event = events[-1]
        is_bullish = "BULLISH" in last_event['type']
        
        # Strict HTF Alignment Check
        if htf_trend:
            if (is_bullish and htf_trend != "BULLISH") or (not is_bullish and htf_trend != "BEARISH"):
                return None
        
        # Check Confluence Criteria
        confluence_score = 0
        reasons = [last_event['type']]
        if htf_trend:
            reasons.append(f"HTF Trend Alignment ({htf_trend})")
        
        entry_target = None
        sl_target = None
        
        # Criterion 1: Order Block proximity / Valid OB
        valid_ob = None
        for ob in reversed(obs):
            if is_bullish and ob['type'] == "OB_BULLISH" and not ob['mitigated']:
                valid_ob = ob
                confluence_score += 1
                reasons.append("Valid Bullish OB")
                entry_target = ob['top']
                sl_target = ob['bottom'] - 0.5 # Small buffer for SL
                break
            elif not is_bullish and ob['type'] == "OB_BEARISH" and not ob['mitigated']:
                valid_ob = ob
                confluence_score += 1
                reasons.append("Valid Bearish OB")
                entry_target = ob['bottom']
                sl_target = ob['top'] + 0.5
                break

        # Criterion 2: FVG
        valid_fvg = None
        for fvg in reversed(fvgs):
            if is_bullish and fvg['type'] == "FVG_BULLISH" and not fvg['mitigated']:
                valid_fvg = fvg
                confluence_score += 1
                reasons.append("Unmitigated Bullish FVG")
                if not entry_target: # FVG can also act as entry if no OB
                    entry_target = fvg['top']
                    sl_target = fvg['bottom'] - 0.5
                break
            elif not is_bullish and fvg['type'] == "FVG_BEARISH" and not fvg['mitigated']:
                valid_fvg = fvg
                confluence_score += 1
                reasons.append("Unmitigated Bearish FVG")
                if not entry_target:
                    entry_target = fvg['bottom']
                    sl_target = fvg['top'] + 0.5
                break

        # Criterion 3: Liquidity Sweep
        if sweeps:
            for sweep in reversed(sweeps):
                if is_bullish and sweep['type'] == "SWEEP_BULLISH":
                    confluence_score += 1
                    reasons.append("Bullish Liquidity Sweep")
                    break
                elif not is_bullish and sweep['type'] == "SWEEP_BEARISH":
                    confluence_score += 1
                    reasons.append("Bearish Liquidity Sweep")
                    break

        # Check if Confluence met. Require stronger confluence (BOS + 2 other factors) to avoid spamming signals
        # and wait for proper entry confirmations to maintain high Win Rate (WR).
        if confluence_score >= 2 and entry_target and sl_target:
            signal_type = "BUY LIMIT" if is_bullish else "SELL LIMIT"
            
            # Very basic TP calculation (1:3 Risk Reward minimum)
            risk = abs(entry_target - sl_target)
            if is_bullish:
                tp_target = entry_target + (risk * 3)
            else:
                tp_target = entry_target - (risk * 3)
                
            # Calculate Lot Size
            settings = settings_manager.load_settings()
            acc_balance = float(settings.get("account_balance", 10000.0))
            risk_pct = float(settings.get("risk_percentage", 1.0))
            
            sl_pips = calculate_pips(symbol, entry_target, sl_target)
            lot_size = calculate_lot_size(acc_balance, risk_pct, sl_pips, symbol)
                
            signal = {
                "symbol": symbol,
                "type": signal_type,
                "timestamp": datetime.now().isoformat(),
                "entry": entry_target,
                "sl": sl_target,
                "tp": tp_target,
                "lot_size": lot_size,
                "reasons": reasons,
                "status": "PENDING"
            }
            
            self.active_signals[symbol] = signal
            
            # Set cooldown so we don't spam
            self.cooldowns[symbol] = datetime.now() + timedelta(hours=self.cooldown_hours)
            
            return signal
            
        return None

    def get_active_signal(self, symbol: str) -> Optional[Dict]:
        return self.active_signals.get(symbol)
        
    def clear_signal(self, symbol: str):
        if symbol in self.active_signals:
            del self.active_signals[symbol]
