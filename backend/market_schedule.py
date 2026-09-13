from datetime import datetime, timezone
from typing import Tuple, Optional

def get_utc_datetime(dt: Optional[datetime] = None) -> datetime:
    """Normalize datetime to UTC datetime."""
    if dt is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    
    if isinstance(dt, (int, float)):
        return datetime.fromtimestamp(dt, tz=timezone.utc).replace(tzinfo=None)
        
    if isinstance(dt, str):
        try:
            ts = dt.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(ts)
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except Exception:
            return datetime.now(timezone.utc).replace(tzinfo=None)
            
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
        
    return dt

def is_forex_market_open(dt: Optional[datetime] = None, symbol: str = "XAU/USD") -> Tuple[bool, str]:
    """
    Check whether Forex/Metals (XAU/USD) market is officially open.
    
    Forex & Metals Market Hours (UTC):
    - Opens: Sunday 21:00 UTC (or 22:00 UTC depending on DST)
    - Closes: Friday 21:00 UTC (or 22:00 UTC)
    - Closed: Friday after 21:00 UTC, All Saturday, Sunday before 21:00 UTC.
    
    Friday Pre-Close Cutoff:
    - To prevent high-risk weekend holds, new trading signals are paused on Friday after 20:00 UTC.
    """
    utc_dt = get_utc_datetime(dt)
    weekday = utc_dt.weekday() # Monday=0, Tuesday=1, ..., Friday=4, Saturday=5, Sunday=6
    hour = utc_dt.hour
    minute = utc_dt.minute

    # Saturday: Market is 100% closed all day
    if weekday == 5:
        return False, "Market Closed (Weekend - Saturday)"

    # Sunday: Market is closed until 21:00 UTC
    if weekday == 6:
        if hour < 21:
            return False, f"Market Closed (Weekend - Sunday pre-market open at 21:00 UTC, current {hour:02d}:{minute:02d} UTC)"
        return True, "Market Open (Sunday Asia/Sydney Open)"

    # Friday: Closes at 21:00 UTC. New signals blocked after 20:00 UTC
    if weekday == 4:
        if hour >= 21:
            return False, f"Market Closed (Weekend - Friday market closed at 21:00 UTC, current {hour:02d}:{minute:02d} UTC)"
        elif hour >= 20:
            return False, f"Trading Paused (Friday pre-weekend close protection active after 20:00 UTC)"
        return True, "Market Open (Friday Regular Session)"

    # Monday through Thursday: Open 24 hours
    return True, f"Market Open (Regular Weekday {utc_dt.strftime('%A')})"

def is_killzone_active(dt: Optional[datetime] = None) -> Tuple[bool, str]:
    """
    Check if current UTC time falls within high-liquidity Institutional Killzones.
    
    ICT/SMC High-Probability Killzones for Daily Intraday Trading:
    - London Killzone: 07:00 - 10:30 UTC (Frankfurt/London liquidity injection)
    - New York Killzone: 12:00 - 15:30 UTC (NY open & US economic releases)
    
    Outside these hours, market volatility is often noisy or erratic (Asian range accumulation / late NY drift).
    """
    utc_dt = get_utc_datetime(dt)
    market_open, open_reason = is_forex_market_open(utc_dt)
    if not market_open:
        return False, open_reason

    hour = utc_dt.hour
    minute = utc_dt.minute
    current_time_dec = hour + minute / 60.0

    # London Killzone: 07:30 - 10:30 UTC (Filtered after initial London open Judas swing)
    if 7.5 <= current_time_dec <= 10.5:
        return True, "London Killzone (High Liquidity)"

    # New York Killzone: 12:30 - 15:30 UTC (Filtered after NY pre-market whipsaws)
    if 12.5 <= current_time_dec <= 15.5:
        return True, "New York Killzone (High Liquidity)"

    return False, f"Outside Killzone ({hour:02d}:{minute:02d} UTC - Trading paused for optimal daily liquidity)"
