import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant"))

from bmk_quant.engine import _assign_quant_sextiles, _backtest, _percentile, _portfolio, _rsi  # noqa: E402
from bmk_quant.universe import Security  # noqa: E402


class EngineTests(unittest.TestCase):
    def test_percentile_direction(self):
        values = pd.Series([10.0, 20.0, 30.0])
        self.assertGreater(_percentile(values).iloc[-1], _percentile(values).iloc[0])
        self.assertLess(_percentile(values, False).iloc[-1], _percentile(values, False).iloc[0])

    def test_rsi_is_bounded(self):
        values = pd.Series([100 + index * 0.3 + (index % 3) * 0.1 for index in range(40)])
        self.assertGreaterEqual(_rsi(values), 0)
        self.assertLessEqual(_rsi(values), 100)

    def test_portfolio_respects_neutral_exposure_and_single_name_caps(self):
        rows = [
            {"symbol": f"S{index}", "expected_edge": 8 - index * 0.2, "volatility": 20 + index,
             "action": "ADD", "close": 10.0, "atr": 0.5, "beta": 1.0}
            for index in range(8)
        ]
        with patch.dict("os.environ", {"PORTFOLIO_SYMBOLS": ",".join(row["symbol"] for row in rows)}):
            portfolio, summary = _portfolio(rows, "NEUTRAL")
        self.assertAlmostEqual(sum(row["target_weight"] for row in portfolio), 70.0, places=4)
        self.assertLessEqual(max(row["target_weight"] for row in portfolio), 15.0)
        self.assertEqual(summary["exposure_cap"], 70.0)
        self.assertAlmostEqual(summary["cash_reserve"], 30.0, places=4)

    def test_exit_position_gets_zero_target(self):
        rows = [{"symbol": "EXITME", "expected_edge": 8.0, "volatility": 20.0,
                 "action": "EXIT", "close": 1.0, "atr": 0.1, "beta": 1.0}]
        with patch.dict("os.environ", {"PORTFOLIO_SYMBOLS": "EXITME"}):
            portfolio, summary = _portfolio(rows, "RISK-ON")
        self.assertEqual(portfolio[0]["target_weight"], 0.0)
        self.assertEqual(summary["capital_deployed"], 0.0)

    def test_backtest_drawdown_uses_period_cohorts(self):
        dates = pd.date_range("2025-01-01", periods=320, freq="B")
        prices = {}
        metadata = {}
        for index in range(6):
            symbol = f"S{index}"
            close = pd.Series([100 * (0.995 ** day) * (1 + index * 0.01) for day in range(320)], index=dates)
            prices[symbol] = pd.DataFrame({
                "Close": close,
                "High": close * 1.01,
                "Low": close * 0.99,
                "Volume": 1_000_000 + index * 100_000,
            })
            metadata[symbol] = Security(symbol, symbol, "Test", 1_000_000 + index)
        benchmark = pd.Series([100 * (0.999 ** day) for day in range(320)], index=dates)

        result = _backtest(metadata, prices, benchmark)

        self.assertEqual(result["total_trades"], 24)
        self.assertLess(result["max_drawdown"], 0)
        self.assertGreater(result["max_drawdown"], -100)
        self.assertEqual([group["name"] for group in result["groups"]], ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"])
        self.assertEqual([cohort["lag_sessions"] for cohort in result["cohorts"]], [80, 60, 40, 20])
        self.assertEqual(sum(cohort["observations"] for cohort in result["cohorts"]), result["total_trades"])
        self.assertEqual(result["methodology"]["forward_horizon_sessions"], 20)
        self.assertEqual(result["methodology"]["grouping"], "cross_sectional_quant_score_sextiles_by_period")

    def test_sextiles_are_balanced_and_score_ordered_in_each_period(self):
        labels = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
        frame = pd.DataFrame([
            {"period": period, "quant_score": float(score), "forward": 0.01}
            for period, size in ((80, 13), (60, 14), (40, 17), (20, 19))
            for score in range(size)
        ])

        assigned = _assign_quant_sextiles(frame, labels)

        counts = assigned.groupby("group").size().reindex(labels)
        self.assertLessEqual(int(counts.max() - counts.min()), 4)
        for _, cohort in assigned.groupby("period"):
            cohort_counts = cohort.groupby("group").size().reindex(labels, fill_value=0)
            self.assertLessEqual(int(cohort_counts.max() - cohort_counts.min()), 1)
            maxima = cohort.groupby("group").quant_score.max().reindex(labels)
            minima = cohort.groupby("group").quant_score.min().reindex(labels)
            self.assertTrue(all(maxima.iloc[index] <= minima.iloc[index + 1] for index in range(5)))


if __name__ == "__main__":
    unittest.main()
