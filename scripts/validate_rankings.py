#!/usr/bin/env python3
"""Fail-closed validation for the published quant ranking/portfolio payload.

This is a contract/integrity check, not a performance claim. It verifies that the
full-universe output is internally consistent before publication.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

ALLOWED_REGIMES = {"STRONG RISK-ON", "RISK-ON", "NEUTRAL", "RISK-OFF", "STRONG RISK-OFF"}
ALLOWED_ACTIONS = ("ADD", "HOLD", "WATCH", "TRIM", "REDUCE", "EXIT")


def finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def check_range(issues: list[str], label: str, value, low: float, high: float) -> None:
    if not finite(value) or not (low <= float(value) <= high):
        issues.append(f"{label}_out_of_range:{value}")


def expected_action(score: float) -> str:
    if score >= 80:
        return "ADD"
    if score >= 67:
        return "HOLD"
    if score >= 52:
        return "WATCH"
    if score >= 40:
        return "TRIM"
    if score >= 28:
        return "REDUCE"
    return "EXIT"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default="artifacts/latest")
    args = parser.parse_args()

    root = Path(args.artifacts)
    payload_path = root / "latest.json"
    if not payload_path.exists():
        raise SystemExit(f"missing_payload:{payload_path}")

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    warnings: list[str] = []

    universe_size = int(payload.get("universe_size") or 0)
    fresh_symbols = int(payload.get("fresh_symbols") or 0)
    stocks = payload.get("stocks") or []
    portfolio = payload.get("portfolio") or []
    regime = payload.get("regime") or {}
    breadth = payload.get("breadth") or {}

    if universe_size <= 0:
        issues.append("universe_size_missing_or_zero")
    if fresh_symbols <= 0:
        issues.append("fresh_symbols_missing_or_zero")
    if fresh_symbols > universe_size:
        issues.append(f"fresh_symbols_exceeds_universe:{fresh_symbols}>{universe_size}")

    # The engine already fails closed below 900 for production-sized runs. Keep
    # the same contract here so publication cannot regress to a partial universe.
    if universe_size >= 900 and fresh_symbols < 900:
        issues.append(f"production_fresh_coverage_below_900:{fresh_symbols}")
    elif universe_size < 900:
        required = max(1, math.ceil(universe_size * 0.70))
        if fresh_symbols < required:
            issues.append(f"development_fresh_coverage_below_70pct:{fresh_symbols}<{required}")

    if not stocks:
        issues.append("stocks_empty")
    if regime.get("state") not in ALLOWED_REGIMES:
        issues.append(f"invalid_regime_state:{regime.get('state')}")
    check_range(issues, "regime_score", regime.get("score"), 0, 100)
    check_range(issues, "regime_confidence", regime.get("confidence"), 0, 100)

    for key in ("above_20dma", "above_50dma", "above_200dma", "volume_breadth", "sector_breadth", "participation"):
        if key in breadth:
            check_range(issues, f"breadth_{key}", breadth[key], 0, 100)

    symbols: set[str] = set()
    ranks: list[int] = []
    previous_key: tuple[float, float] | None = None
    for index, row in enumerate(stocks, start=1):
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            issues.append(f"stock_{index}_missing_symbol")
            continue
        if symbol in symbols:
            issues.append(f"duplicate_stock_symbol:{symbol}")
        symbols.add(symbol)

        rank = row.get("rank")
        if not isinstance(rank, int):
            try:
                rank = int(rank)
            except (TypeError, ValueError):
                issues.append(f"invalid_rank:{symbol}:{rank}")
                rank = None
        if rank is not None:
            ranks.append(rank)
            if rank != index:
                issues.append(f"rank_sequence_mismatch:{symbol}:{rank}!={index}")

        score = row.get("quant_score")
        if not finite(score):
            issues.append(f"invalid_quant_score:{symbol}")
            continue
        score = float(score)
        check_range(issues, f"quant_score_{symbol}", score, 0, 100)
        check_range(issues, f"expected_edge_{symbol}", row.get("expected_edge"), -12, 18)
        check_range(issues, f"confidence_{symbol}", row.get("confidence"), 50, 95)
        check_range(issues, f"risk_score_{symbol}", row.get("risk_score"), 0, 100)

        action = row.get("action")
        if action not in ALLOWED_ACTIONS:
            issues.append(f"invalid_action:{symbol}:{action}")
        elif action != expected_action(score):
            issues.append(f"action_score_mismatch:{symbol}:{score}:{action}!={expected_action(score)}")

        turnover = row.get("turnover")
        if not finite(turnover) or float(turnover) < 0:
            issues.append(f"invalid_turnover:{symbol}")
        else:
            key = (score, float(turnover))
            if previous_key is not None and key > previous_key:
                issues.append(f"ranking_order_violation:{symbol}")
            previous_key = key

    if ranks and ranks != list(range(1, len(stocks) + 1)):
        issues.append("rank_values_not_contiguous")

    portfolio_symbols: set[str] = set()
    for row in portfolio:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            issues.append("portfolio_row_missing_symbol")
            continue
        if symbol in portfolio_symbols:
            issues.append(f"duplicate_portfolio_symbol:{symbol}")
        portfolio_symbols.add(symbol)
        if symbol not in symbols:
            issues.append(f"portfolio_symbol_not_in_rankings:{symbol}")
        for field in ("target_weight", "position_size", "risk_contribution"):
            if field in row and row[field] is not None:
                if not finite(row[field]) or float(row[field]) < 0:
                    issues.append(f"invalid_portfolio_{field}:{symbol}")
        if "stop_price" in row and row["stop_price"] is not None:
            if not finite(row["stop_price"]) or float(row["stop_price"]) <= 0:
                issues.append(f"invalid_stop_price:{symbol}")

    if not portfolio:
        warnings.append("portfolio_empty")

    report = {
        "ok": not issues,
        "run_id": payload.get("run_id"),
        "scan_date": payload.get("scan_date"),
        "universe_size": universe_size,
        "fresh_symbols": fresh_symbols,
        "fresh_coverage_pct": round(fresh_symbols / universe_size * 100, 2) if universe_size else 0,
        "ranked_stocks": len(stocks),
        "portfolio_rows": len(portfolio),
        "regime": regime.get("state"),
        "regime_score": regime.get("score"),
        "regime_confidence": regime.get("confidence"),
        "issues": issues,
        "warnings": warnings,
        "top_10": [
            {
                "rank": row.get("rank"),
                "symbol": row.get("symbol"),
                "quant_score": row.get("quant_score"),
                "expected_edge": row.get("expected_edge"),
                "confidence": row.get("confidence"),
                "action": row.get("action"),
            }
            for row in stocks[:10]
        ],
    }

    (root / "ranking-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
