import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant"))

from bmk_quant.activity import (  # noqa: E402
    ACTIVITY_METHODOLOGY,
    activity_direction,
    activity_score,
    build_unexplained_activity,
    latest_prior_zscore,
    should_flag,
)


class ActivityMonitorTests(unittest.TestCase):
    def test_latest_zscore_excludes_current_observation_from_baseline(self):
        values = pd.Series([float(index) for index in range(1, 21)] + [50.0])
        z = latest_prior_zscore(values)
        self.assertIsNotNone(z)
        self.assertGreater(z, 6.0)

    def test_trigger_is_single_three_sigma_or_two_two_sigma_factors(self):
        self.assertTrue(should_flag({"price_return": 3.1, "volume": 0.5, "turnover": 0.4, "relative_strength": 0.3}))
        self.assertTrue(should_flag({"price_return": 2.1, "volume": 2.2, "turnover": 0.4, "relative_strength": 0.3}))
        self.assertFalse(should_flag({"price_return": 1.9, "volume": 1.8, "turnover": 1.7, "relative_strength": 1.6}))

    def test_score_is_bounded_and_rises_with_multi_factor_deviation(self):
        single = activity_score({"price_return": 3.0, "volume": 0.5, "turnover": 0.5, "relative_strength": 0.5})
        multi = activity_score({"price_return": 3.0, "volume": 2.5, "turnover": 2.2, "relative_strength": 2.0})
        self.assertGreater(multi, single)
        self.assertLessEqual(multi, 100.0)

    def test_direction_uses_the_strongest_directional_deviation(self):
        self.assertEqual(activity_direction({"price_return": 3.5, "relative_strength": 2.0, "volume": 8.0}), "POSITIVE")
        self.assertEqual(activity_direction({"price_return": 1.5, "relative_strength": -4.0, "turnover": 9.0}), "NEGATIVE")

    def test_four_factor_monitor_flags_spike_and_keeps_neutral_reason(self):
        dates = pd.date_range("2026-05-01", periods=50, freq="B")
        benchmark_close = pd.Series([100.0 * (1.0005 ** index) for index in range(50)], index=dates)
        benchmark = benchmark_close

        quiet_close = pd.Series([50.0 * (1.0007 ** index) for index in range(50)], index=dates)
        quiet_volume = pd.Series([1_000_000 + (index % 5) * 20_000 for index in range(50)], index=dates)
        spike_close = quiet_close.copy()
        spike_volume = quiet_volume.copy()
        spike_close.iloc[-1] = spike_close.iloc[-2] * 1.12
        spike_volume.iloc[-1] = quiet_volume.iloc[-2] * 8

        prices = {
            "SPIKE": pd.DataFrame({"Close": spike_close, "Volume": spike_volume}),
            "QUIET": pd.DataFrame({"Close": quiet_close, "Volume": quiet_volume}),
        }
        scored = [
            {"symbol": "SPIKE", "name": "Spike", "sector": "Test"},
            {"symbol": "QUIET", "name": "Quiet", "sector": "Test"},
        ]

        rows = build_unexplained_activity(scored, prices, benchmark)

        self.assertEqual([row["symbol"] for row in rows], ["SPIKE"])
        row = rows[0]
        self.assertEqual(set(row["factors"]), {"price_return", "volume", "turnover", "relative_strength"})
        self.assertGreaterEqual(row["activity_score"], 70)
        self.assertEqual(row["direction"], "POSITIVE")
        self.assertNotIn("insider", row["reason"].lower())
        self.assertNotIn("leak", row["reason"].lower())
        self.assertNotIn("announcement", row["reason"].lower())
        self.assertEqual(row["baseline_sessions"], 20)
        self.assertAlmostEqual(row["close"], float(spike_close.iloc[-1]), places=4)

    def test_methodology_keeps_explicit_step14_exclusions(self):
        exclusions = " ".join(ACTIVITY_METHODOLOGY["exclusions"]).lower()
        self.assertIn("no event-study database", exclusions)
        self.assertIn("no pre/post-announcement", exclusions)
        self.assertIn("no insider-trading detection", exclusions)
        self.assertIn("no leaked-information identification", exclusions)
        self.assertIn("not a directional trade signal", ACTIVITY_METHODOLOGY["interpretation"].lower())


if __name__ == "__main__":
    unittest.main()
