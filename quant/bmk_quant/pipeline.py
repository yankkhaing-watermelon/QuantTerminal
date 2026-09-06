from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import engine
from .activity import ACTIVITY_METHODOLOGY, build_unexplained_activity
from .wizard import WIZARD_METHODOLOGY, build_wizard_candidates


def build_quant_payload(max_symbols: int = 0, market_data=None) -> tuple[dict, list[dict]]:
    """Build the production Quant payload with activity and Wizard layers.

    Core ranking, regime, portfolio and walk-forward calculations remain in
    ``engine``. The activity monitor and Wizard shortlist are deliberately
    layered around those validated functions so neither can alter Quant Score
    or backtest behavior. The Wizard layer reuses the same in-memory daily
    TradingView snapshot and performs no additional market-data scan.
    """
    universe = tuple(market_data[1].values()) if market_data is not None else engine.get_universe()
    if len(universe) < engine.MIN_UNIVERSE:
        raise RuntimeError(f"universe_too_small:{len(universe)}<{engine.MIN_UNIVERSE}")
    if max_symbols:
        universe = universe[:max_symbols]

    benchmark = market_data[2].tail(300) if market_data is not None else engine._benchmark()
    completed_session = benchmark.index[-1]
    prices = ({s.symbol: market_data[0][s.symbol].tail(300) for s in universe
               if s.symbol in market_data[0] and len(market_data[0][s.symbol]) >= engine.MIN_BARS}
              if market_data is not None else engine._download_prices(universe))
    metadata = {security.symbol: security for security in universe}
    rows = [
        row
        for symbol, frame in prices.items()
        if (row := engine._features(metadata[symbol], frame, benchmark, completed_session))
    ]
    required = min(engine.MIN_UNIVERSE, len(universe)) if not max_symbols else max(1, int(len(universe) * 0.7))
    if len(rows) < required:
        raise RuntimeError(f"fresh_universe_too_small:{len(rows)}<{required}")

    scored = engine._score(rows)
    breadth, regime = engine._breadth(scored, benchmark)
    portfolio, portfolio_summary = engine._portfolio(scored, regime["state"])
    unexplained_activity = build_unexplained_activity(scored, prices, benchmark)
    held_symbols = {str(row.get("symbol")) for row in portfolio if row.get("symbol")}
    wizard_candidates, wizard_summary = build_wizard_candidates(
        scored,
        unexplained_activity,
        regime,
        held_symbols=held_symbols,
        limit=20,
    )

    generated = datetime.now(timezone.utc).isoformat()
    seed = f"{completed_session.date().isoformat()}|{len(scored)}|{regime['state']}"
    run_id = f"qv5-{completed_session.date().isoformat()}-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    research = [
        {
            "symbol": row["symbol"],
            "sector": row["sector"],
            "quality_score": engine._finite(row["quality_score"]),
            "momentum_score": engine._finite(row["momentum_score"]),
            "risk_score": engine._finite(row["risk_score"]),
            "summary": (
                f"Rank {row['rank']} with quant score {row['quant_score']:.1f}; "
                f"{row['action']} under {regime['state']} regime."
            ),
        }
        for row in scored
    ]

    level_counts = {"VERY HIGH": 0, "HIGH": 0, "ELEVATED": 0}
    direction_counts = {"POSITIVE": 0, "NEGATIVE": 0}
    for row in unexplained_activity:
        level = row.get("activity_level")
        if level in level_counts:
            level_counts[level] += 1
        direction = row.get("direction")
        if direction in direction_counts:
            direction_counts[direction] += 1

    methodology = {
        "price_adjustment": "unadjusted",
        "session_gate": completed_session.date().isoformat(),
        "stale_symbols_excluded": len(prices) - len(rows),
        "unexplained_activity": ACTIVITY_METHODOLOGY,
        "wizard": WIZARD_METHODOLOGY,
        "market_snapshot_hash": market_data[3]["data_hash"] if market_data is not None else None,
    }
    payload = {
        "version": "5.0.0",
        "engine": "Bursa MusangKing Quant v5",
        "run_id": run_id,
        "scan_date": completed_session.date().isoformat(),
        "generated_at": generated,
        "market": "MYX",
        "benchmark": "^KLSE",
        "universe_size": len(universe),
        "fresh_symbols": len(scored),
        "regime": regime,
        "breadth": breadth,
        "stocks": scored,
        "portfolio": portfolio,
        "portfolio_summary": portfolio_summary,
        "wizard_candidates": wizard_candidates,
        "wizard_summary": wizard_summary,
        "research": research[:120],
        # ``unexplained_activity`` is the canonical Step 14 name. Keep the old
        # key as a read-only compatibility alias for existing dashboard builds.
        "unexplained_activity": unexplained_activity,
        "abnormal_activity": unexplained_activity,
        "unexplained_activity_summary": {
            "flagged": len(unexplained_activity),
            "very_high": level_counts["VERY HIGH"],
            "high": level_counts["HIGH"],
            "elevated": level_counts["ELEVATED"],
            "positive": direction_counts["POSITIVE"],
            "negative": direction_counts["NEGATIVE"],
            "max_score": max((row["activity_score"] for row in unexplained_activity), default=0),
        },
        "backtest": engine._backtest(metadata, prices, benchmark),
        "performance": {
            "live_trades": 0,
            "open_trades": 0,
            "closed_trades": 0,
            "hit_rate": 0,
            "realized_return": 0,
            "equity_curve": [],
        },
        "methodology": methodology,
    }
    return payload, research


def write_artifacts(destination: str | Path, max_symbols: int = 0, market_data=None) -> Path:
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    payload, research = build_quant_payload(max_symbols=max_symbols, market_data=market_data)
    (target / "latest.json").write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    (target / "research.json").write_text(
        json.dumps(research, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    return target
