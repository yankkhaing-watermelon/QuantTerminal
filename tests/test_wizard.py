import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant"))

from bmk_quant.wizard import build_wizard_candidates  # noqa: E402


def stock(symbol: str, **overrides):
    row = {
        "symbol": symbol,
        "tv_symbol": symbol,
        "name": f"{symbol} BHD",
        "sector": "Industrial",
        "close": 1.00,
        "last_bar": "2026-09-03",
        "rank": 1,
        "quant_score": 88.0,
        "quality_score": 72.0,
        "momentum_score": 90.0,
        "trend_score": 100.0,
        "risk_score": 70.0,
        "expected_edge": 8.0,
        "confidence": 86.0,
        "rs_20d": 6.0,
        "rsi": 58.0,
        "atr": 0.04,
        "volume_ratio": 1.8,
        "turnover": 3_000_000,
        "volatility": 38.0,
        "above_20dma": True,
        "above_50dma": True,
        "above_200dma": True,
        "new_20d_high": True,
        "new_52w_high": False,
        "price_z20": 1.6,
        "volume_z20": 2.0,
        "action": "ADD",
    }
    row.update(overrides)
    return row


class WizardDecisionTests(unittest.TestCase):
    def test_momentum_breakout_can_be_buy(self):
        rows = [stock("AAA")]
        activity = [{"symbol": "AAA", "activity_score": 78, "direction": "POSITIVE"}]
        candidates, summary = build_wizard_candidates(rows, activity, {"state": "RISK-ON"}, limit=15)
        self.assertEqual(candidates[0]["setup_code"], "W1")
        self.assertTrue(candidates[0]["entry_confirmed"])
        self.assertIn(candidates[0]["action"], {"BUY", "BUY CANDIDATE"})
        self.assertFalse(summary["additional_market_scan"])
        self.assertEqual(summary["source"], "same_daily_quant_snapshot")

    def test_held_weak_position_generates_defensive_action(self):
        rows = [stock(
            "WEAK",
            quant_score=35,
            momentum_score=25,
            trend_score=20,
            quality_score=45,
            expected_edge=-4,
            rs_20d=-8,
            new_20d_high=False,
            above_20dma=False,
            above_50dma=False,
            price_z20=-1.5,
            volume_z20=0.2,
            volume_ratio=0.8,
            action="REDUCE",
        )]
        candidates, _ = build_wizard_candidates(rows, [], {"state": "NEUTRAL"}, held_symbols={"WEAK"})
        self.assertIn(candidates[0]["action"], {"SELL", "TRIM"})
        self.assertTrue(candidates[0]["held"])

    def test_strong_risk_off_blocks_new_buy(self):
        rows = [stock("AAA")]
        activity = [{"symbol": "AAA", "activity_score": 90, "direction": "POSITIVE"}]
        candidates, _ = build_wizard_candidates(rows, activity, {"state": "STRONG RISK-OFF"})
        self.assertEqual(candidates[0]["action"], "AVOID")
        self.assertEqual(candidates[0]["risk_budget_pct"], 0.0)

    def test_limit_is_bounded_to_ten_to_twenty(self):
        rows = [stock(f"S{i:02d}", rank=i + 1, quant_score=95 - i) for i in range(30)]
        ten, summary_ten = build_wizard_candidates(rows, [], {"state": "RISK-ON"}, limit=3)
        twenty, summary_twenty = build_wizard_candidates(rows, [], {"state": "RISK-ON"}, limit=99)
        self.assertEqual(len(ten), 10)
        self.assertEqual(summary_ten["candidate_limit"], 10)
        self.assertEqual(len(twenty), 20)
        self.assertEqual(summary_twenty["candidate_limit"], 20)


if __name__ == "__main__":
    unittest.main()
