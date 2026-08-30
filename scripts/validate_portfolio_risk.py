#!/usr/bin/env python3
"""Validate the financial consistency of Quant Terminal portfolio sizing."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

REGIME_CAPS = {
    "STRONG RISK-ON": 100.0,
    "RISK-ON": 85.0,
    "NEUTRAL": 70.0,
    "RISK-OFF": 45.0,
    "STRONG RISK-OFF": 25.0,
}
MAX_POSITION_WEIGHT = 15.0


def finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def close_enough(left, right, tolerance: float = 1e-4) -> bool:
    return finite(left) and finite(right) and abs(float(left) - float(right)) <= tolerance


def validate_portfolio(payload: dict, require_portfolio: bool = False) -> dict:
    issues: list[str] = []
    warnings: list[str] = []
    stocks = {str(row.get("symbol") or ""): row for row in payload.get("stocks") or []}
    portfolio = payload.get("portfolio") or []
    summary = payload.get("portfolio_summary") or {}
    regime = str((payload.get("regime") or {}).get("state") or "")
    exposure_cap = REGIME_CAPS.get(regime)

    if not portfolio:
        message = "portfolio_empty_set_GitHub_variable_PORTFOLIO_SYMBOLS"
        (issues if require_portfolio else warnings).append(message)
        return {
            "ok": not issues, "status": "not_configured", "run_id": payload.get("run_id"),
            "regime": regime, "portfolio_rows": 0, "issues": issues, "warnings": warnings,
        }
    if exposure_cap is None:
        issues.append(f"unsupported_regime:{regime}")
        exposure_cap = 0

    weights: list[float] = []
    risk_contributions: list[float] = []
    weighted_betas: list[float] = []
    seen: set[str] = set()
    for row in portfolio:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            issues.append(f"invalid_or_duplicate_portfolio_symbol:{symbol or 'missing'}")
            continue
        seen.add(symbol)
        stock = stocks.get(symbol)
        if stock is None:
            issues.append(f"portfolio_symbol_missing_from_full_rankings:{symbol}")
            continue

        for field in ("target_weight", "position_size", "risk_contribution", "stop_price", "beta"):
            if not finite(row.get(field)):
                issues.append(f"non_finite_{field}:{symbol}")
        if any(not finite(row.get(field)) for field in ("target_weight", "position_size", "risk_contribution", "stop_price", "beta")):
            continue

        weight = float(row["target_weight"])
        position_size = float(row["position_size"])
        risk_contribution = float(row["risk_contribution"])
        if weight < -1e-6 or weight > MAX_POSITION_WEIGHT + 1e-4:
            issues.append(f"single_name_weight_out_of_bounds:{symbol}:{weight}")
        if not close_enough(weight, position_size):
            issues.append(f"position_target_mismatch:{symbol}:{position_size}!={weight}")
        if row.get("action") == "EXIT" and weight > 1e-4:
            issues.append(f"exit_position_has_nonzero_target:{symbol}:{weight}")

        volatility = stock.get("volatility")
        if not finite(volatility):
            issues.append(f"missing_stock_volatility:{symbol}")
        else:
            expected_risk = weight * float(volatility) / 100
            if not close_enough(risk_contribution, expected_risk, 2e-5):
                issues.append(f"risk_contribution_formula_mismatch:{symbol}:{risk_contribution}!={expected_risk:.6f}")

        if finite(stock.get("beta")) and not close_enough(row["beta"], stock["beta"], 1e-6):
            issues.append(f"beta_mismatch:{symbol}:{row['beta']}!={stock['beta']}")
        close = stock.get("close")
        atr = stock.get("atr")
        if finite(close) and finite(atr):
            expected_stop = max(0.001, float(close) - 3 * float(atr))
            if float(row["stop_price"]) <= 0 or float(row["stop_price"]) >= float(close):
                issues.append(f"invalid_stop_geometry:{symbol}:{row['stop_price']}:{close}")
            elif not close_enough(row["stop_price"], expected_stop, 1e-4):
                issues.append(f"three_atr_stop_mismatch:{symbol}:{row['stop_price']}!={expected_stop:.4f}")

        weights.append(weight)
        risk_contributions.append(risk_contribution)
        weighted_betas.append(weight * float(row["beta"]))

    deployed = sum(weights)
    if deployed > exposure_cap + 1e-4:
        issues.append(f"regime_exposure_cap_breached:{deployed:.6f}>{exposure_cap:.6f}")
    if deployed > 100 + 1e-4:
        issues.append(f"portfolio_exposure_above_100:{deployed:.6f}")
    if not close_enough(summary.get("capital_deployed"), deployed, 2e-5):
        issues.append(f"capital_deployed_mismatch:{summary.get('capital_deployed')}!={deployed:.6f}")
    if not close_enough(summary.get("cash_reserve"), 100 - deployed, 2e-5):
        issues.append(f"cash_reserve_mismatch:{summary.get('cash_reserve')}!={100 - deployed:.6f}")
    if not close_enough(summary.get("exposure_cap"), exposure_cap):
        issues.append(f"exposure_cap_mismatch:{summary.get('exposure_cap')}!={exposure_cap}")
    if summary.get("regime") != regime:
        issues.append(f"summary_regime_mismatch:{summary.get('regime')}!={regime}")
    if not close_enough(summary.get("risk_used"), sum(risk_contributions), 2e-5):
        issues.append(f"risk_used_mismatch:{summary.get('risk_used')}!={sum(risk_contributions):.6f}")
    if not close_enough(summary.get("max_single_weight"), max(weights, default=0), 2e-5):
        issues.append("max_single_weight_mismatch")

    active_weights = [weight for weight in weights if weight > 1e-9]
    if deployed > 0 and active_weights:
        expected_beta = sum(weighted_betas) / deployed
        normalized = [weight / deployed for weight in active_weights]
        effective_positions = 1 / sum(weight * weight for weight in normalized)
    else:
        expected_beta, effective_positions = 0.0, 0.0
    expected_diversification = min(100, effective_positions * 12.5)
    if not close_enough(summary.get("beta"), expected_beta, 2e-5):
        issues.append(f"portfolio_beta_mismatch:{summary.get('beta')}!={expected_beta:.6f}")
    if not close_enough(summary.get("effective_positions"), effective_positions, 2e-5):
        issues.append("effective_positions_mismatch")
    if not close_enough(summary.get("diversification_score"), expected_diversification, 2e-5):
        issues.append("diversification_score_mismatch")
    if deployed > 0 and max(active_weights, default=0) / deployed > 0.35:
        warnings.append("invested_capital_concentrated_above_35pct_in_one_name")

    return {
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "run_id": payload.get("run_id"),
        "scan_date": payload.get("scan_date"),
        "regime": regime,
        "exposure_cap_pct": exposure_cap,
        "capital_deployed_pct": round(deployed, 6),
        "cash_reserve_pct": round(100 - deployed, 6),
        "portfolio_beta": summary.get("beta"),
        "risk_used": summary.get("risk_used"),
        "portfolio_rows": len(portfolio),
        "active_positions": len(active_weights),
        "effective_positions": summary.get("effective_positions"),
        "diversification_score": summary.get("diversification_score"),
        "issues": issues,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default="artifacts/latest")
    parser.add_argument("--require-portfolio", action="store_true")
    args = parser.parse_args()
    root = Path(args.artifacts)
    payload_path = root / "latest.json"
    if not payload_path.exists():
        raise SystemExit(f"missing_payload:{payload_path}")
    report = validate_portfolio(json.loads(payload_path.read_text(encoding="utf-8")), args.require_portfolio)
    (root / "portfolio-risk-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
