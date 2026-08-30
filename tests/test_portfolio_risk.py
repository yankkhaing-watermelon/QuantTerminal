import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_portfolio_risk import validate_portfolio  # noqa: E402


def valid_payload():
    return {
        "run_id": "test-run", "scan_date": "2026-08-28",
        "regime": {"state": "NEUTRAL"},
        "stocks": [
            {"symbol": "AAA", "close": 10.0, "atr": 0.5, "volatility": 20.0, "beta": 1.1},
            {"symbol": "BBB", "close": 5.0, "atr": 0.2, "volatility": 30.0, "beta": 0.8},
        ],
        "portfolio": [
            {"symbol": "AAA", "action": "ADD", "target_weight": 12.0, "position_size": 12.0, "risk_contribution": 2.4, "stop_price": 8.5, "beta": 1.1},
            {"symbol": "BBB", "action": "HOLD", "target_weight": 8.0, "position_size": 8.0, "risk_contribution": 2.4, "stop_price": 4.4, "beta": 0.8},
        ],
        "portfolio_summary": {
            "regime": "NEUTRAL", "exposure_cap": 70.0, "capital_deployed": 20.0,
            "cash_reserve": 80.0, "beta": 0.98, "risk_used": 4.8,
            "max_single_weight": 12.0, "effective_positions": 1.923077,
            "diversification_score": 24.038462,
        },
    }


class PortfolioRiskValidationTests(unittest.TestCase):
    def test_valid_portfolio_passes(self):
        report = validate_portfolio(valid_payload(), require_portfolio=True)
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(report["status"], "passed")

    def test_regime_exposure_breach_fails(self):
        payload = valid_payload()
        payload["regime"]["state"] = "STRONG RISK-OFF"
        payload["portfolio"][0]["target_weight"] = 15.0
        payload["portfolio"][0]["position_size"] = 15.0
        payload["portfolio"][0]["risk_contribution"] = 3.0
        payload["portfolio"][1]["target_weight"] = 15.0
        payload["portfolio"][1]["position_size"] = 15.0
        payload["portfolio"][1]["risk_contribution"] = 4.5
        payload["portfolio_summary"].update({"regime": "STRONG RISK-OFF", "exposure_cap": 25.0, "capital_deployed": 30.0, "cash_reserve": 70.0, "risk_used": 7.5, "max_single_weight": 15.0, "beta": 0.95, "effective_positions": 2.0, "diversification_score": 25.0})
        report = validate_portfolio(payload, require_portfolio=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any(issue.startswith("regime_exposure_cap_breached") for issue in report["issues"]))

    def test_risk_formula_mismatch_fails(self):
        payload = valid_payload()
        payload["portfolio"][0]["risk_contribution"] = 9.9
        report = validate_portfolio(payload, require_portfolio=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any(issue.startswith("risk_contribution_formula_mismatch:AAA") for issue in report["issues"]))

    def test_empty_portfolio_requires_configuration(self):
        payload = valid_payload()
        payload["portfolio"] = []
        payload["portfolio_summary"] = {}
        self.assertFalse(validate_portfolio(payload, require_portfolio=True)["ok"])
        optional = validate_portfolio(payload, require_portfolio=False)
        self.assertTrue(optional["ok"])
        self.assertEqual(optional["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
