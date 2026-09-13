import unittest
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
import asyncio

class TestCRTSnDSnR(unittest.TestCase):
    def test_crt_bullish_detection(self):
        # 3 candles:
        # c0: range 2500 - 2510
        # c1: sweeps 2500 down to 2495, closes at 2505 (inside c0 range)
        # c2: continuation
        candles = [
            {'timestamp': '2026-09-08T13:00:00', 'open': 2505.0, 'high': 2510.0, 'low': 2500.0, 'close': 2508.0, 'volume': 100},
            {'timestamp': '2026-09-08T13:05:00', 'open': 2508.0, 'high': 2508.0, 'low': 2495.0, 'close': 2505.0, 'volume': 200},
            {'timestamp': '2026-09-08T13:10:00', 'open': 2505.0, 'high': 2515.0, 'low': 2504.0, 'close': 2512.0, 'volume': 150}
        ]
        engine = SMCEngine(candles)
        crts = engine.detect_crt()
        self.assertEqual(len(crts), 1)
        self.assertEqual(crts[0]['type'], 'CRT_BULLISH')
        self.assertEqual(crts[0]['sweep_price'], 2495.0)
        self.assertEqual(crts[0]['reference_level'], 2500.0)
        self.assertEqual(crts[0]['target_price'], 2510.0)

    def test_crt_bearish_detection(self):
        # c0: range 2500 - 2510
        # c1: sweeps 2510 up to 2516, closes at 2506 (inside c0 range)
        candles = [
            {'timestamp': '2026-09-08T13:00:00', 'open': 2502.0, 'high': 2510.0, 'low': 2500.0, 'close': 2508.0, 'volume': 100},
            {'timestamp': '2026-09-08T13:05:00', 'open': 2508.0, 'high': 2516.0, 'low': 2504.0, 'close': 2506.0, 'volume': 250},
            {'timestamp': '2026-09-08T13:10:00', 'open': 2506.0, 'high': 2507.0, 'low': 2498.0, 'close': 2500.0, 'volume': 180}
        ]
        engine = SMCEngine(candles)
        crts = engine.detect_crt()
        self.assertEqual(len(crts), 1)
        self.assertEqual(crts[0]['type'], 'CRT_BEARISH')
        self.assertEqual(crts[0]['sweep_price'], 2516.0)
        self.assertEqual(crts[0]['reference_level'], 2510.0)
        self.assertEqual(crts[0]['target_price'], 2500.0)

    def test_strict_grade_a_plus_rejection(self):
        # If score < 9, evaluate_confluence MUST return None
        sg = SignalGenerator(cooldown_minutes=0)
        
        fake_time = "2026-09-08T08:30:00" # Tuesday 08:30 UTC
        
        async def run_low_score():
            sig = await sg.evaluate_confluence(
                symbol="XAU/USD",
                current_price=2500.0,
                events=[],
                obs=[],
                fvgs=[],
                current_time_str=fake_time,
                h1_trend=None, # Missing H1 trend
                m15_trend="BULLISH",
                m5_trend="BEARISH"
            )
            return sig

        res = asyncio.run(run_low_score())
        self.assertIsNone(res, "Setup with low confluence score must be strictly rejected (Grade A+ only)")

if __name__ == '__main__':
    unittest.main()
