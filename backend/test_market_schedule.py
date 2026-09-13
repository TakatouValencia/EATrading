import unittest
from datetime import datetime, timezone
from market_schedule import is_forex_market_open, is_killzone_active

class TestMarketSchedule(unittest.TestCase):
    def test_saturday_closed(self):
        # 2026-09-12 was Saturday
        sat_dt = datetime(2026, 9, 12, 14, 0, 0)
        is_open, reason = is_forex_market_open(sat_dt)
        self.assertFalse(is_open)
        self.assertIn("Saturday", reason)

        is_kz, kz_reason = is_killzone_active(sat_dt)
        self.assertFalse(is_kz)

    def test_sunday_before_open(self):
        # 2026-09-13 is Sunday at 14:00 UTC
        sun_dt = datetime(2026, 9, 13, 14, 0, 0)
        is_open, reason = is_forex_market_open(sun_dt)
        self.assertFalse(is_open)
        self.assertIn("Sunday", reason)

    def test_sunday_after_open(self):
        # 2026-09-13 Sunday at 21:30 UTC
        sun_open = datetime(2026, 9, 13, 21, 30, 0)
        is_open, reason = is_forex_market_open(sun_open)
        self.assertTrue(is_open)

    def test_friday_late_and_close(self):
        # Friday at 20:30 UTC -> Pre-weekend pause
        fri_pause = datetime(2026, 9, 11, 20, 30, 0)
        is_open, reason = is_forex_market_open(fri_pause)
        self.assertFalse(is_open)
        self.assertIn("Friday pre-weekend", reason)

        # Friday at 22:00 UTC -> Market closed
        fri_closed = datetime(2026, 9, 11, 22, 0, 0)
        is_open, reason = is_forex_market_open(fri_closed)
        self.assertFalse(is_open)

    def test_killzone_london_and_ny(self):
        # Tuesday at 08:30 UTC (London Killzone)
        tue_london = datetime(2026, 9, 8, 8, 30, 0)
        is_kz, reason = is_killzone_active(tue_london)
        self.assertTrue(is_kz)
        self.assertIn("London Killzone", reason)

        # Tuesday at 13:30 UTC (NY Killzone)
        tue_ny = datetime(2026, 9, 8, 13, 30, 0)
        is_kz, reason = is_killzone_active(tue_ny)
        self.assertTrue(is_kz)
        self.assertIn("New York Killzone", reason)

        # Tuesday at 11:15 UTC (Between London and NY)
        tue_mid = datetime(2026, 9, 8, 11, 15, 0)
        is_kz, reason = is_killzone_active(tue_mid)
        self.assertFalse(is_kz)

if __name__ == "__main__":
    unittest.main()
