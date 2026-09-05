import json
import os
import sys
import time
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant"))
from bmk_quant.astra import Config, build, features, prepare, simulate
from bmk_quant.astra_data import completed_cutoff, discover, normalize_daily
from bmk_quant.universe import Security


def history(count=270, start=1):
    close = start * np.exp(np.arange(count) * .004)
    return pd.DataFrame({"Open": close, "High": close * 1.005, "Low": close * .995,
                         "Close": close, "Volume": 10000000}, index=pd.bdate_range("2023-01-02", periods=count))


class AstraTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(fee_bps=0, minimum_fee=0, slippage_bps=0,
                             max_position_pct=100, max_sector_pct=100, min_turnover=0)
        self.dates = pd.bdate_range("2025-01-01", periods=4)
        self.frame = pd.DataFrame({"Open": [10, 10, 8, 8], "High": [10.1, 10.1, 8.1, 8.1],
            "Low": [9.9, 9.9, 7.9, 7.9], "Close": [10., 10., 8., 8.], "Volume": [1000000] * 4}, index=self.dates)
        self.meta = {"A": Security("A", "Alpha", "Technology", 0)}
        self.signals = {self.dates[0]: [{"symbol": "A", "date": self.dates[0], "atr": .2, "turnover": 1e8}]}

    def run_sim(self, frame=None, config=None, signals=None):
        return simulate({"A": frame if frame is not None else self.frame}, self.meta,
            signals if signals is not None else self.signals, self.dates, config or self.config)

    def test_signal_enters_next_session_and_gap_exceeds_planned_risk(self):
        result = self.run_sim()
        trade = result["trades"][0]
        self.assertEqual(trade["entry_date"], self.dates[1].date().isoformat())
        self.assertEqual(trade["exit"], 8)
        self.assertLess(trade["r"], -1)
        self.assertEqual(trade["shares"] % 100, 0)
        self.assertAlmostEqual(result["metrics"]["final_equity"], self.config.capital + trade["pnl"])

    def test_last_day_signal_cannot_fill_in_past(self):
        result = self.run_sim(signals={self.dates[-1]: self.signals[self.dates[0]]})
        self.assertEqual(result["metrics"]["open_positions"], 0)
        self.assertEqual(result["metrics"]["closed_trades"], 0)
        self.assertIsNone(result["metrics"]["win_rate"])

    def test_zero_volume_defers_stop(self):
        frame = self.frame.copy(); frame.loc[self.dates[2], "Volume"] = 0
        result = self.run_sim(frame)
        self.assertEqual(result["trades"][0]["exit_date"], self.dates[3].date().isoformat())

    def test_zero_range_defers_stop(self):
        frame = self.frame.copy(); frame.loc[self.dates[2], ["Open", "High", "Low", "Close"]] = 8
        self.assertEqual(self.run_sim(frame)["trades"][0]["exit_date"], self.dates[3].date().isoformat())

    def test_partial_exit_accounting(self):
        frame = self.frame.copy(); frame.loc[self.dates[2], "Volume"] = 10000
        result = self.run_sim(frame)
        self.assertEqual(result["metrics"]["closed_trades"], 1)
        self.assertAlmostEqual(result["metrics"]["final_equity"], self.config.capital + result["trades"][0]["pnl"])
        self.assertEqual(result["trades"][0]["reason"], "delayed_stop")

    def test_costs_reduce_equity_and_are_in_risk_budget(self):
        result = self.run_sim(config=replace(self.config, fee_bps=20, minimum_fee=8))
        trade = result["trades"][0]
        self.assertGreater(trade["fees"], 0)
        self.assertLessEqual(trade["shares"] * (trade["entry"] - trade["initial_stop"]) + trade["fees"], 500)
        self.assertAlmostEqual(result["metrics"]["final_equity"], self.config.capital + trade["pnl"])

    def test_position_cap(self):
        result = self.run_sim(config=replace(self.config, max_position_pct=2))
        trade = result["trades"][0]
        self.assertLessEqual(trade["shares"] * trade["entry"], 2000)

    def test_today_high_cannot_raise_today_stop(self):
        frame = self.frame.copy()
        frame.loc[self.dates[1], ["High", "Low", "Close"]] = [13, 9.9, 12]
        frame.loc[self.dates[2], ["Open", "High", "Low", "Close"]] = [12, 12.1, 11, 11.5]
        result = self.run_sim(frame)
        self.assertNotEqual(result["trades"][0]["exit_date"], self.dates[1].date().isoformat())

    def test_feature_prefix_invariance_and_breakout_excludes_today(self):
        frame = history()
        prior = features(frame.iloc[:250])
        frame.loc[frame.index[250:], "Close"] *= 3
        pd.testing.assert_frame_equal(prior, features(frame).iloc[:250])
        self.assertAlmostEqual(prior.iloc[-1].breakout_level, frame.High.iloc[194:249].max())

    def test_cross_sectional_rank_recomputed_by_date(self):
        a, b = history(), history()
        b[["Open", "High", "Low", "Close"]] *= np.exp(np.arange(len(b)) * .001)[:, None]
        table, _ = prepare({"A": a, "B": b}, self.config)
        today = table[table.date == a.index[-1]].set_index("symbol")
        self.assertEqual(today.loc["B", "rs_percentile"], 100)
        self.assertEqual(today.loc["A", "rs_percentile"], 50)

    def test_complete_report_is_json_serializable_and_labels_limits(self):
        frame = history()
        result = build({"A": frame}, self.meta, frame.Close, self.config)
        json.dumps(result, allow_nan=False)
        self.assertEqual(result["history"]["test_sessions"], 50)
        self.assertEqual(set(result["strategies"]), {"breakout", "pullback"})
        self.assertTrue(any("survivorship" in line for line in result["limitations"]))

    def test_intraday_cutoff_and_weekend(self):
        tz = ZoneInfo("Asia/Kuala_Lumpur")
        self.assertEqual(str(completed_cutoff(datetime(2026, 9, 7, 15, tzinfo=tz)).date()), "2026-09-04")
        self.assertEqual(str(completed_cutoff(datetime(2026, 9, 7, 18, tzinfo=tz)).date()), "2026-09-07")

    def test_discovery_paginates_beyond_one_thousand(self):
        class Session:
            def __init__(self): self.calls = 0
            def post(self, url, json, timeout):
                self.calls += 1
                offset = json["range"][0]
                items = [{"s": f"MYX:T{i}", "d": [f"T{i}", f"Company {i}", "Industry", 0, "stock", "", ["common"]]}
                         for i in range(offset, min(offset + 500, 1129))]
                class Response:
                    def raise_for_status(self): pass
                    def json(self): return {"totalCount": 1129, "data": items}
                return Response()
        session = Session()
        universe = discover(session)
        self.assertEqual(len(universe), 1129)
        self.assertEqual(session.calls, 3)

    def test_daily_bar_date_is_independent_of_runner_timezone(self):
        original = os.environ.get("TZ")
        try:
            for zone, stamp in [("America/Los_Angeles", "2026-09-03 18:00:00"), ("UTC", "2026-09-04 01:00:00")]:
                os.environ["TZ"] = zone; time.tzset()
                raw = pd.DataFrame({"open": [1.], "high": [1.1], "low": [.9], "close": [1.], "volume": [100]},
                                   index=pd.DatetimeIndex([stamp]))
                self.assertEqual(str(normalize_daily(raw, "A").index[0].date()), "2026-09-04")
        finally:
            if original is None: os.environ.pop("TZ", None)
            else: os.environ["TZ"] = original
            time.tzset()

    def test_preferred_share_does_not_collapse_into_ordinary_code(self):
        class Session:
            def post(self, *args, **kwargs):
                class Response:
                    def raise_for_status(self): pass
                    def json(self): return {"totalCount": 2, "data": [
                        {"d": ["SUNMOW", "Sunmow", "Industry", 0, "stock", "MYL03050O001", ["common"]]},
                        {"d": ["SUNMOW-PA", "Sunmow Preferred", "Industry", 0, "stock", "MYL030501695", ["preferred"]]},
                    ]}
                return Response()
        universe, excluded, total = discover(Session(), details=True)
        self.assertEqual((len(universe), len(excluded), total), (1, 1, 2))
        self.assertEqual(universe[0].symbol, "03050")
        self.assertEqual(excluded[0]["symbol"], "SUNMOW-PA")


if __name__ == "__main__": unittest.main()
