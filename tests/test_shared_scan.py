import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant"))
from bmk_quant import engine
from bmk_quant.astra import Config, build, evidence
from bmk_quant.astra_data import fetch_frame
from bmk_quant.pipeline import build_quant_payload
from bmk_quant.universe import Security

spec = importlib.util.spec_from_file_location("run_shared", ROOT / "scripts/run_shared.py")
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


def fixture(count=300):
    close = np.linspace(1, 2, count)
    index = pd.bdate_range("2025-01-01", periods=count)
    frame = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * .99, "Close": close, "Volume": 2e6}, index=index)
    metadata = {"0001": Security("0001", "Example", "Industry", 1e9, "EXAMPLE")}
    return {"0001": frame}, metadata, frame.Close, {"data_hash": "example", "processed": 1, "discovered": 1}


class SharedScanTests(unittest.TestCase):
    def test_quant_injected_snapshot_never_downloads(self):
        data = fixture()
        with patch.object(engine, "MIN_UNIVERSE", 1), patch.object(engine, "get_universe", side_effect=AssertionError("second discovery")), \
             patch.object(engine, "_benchmark", side_effect=AssertionError("second benchmark")), \
             patch.object(engine, "_download_prices", side_effect=AssertionError("second download")):
            payload, _ = build_quant_payload(market_data=data)
        self.assertEqual(payload["fresh_symbols"], 1)
        self.assertEqual(payload["methodology"]["market_snapshot_hash"], "example")

    def test_shared_runner_collects_once_and_passes_same_frames_to_both(self):
        data = fixture()
        def quant_write(path, **kwargs):
            path.mkdir(parents=True, exist_ok=True)
            (path / "latest.json").write_text('{}')
            self.assertIs(kwargs["market_data"], data)
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "collect", return_value=data) as collector, \
             patch.object(runner, "write_artifacts", side_effect=quant_write), \
             patch.object(runner, "build", return_value={"strategies": {"breakout": {"trades": []}}}) as astra, \
             patch.object(runner.subprocess, "run"), patch.object(runner, "print"):
            report = Mock()
            runner.execute(directory, Config(), report)
            collector.assert_called_once()
            self.assertEqual(collector.call_args.args[0], 300)
            self.assertIs(astra.call_args.args[0], data[0])
            self.assertEqual(report.call_args.args[0], "complete")
            self.assertEqual(len(pd.read_csv(Path(directory) / "source/0001.csv")), 300)

    def test_partial_failure_does_not_claim_both_completed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "collect", return_value=fixture()), \
             patch.object(runner, "write_artifacts", side_effect=RuntimeError("Quant failed")), \
             patch.object(runner, "build", return_value={"strategies": {}}), patch.object(runner, "print"):
            report = Mock()
            with self.assertRaisesRegex(RuntimeError, "Quant"):
                runner.execute(directory, Config(), report)
            self.assertNotIn("complete", [call.args[0] for call in report.call_args_list])

    def test_backtest_caps_imported_history_at_three_hundred(self):
        prices, metadata, benchmark, _ = fixture(600)
        result = build(prices, metadata, benchmark)
        self.assertEqual(result["history"]["benchmark_bars"], 300)
        self.assertEqual(result["history"]["stock_bars_max"], 300)
        self.assertEqual(result["history"]["test_sessions"], 80)

    def test_provider_request_and_response_are_capped(self):
        prices, _, _, _ = fixture(600)
        client = Mock(); client.get_hist.return_value = prices["0001"]
        interval = Mock()
        with patch("bmk_quant.astra_data.engine._tv_client", return_value=(client, interval)), \
             patch("bmk_quant.astra_data.engine._pace_tradingview_connection"), \
             patch("bmk_quant.astra_data.normalize_daily", return_value=prices["0001"]):
            frame, error = fetch_frame("A", "MYX", 1500, pd.Timestamp("2030-01-01"))
        self.assertIsNone(error)
        self.assertEqual(client.get_hist.call_args.kwargs["n_bars"], 300)
        self.assertEqual(len(frame), 300)

    def test_weak_and_short_results_never_become_a_success_claim(self):
        metrics = {"closed_trades": 10, "expectancy_r": -.2, "return_pct": -1}
        verdict = evidence(metrics, metrics, metrics, 80, 5)
        self.assertEqual(verdict["status"], "WEAK RESULTS")
        self.assertEqual(verdict["readiness"], "NOT VALIDATED")
        self.assertTrue(verdict["reasons"])


if __name__ == "__main__": unittest.main()
