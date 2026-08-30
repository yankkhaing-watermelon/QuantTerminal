#!/usr/bin/env python3
"""Fail-closed validation for the Step 12 walk-forward backtest payload."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

GROUPS = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
LAGS = [80, 60, 40, 20]
METHODOLOGY = {
    "grouping": "cross_sectional_quant_score_sextiles_by_period",
    "group_order": "Q1 lowest Quant Score; Q6 highest Quant Score",
    "minimum_signal_history_sessions": 200,
    "forward_horizon_sessions": 20,
    "drawdown": "equal_weight_period_cohort",
    "universe_basis": "current_fresh_universe",
    "transaction_costs_bps": 0,
}


def finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def close_enough(actual, expected, tolerance: float = 1e-4) -> bool:
    return finite(actual) and abs(float(actual) - float(expected)) <= tolerance


def validate_backtest(payload: dict) -> dict:
    issues: list[str] = []
    warnings: list[str] = []
    backtest = payload.get("backtest") or {}
    groups = backtest.get("groups") or []
    cohorts = backtest.get("cohorts") or []
    methodology = backtest.get("methodology") or {}

    for key, expected in METHODOLOGY.items():
        if methodology.get(key) != expected:
            issues.append(f"methodology_mismatch:{key}:{methodology.get(key)}!={expected}")

    total = backtest.get("total_trades")
    if not isinstance(total, int) or total <= 0:
        issues.append(f"invalid_total_observations:{total}")
        total = 0
    for key, low, high in (
        ("win_rate", 0, 100),
        ("expectancy", -100, 1000),
        ("max_drawdown", -99.999999, 0),
    ):
        value = backtest.get(key)
        if not finite(value) or not (low <= float(value) <= high):
            issues.append(f"{key}_out_of_range:{value}")

    names = [row.get("name") for row in groups]
    if names != GROUPS:
        issues.append(f"invalid_group_sequence:{names}")
    group_total = 0
    group_counts = []
    for row in groups:
        name = row.get("name")
        trades = row.get("trades")
        if not isinstance(trades, int) or trades <= 0:
            issues.append(f"invalid_group_observations:{name}:{trades}")
            continue
        group_total += trades
        group_counts.append(trades)
        if not finite(row.get("win_rate")) or not (0 <= float(row["win_rate"]) <= 100):
            issues.append(f"invalid_group_win_rate:{name}:{row.get('win_rate')}")
        if not finite(row.get("expectancy")):
            issues.append(f"invalid_group_expectancy:{name}:{row.get('expectancy')}")
        if not finite(row.get("profit_factor")) or float(row["profit_factor"]) < 0:
            issues.append(f"invalid_group_profit_factor:{name}:{row.get('profit_factor')}")
    if total and group_total != total:
        issues.append(f"group_total_mismatch:{group_total}!={total}")
    if group_counts and max(group_counts) - min(group_counts) > len(cohorts):
        issues.append(f"group_imbalance:{min(group_counts)}..{max(group_counts)}")

    lags = [row.get("lag_sessions") for row in cohorts]
    if lags != LAGS:
        issues.append(f"invalid_cohort_sequence:{lags}")
    cohort_total = 0
    cohort_wins = 0
    weighted_returns = 0.0
    equity = 1.0
    peak = 1.0
    calculated_drawdown = 0.0
    for row in cohorts:
        lag = row.get("lag_sessions")
        observations = row.get("observations")
        wins = row.get("wins")
        return_pct = row.get("return_pct")
        if not isinstance(observations, int) or observations <= 0:
            issues.append(f"invalid_cohort_observations:{lag}:{observations}")
            continue
        if not isinstance(wins, int) or not (0 <= wins <= observations):
            issues.append(f"invalid_cohort_wins:{lag}:{wins}")
            continue
        if not finite(return_pct) or not (-99.9 <= float(return_pct) <= 1000):
            issues.append(f"invalid_cohort_return:{lag}:{return_pct}")
            continue
        cohort_total += observations
        cohort_wins += wins
        weighted_returns += observations * float(return_pct)
        equity *= 1 + float(return_pct) / 100
        peak = max(peak, equity)
        drawdown = (equity / peak - 1) * 100
        calculated_drawdown = min(calculated_drawdown, drawdown)
        if not close_enough(row.get("equity"), equity):
            issues.append(f"cohort_equity_mismatch:{lag}:{row.get('equity')}!={equity:.8f}")
        if not close_enough(row.get("drawdown_pct"), drawdown):
            issues.append(f"cohort_drawdown_mismatch:{lag}:{row.get('drawdown_pct')}!={drawdown:.6f}")

    if total and cohort_total != total:
        issues.append(f"cohort_total_mismatch:{cohort_total}!={total}")
    if cohort_total:
        expected_win_rate = cohort_wins / cohort_total * 100
        expected_expectancy = weighted_returns / cohort_total
        if not close_enough(backtest.get("win_rate"), expected_win_rate):
            issues.append(f"win_rate_mismatch:{backtest.get('win_rate')}!={expected_win_rate:.6f}")
        if not close_enough(backtest.get("expectancy"), expected_expectancy):
            issues.append(f"expectancy_mismatch:{backtest.get('expectancy')}!={expected_expectancy:.6f}")
        if not close_enough(backtest.get("max_drawdown"), calculated_drawdown):
            issues.append(f"max_drawdown_mismatch:{backtest.get('max_drawdown')}!={calculated_drawdown:.6f}")
    else:
        issues.append("cohorts_empty")

    if float(backtest.get("expectancy") or 0) <= 0:
        warnings.append("non_positive_walk_forward_expectancy")
    expectancies = [float(row["expectancy"]) for row in groups if finite(row.get("expectancy"))]
    top_bottom_spread = expectancies[-1] - expectancies[0] if len(expectancies) == len(GROUPS) else None
    monotonic = len(expectancies) == len(GROUPS) and all(
        right >= left for left, right in zip(expectancies, expectancies[1:])
    )
    if top_bottom_spread is not None and top_bottom_spread <= 0:
        warnings.append("highest_quant_sextile_not_outperforming_lowest")
    if expectancies and not monotonic:
        warnings.append("expectancy_not_monotonic_across_score_sextiles")
    if methodology.get("universe_basis") == "current_fresh_universe":
        warnings.append("current_universe_survivorship_bias_possible")
    if methodology.get("transaction_costs_bps") == 0:
        warnings.append("transaction_costs_excluded")

    return {
        "ok": not issues,
        "run_id": payload.get("run_id"),
        "scan_date": payload.get("scan_date"),
        "total_observations": total,
        "cohort_count": len(cohorts),
        "group_count": len(groups),
        "win_rate": backtest.get("win_rate"),
        "expectancy": backtest.get("expectancy"),
        "max_drawdown": backtest.get("max_drawdown"),
        "q6_minus_q1_expectancy_pct": round(top_bottom_spread, 6) if top_bottom_spread is not None else None,
        "expectancy_monotonic": monotonic,
        "issues": issues,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default="artifacts/latest")
    args = parser.parse_args()
    root = Path(args.artifacts)
    payload_path = root / "latest.json"
    if not payload_path.exists():
        raise SystemExit(f"missing_payload:{payload_path}")
    report = validate_backtest(json.loads(payload_path.read_text(encoding="utf-8")))
    (root / "backtest-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
