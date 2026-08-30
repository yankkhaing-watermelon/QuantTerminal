import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant"))

from bmk_quant.engine import _percentile, _portfolio, _rsi  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
