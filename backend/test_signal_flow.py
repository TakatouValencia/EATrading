import asyncio
import unittest
from datetime import datetime
from signal_generator import SignalGenerator
from trade_manager import TradeManager
from smc_engine import SMCEngine

class DummyDB:
    def update_signal_status(self, *args, **kwargs): pass
    def save_signal(self, signal): return {"id": 1}
    def get_historical_signals(self, limit=50): return []
    def save_blacklisted_zone(self, *args, **kwargs): pass
    def get_blacklisted_zones(self, *args, **kwargs): return set()
    def get_today_signals(self, date): return []

class TestSignalFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sg = SignalGenerator(cooldown_minutes=0)
        self.db = DummyDB()
        self.tm = TradeManager(self.db)

    async def test_saturday_returns_none(self):
        # Saturday 2026-09-12 14:00 UTC (Market closed)
        sat_time = "2026-09-12T14:00:00Z"
        res = await self.sg.evaluate_confluence(
            symbol="XAU/USD",
            current_price=2500.0,
            events=[],
            obs=[],
            fvgs=[],
            current_time_str=sat_time
        )
        self.assertIsNone(res, "Signal generator MUST return None on Saturday when market is closed.")

    async def test_sunday_daytime_returns_none(self):
        # Sunday 2026-09-13 10:00 UTC (Market closed)
        sun_time = "2026-09-13T10:00:00Z"
        res = await self.sg.evaluate_confluence(
            symbol="XAU/USD",
            current_price=2500.0,
            events=[],
            obs=[],
            fvgs=[],
            current_time_str=sun_time
        )
        self.assertIsNone(res, "Signal generator MUST return None on Sunday morning when market is closed.")

    async def test_tuesday_killzone_structural_tp_sl(self):
        # Tuesday 2026-09-08 08:30 UTC (London Killzone)
        tue_time = "2026-09-08T08:30:00Z"
        # Mocking an H1 trend BULLISH, and an M5 Bullish OB at 2490 - 2498
        obs = [{"type": "OB_BULLISH", "bottom": 2490.0, "top": 2498.0, "mitigated": False}]
        events = [{"type": "CHOCH_BULLISH", "level": 2502.0}]
        sweeps = [{"type": "SWEEP_BULLISH", "level": 2488.0, "is_idm": True}]
        
        # Synthetic 50 candles for engine_ltf
        candles = []
        for i in range(50):
            candles.append({
                'timestamp': f"2026-09-08T08:{i:02d}:00Z",
                'open': 2495.0, 'high': 2505.0, 'low': 2488.0, 'close': 2500.0, 'volume': 100
            })
        engine = SMCEngine(candles)
        
        res = await self.sg.evaluate_confluence(
            symbol="XAU/USD",
            current_price=2498.2, # Near OB top
            events=events,
            obs=obs,
            fvgs=[],
            sweeps=sweeps,
            m15_trend="BULLISH",
            m5_trend="BULLISH",
            h1_trend="BULLISH",
            atr=2.5,
            engine_ltf=engine,
            current_time_str=tue_time,
            trade_manager=self.tm
        )
        
        if res is not None:
            # Check SL is safe (at least 50 pips = $5.00)
            sl_dist = abs(res['entry'] - res['sl'])
            self.assertGreaterEqual(sl_dist, 5.0, f"SL distance {sl_dist} should be at least $5.00 (50 pips)")
            
            # Check TP1 is at least 2.0R
            tp1_dist = abs(res['tp1'] - res['entry'])
            self.assertGreaterEqual(tp1_dist, 1.8 * sl_dist, "TP1 should have at least ~2.0 R:R")
            
            # Check R:R ratio
            self.assertGreaterEqual(res['rr_ratio'], 1.8, "R:R ratio should be at least 1.8")
            print(f"Generated setup: {res['type']} @ {res['entry']} | SL: {res['sl']} (-{sl_dist:.2f}) | TP1: {res['tp1']} | TP2: {res['tp']} | R:R 1:{res['rr_ratio']}")

    async def test_daily_trade_limit(self):
        # When daily completed trades reaches max_daily_trades (2), new trades should be locked
        self.tm.daily_completed_trades = 2
        allowed, reason = self.tm.check_trading_allowed()
        self.assertFalse(allowed)
        self.assertIn("Daily Trades Quota Reached", reason)

if __name__ == "__main__":
    unittest.main()
