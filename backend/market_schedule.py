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

def is_forex_market_open(dt: Optional[datetime] = None, symbol: str = "XAU/USD", check_real_time: bool = False) -> Tuple[bool, str]:
    """
    Check whether Forex/Metals (XAU/USD) market is officially open.
    
    Forex & Metals Market Hours (UTC):
    - Opens: Sunday 21:00 UTC (or 22:00 UTC depending on DST)
    - Closes: Friday 21:00 UTC (or 22:00 UTC)
    - Closed: Friday after 20:00 UTC (Pre-close safety pause), All Saturday, Sunday before 21:00 UTC.
    """
    # 1. Real-World Live Time Check (if check_real_time=True or dt is None)
    if check_real_time or dt is None:
        real_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        real_wday = real_utc.weekday()
        real_hr = real_utc.hour
        
        # Real-world Saturday
        if real_wday == 5:
            return False, f"Market Closed (Real-world Weekend - Saturday {real_utc.strftime('%H:%M')} UTC)"
        # Real-world Sunday before 21:00 UTC
        if real_wday == 6 and real_hr < 21:
            return False, f"Market Closed (Real-world Weekend - Sunday pre-market {real_utc.strftime('%H:%M')} UTC)"
        # Real-world Friday after 20:00 UTC
        if real_wday == 4 and real_hr >= 20:
            return False, f"Trading Paused (Real-world Friday weekend cutoff active after 20:00 UTC)"

    # 2. Historical / Tick Datetime Check
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
    Check if current UTC/WIB time falls within high-probability Killzones for XAUUSD.
    
    Session Times (WIB = UTC+7):
    - London Institutional Session: 13:00 - 17:30 WIB (06:00 - 10:30 UTC)
    - New York Institutional Session: 19:30 - 23:30 WIB (12:30 - 16:30 UTC)
    
    Outside these hours, signals are strictly ignored.
    """
    utc_dt = get_utc_datetime(dt)
    market_open, open_reason = is_forex_market_open(utc_dt)
    if not market_open:
        return False, open_reason

    hour = utc_dt.hour
    minute = utc_dt.minute
    current_time_dec = hour + minute / 60.0
    
    # Calculate WIB time for logging/display
    wib_hour = (hour + 7) % 24
    wib_str = f"{wib_hour:02d}:{minute:02d} WIB ({hour:02d}:{minute:02d} UTC)"

    # Session Times (WIB = UTC+7):
    # - Asian Institutional Session: 07:00 - 12:00 WIB (00:00 - 05:00 UTC)
    # - London Institutional Session: 12:00 - 18:00 WIB (05:00 - 11:00 UTC)
    # - New York Institutional Session: 18:00 - 01:00 WIB (11:00 - 18:00 UTC)
    # - Rollover & Spread Protection: 01:00 - 07:00 WIB (18:00 - 24:00 UTC) - PAUSED
    if 0.0 <= current_time_dec < 5.0:
        return True, f"Asian Institutional Session (07:00 - 12:00 WIB) [{wib_str}]"
    elif 5.0 <= current_time_dec < 11.0:
        return True, f"London Institutional Session (12:00 - 18:00 WIB) [{wib_str}]"
    elif 11.0 <= current_time_dec <= 18.0:
        return True, f"New York Institutional Session (18:00 - 01:00 WIB) [{wib_str}]"

    return False, f"Rollover / Pre-Asia Twilight [{wib_str}] - Setup paused for spread protection (Active 07:00-01:00 WIB)"


