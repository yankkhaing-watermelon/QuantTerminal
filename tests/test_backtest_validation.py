import unittest

from scripts.validate_backtest import validate_backtest


def valid_payload() -> dict:
    returns = [1.0, 2.0, -1.0, 3.0]
    lags = [80, 60, 40, 20]
    equity = 1.0
    peak = 1.0
    cohorts = []
    for lag, return_pct in zip(lags, returns, strict=True):
        equity *= 1 + return_pct / 100
        peak = max(peak, equity)
        cohorts.append({
            "lag_sessions": lag,
            "observations": 6,
            "wins": 3,
            "return_pct": return_pct,
            "equity": round(equity, 8),
            "drawdown_pct": round((equity / peak - 1) * 100, 6),
        })
    return {
        "run_id": "test-run",
        "scan_date": "2026-08-28",
        "backtest": {
            "total_trades": 24,
            "win_rate": 50.0,
            "expectancy": 1.25,
            "max_drawdown": -1.0,
            "groups": [
                {"name": f"Q{index}", "trades": 4, "win_rate": 50.0, "expectancy": 1.0, "profit_factor": 1.2}
                for index in range(1, 7)
            ],
            "cohorts": cohorts,
            "methodology": {
                "grouping": "cross_sectional_quant_score_sextiles_by_period",
                "group_order": "Q1 lowest Quant Score; Q6 highest Quant Score",
                "minimum_signal_history_sessions": 200,
                "forward_horizon_sessions": 20,
                "drawdown": "equal_weight_period_cohort",
                "universe_basis": "current_fresh_universe",
                "transaction_costs_bps": 0,
            },
        },
    }


class BacktestValidationTests(unittest.TestCase):
    def test_valid_backtest_passes(self):
        report = validate_backtest(valid_payload())
        self.assertTrue(report["ok"], report["issues"])

    def test_tampered_drawdown_fails(self):
        payload = valid_payload()
        payload["backtest"]["max_drawdown"] = -99.0
        report = validate_backtest(payload)
        self.assertFalse(report["ok"])
        self.assertTrue(any(issue.startswith("max_drawdown_mismatch") for issue in report["issues"]))

    def test_missing_methodology_fails(self):
        payload = valid_payload()
        payload["backtest"]["methodology"] = {}
        report = validate_backtest(payload)
        self.assertFalse(report["ok"])
        self.assertTrue(any(issue.startswith("methodology_mismatch") for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
