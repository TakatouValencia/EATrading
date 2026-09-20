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

    async def test_outside_killzone_returns_none(self):
        # 05:00 UTC = 12:00 WIB (Outside London KZ 14:00-17:00 and NY KZ 19:30-22:30 WIB)
        asian_mid_time = "2026-09-08T05:00:00Z"
        res = await self.sg.evaluate_confluence(
            symbol="XAU/USD",
            current_price=2500.0,
            events=[{"type": "CHOCH_BULLISH"}],
            obs=[{"type": "OB_BULLISH", "bottom": 2490.0, "top": 2495.0, "mitigated": False}],
            fvgs=[],
            sweeps=[{"type": "SWEEP_BULLISH", "level": 2485.0, "has_rejection": True, "volume_spike": True}],
            current_time_str=asian_mid_time
        )
        self.assertIsNone(res, "Signal generator MUST return None outside London & NY Killzones.")

    async def test_counter_trend_without_h4_choch_rejected(self):
        # London KZ: 08:30 UTC = 15:30 WIB
        tue_time = "2026-09-08T08:30:00Z"
        # H4 is BEARISH, setup is BUY, but NO H4 CHoCH -> Must be rejected!
        res = await self.sg.evaluate_confluence(
            symbol="XAU/USD",
            current_price=2495.0,
            events=[{"type": "CHOCH_BULLISH"}],
            obs=[{"type": "OB_BULLISH", "bottom": 2490.0, "top": 2495.0, "mitigated": False}],
            fvgs=[{"type": "FVG_BULLISH", "bottom": 2492.0, "top": 2495.0, "mitigated": False}],
            sweeps=[{
                "type": "SWEEP_BULLISH", "level": 2485.0, "sweep_low": 2483.0,
                "pool_type": "ASIAN_LOW", "pool_name": "Asian Session Low",
                "has_rejection": True, "volume_spike": True
            }],
            h4_trend="BEARISH",
            d1_trend="BEARISH",
            h4_choch=None, # No CHoCH
            current_time_str=tue_time
        )
        self.assertIsNone(res, "Counter-trend BUY against Bearish H4 MUST be rejected without H4 CHoCH.")

    async def test_grade_a_plus_with_h4_choch_accepted(self):
        # London KZ: 08:30 UTC = 15:30 WIB
        tue_time = "2026-09-08T08:30:00Z"
        # H4 Bearish, but H4 Bullish CHoCH confirmed!
        # Liquidity Sweep of Asian Low at 2485.0 (sweep low 2483.0)
        # Entry at FVG [2490.0 - 2493.0], Resistance at 2520.0
        fvgs = [{"type": "FVG_BULLISH", "bottom": 2490.0, "top": 2493.0, "mitigated": False}]
        events = [{"type": "CHOCH_BULLISH", "level": 2496.0}]
        sweeps = [{
            "type": "SWEEP_BULLISH",
            "level": 2485.0,
            "sweep_low": 2483.0,
            "pool_type": "ASIAN_LOW",
            "pool_name": "Asian Session Low (2485.00)",
            "has_rejection": True,
            "volume_spike": True
        }]
        snr_zones = [{"type": "RESISTANCE", "level": 2520.0}]

        res = await self.sg.evaluate_confluence(
            symbol="XAU/USD",
            current_price=2492.5, # Inside FVG
            events=events,
            obs=[],
            fvgs=fvgs,
            sweeps=sweeps,
            h4_trend="BEARISH",
            d1_trend="BEARISH",
            h4_choch="CHOCH_BULLISH", # Reversal validated!
            snr_zones=snr_zones,
            atr=2.0,
            current_time_str=tue_time,
            trade_manager=self.tm
        )

        self.assertIsNotNone(res, "Setup with H4 CHoCH reversal, Asian Low sweep, and FVG must be accepted as Grade A+.")
        self.assertEqual(res['grade'], "A+")
        self.assertEqual(res['type'], "BUY")
        
        # SL is capped at max 70 pips (7.0): 2492.5 - 7.0 = 2485.50
        self.assertAlmostEqual(res['sl'], 2485.50, places=1)
        
        # RRR to TP1 must be >= 1.45 (1:1.5 minimum adaptive)
        risk = res['entry'] - res['sl']
        reward_tp1 = res['tp1'] - res['entry']
        self.assertGreaterEqual(reward_tp1 / risk, 1.45, "TP1 must satisfy at least 1:1.5 RRR")
        print(f"\n[TEST PASS] Grade A+ Setup: {res['symbol']} {res['type']} @ {res['entry']} | SL: {res['sl']} | TP1: {res['tp1']} | TP2: {res['tp2']} | RRR: 1:{res['rr_tp1']}")

    async def test_new_york_killzone_sell_setup(self):
        # NY KZ: 13:30 UTC = 20:30 WIB (Within 19:30 - 22:30 WIB)
        ny_time = "2026-09-08T13:30:00Z"
        # Sweep of PDH at 2530.0 (sweep high 2532.0)
        fvgs = [{"type": "FVG_BEARISH", "bottom": 2520.0, "top": 2523.0, "mitigated": False}]
        events = [{"type": "CHOCH_BEARISH", "level": 2518.0}]
        sweeps = [{
            "type": "SWEEP_BEARISH",
            "level": 2530.0,
            "sweep_high": 2532.0,
            "pool_type": "PDH",
            "pool_name": "Previous Day High / PDH (2530.00)",
            "has_rejection": True,
            "volume_spike": True
        }]
        snr_zones = [{"type": "SUPPORT", "level": 2508.0}]

        res = await self.sg.evaluate_confluence(
            symbol="XAU/USD",
            current_price=2521.5, # Inside Bearish FVG
            events=events,
            obs=[],
            fvgs=fvgs,
            sweeps=sweeps,
            h4_trend="BEARISH",
            d1_trend="BEARISH",
            snr_zones=snr_zones,
            atr=2.5,
            current_time_str=ny_time,
            trade_manager=self.tm
        )

        self.assertIsNotNone(res, "NY Killzone SELL setup on PDH sweep must be accepted.")
        self.assertEqual(res['grade'], "A+")
        self.assertEqual(res['type'], "SELL")
        # SL is capped at max 70 pips (7.0): 2521.5 + 7.0 = 2528.50
        self.assertAlmostEqual(res['sl'], 2528.50, places=1)
        self.assertGreaterEqual(res['rr_tp1'], 1.45)
        print(f"\n[TEST PASS] NY KZ Grade A+ SELL: {res['symbol']} {res['type']} @ {res['entry']} | SL: {res['sl']} | TP1: {res['tp1']} | TP2: {res['tp2']}")

    async def test_daily_trade_limit(self):
        self.tm.daily_completed_trades = 2
        allowed, reason = self.tm.check_trading_allowed()
        self.assertFalse(allowed)
        self.assertIn("Daily Trades Quota Reached", reason)

if __name__ == "__main__":
    unittest.main()

