import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant"))

from bmk_quant.engine import _percentile, _rsi  # noqa: E402


class EngineTests(unittest.TestCase):
    def test_percentile_direction(self):
        values = pd.Series([10.0, 20.0, 30.0])
        self.assertGreater(_percentile(values).iloc[-1], _percentile(values).iloc[0])
        self.assertLess(_percentile(values, False).iloc[-1], _percentile(values, False).iloc[0])

    def test_rsi_is_bounded(self):
        values = pd.Series([100 + index * 0.3 + (index % 3) * 0.1 for index in range(40)])
        self.assertGreaterEqual(_rsi(values), 0)
        self.assertLessEqual(_rsi(values), 100)


if __name__ == "__main__":
    unittest.main()
