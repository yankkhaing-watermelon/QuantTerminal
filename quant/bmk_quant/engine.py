from __future__ import annotations

import hashlib
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from .universe import Security, get_universe

MIN_BARS = 220
MIN_UNIVERSE = int(os.getenv("MIN_UNIVERSE", "900"))
# Yahoo can throttle nested parallel requests. Keep our own concurrency small
# and disable yfinance's internal thread pool. Smaller batches also make
# partial provider responses recoverable without discarding the whole batch.
BATCH_SIZE = int(os.getenv("YF_BATCH_SIZE", "30"))
MAX_WORKERS = int(os.getenv("YF_MAX_WORKERS", "2"))
DOWNLOAD_RETRIES = int(os.getenv("YF_DOWNLOAD_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("YF_RETRY_BACKOFF", "2"))


def _finite(value: float | int | np.number | None, digits: int = 6):
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _rsi(close: pd.Series, window: int = 14) -> float:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(window).mean()
    down = -delta.clip(upper=0).rolling(window).mean()
    average_up, average_down = float(up.iloc[-1]), float(down.iloc[-1])
    if average_down == 0:
        return 100.0 if average_up > 0 else 50.0
    if average_up == 0:
        return 0.0
    return float(100 - 100 / (1 + average_up / average_down))


def _percentile(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    ranked = series.rank(pct=True, method="average") * 100
    return ranked if higher_is_better else 100 - ranked


def _extract_price_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Normalize one ticker from a yfinance batch/single-symbol response."""
    try:
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            # yfinance normally returns ticker-first columns for group_by=ticker,
            # but tolerate field-first output as well.
            if ticker in raw.columns.get_level_values(0):
                frame = raw[ticker]
            elif ticker in raw.columns.get_level_values(-1):
                frame = raw.xs(ticker, axis=1, level=-1)
            else:
                return None
        else:
            frame = raw
        frame = frame.rename(columns=str.title)
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(column in frame.columns for column in required):
            return None
        frame = frame[required].dropna(subset=["Close"])
        if frame.empty:
            return None
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        return frame
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _download_once(tickers: list[str]) -> tuple[dict[str, pd.DataFrame], set[str]]:
    """Download a batch once and return usable frames plus unresolved tickers."""
    raw = yf.download(
        tickers=tickers,
        period="18mo",
        interval="1d",
        auto_adjust=False,
        actions=False,
        group_by="ticker",
        # Do not nest yfinance's threads inside our executor.
        threads=False,
        progress=False,
        timeout=30,
    )
    result: dict[str, pd.DataFrame] = {}
    unresolved: set[str] = set()
    for ticker in tickers:
        frame = _extract_price_frame(raw, ticker)
        if frame is None or len(frame) < MIN_BARS:
            unresolved.add(ticker)
            continue
        result[ticker] = frame
    return result, unresolved


def _download_single(ticker: str) -> pd.DataFrame | None:
    """Last-resort single-symbol request for a transient batch failure."""
    try:
        raw = yf.download(
            tickers=ticker,
            period="18mo",
            interval="1d",
            auto_adjust=False,
            actions=False,
            group_by="ticker",
            threads=False,
            progress=False,
            timeout=30,
        )
        frame = _extract_price_frame(raw, ticker)
        if frame is not None and len(frame) >= MIN_BARS:
            return frame
    except Exception:  # noqa: BLE001 - one provider failure must not abort the universe
        pass
    return None


def _download_chunk(securities: list[Security]) -> dict[str, pd.DataFrame]:
    """Resiliently download one universe chunk without letting partial Yahoo responses erase it."""
    ticker_by_symbol = {security.symbol: f"{security.symbol}.KL" for security in securities}
    pending = list(ticker_by_symbol.values())
    result_by_ticker: dict[str, pd.DataFrame] = {}

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        if not pending:
            break
        try:
            frames, unresolved = _download_once(pending)
            result_by_ticker.update(frames)
            pending = [ticker for ticker in pending if ticker in unresolved]
        except Exception:  # noqa: BLE001 - retry the affected batch
            pass
        if pending and attempt < DOWNLOAD_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    # If a batch is still partially unresolved, retry the remainder in small
    # serial sub-batches. This is the key protection against transient Yahoo
    # partial responses that previously left the full-universe run with only a
    # handful of frames (for example 71/1000+).
    if pending:
        for start in range(0, len(pending), 5):
            sub_pending = pending[start:start + 5]
            for attempt in range(1, 3):
                try:
                    frames, unresolved = _download_once(sub_pending)
                    result_by_ticker.update(frames)
                    sub_pending = [ticker for ticker in sub_pending if ticker in unresolved]
                except Exception:  # noqa: BLE001
                    pass
                if not sub_pending or attempt == 2:
                    break
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

            # Absolute last resort: individual requests for anything still
            # unresolved. Invalid/delisted symbols will simply remain absent.
            for ticker in sub_pending:
                frame = _download_single(ticker)
                if frame is not None:
                    result_by_ticker[ticker] = frame

    return {
        security.symbol: result_by_ticker[ticker_by_symbol[security.symbol]]
        for security in securities
        if ticker_by_symbol[security.symbol] in result_by_ticker
    }


def _download_prices(universe: tuple[Security, ...]) -> dict[str, pd.DataFrame]:
    chunks = [list(universe[index:index + BATCH_SIZE]) for index in range(0, len(universe), BATCH_SIZE)]
    prices: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_download_chunk, chunk) for chunk in chunks]
        for future in as_completed(futures):
            try:
                prices.update(future.result())
            except Exception as exc:  # noqa: BLE001 - one batch must never kill the full universe
                print(f"Yahoo batch failed and was skipped: {type(exc).__name__}: {exc}")
    print(f"Yahoo price coverage: {len(prices)}/{len(universe)} frames with >= {MIN_BARS} bars")
    return prices


def _benchmark() -> pd.Series:
    for attempt in range(3):
        try:
            raw = yf.download("^KLSE", period="18mo", interval="1d", auto_adjust=False, actions=False, progress=False, timeout=30)
            close = raw.get("Close")
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            if close is not None:
                close = close.dropna()
                close.index = pd.to_datetime(close.index).tz_localize(None)
                if len(close) >= MIN_BARS:
                    return close
        except Exception:
            pass
        if attempt < 2:
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise RuntimeError("benchmark_unavailable_or_short")


def _features(security: Security, frame: pd.DataFrame, benchmark: pd.Series, completed_session: pd.Timestamp) -> dict | None:
    if frame.index[-1].normalize() < completed_session.normalize():
        return None
    close = frame["Close"].astype(float)
    volume = frame["Volume"].astype(float)
    returns = close.pct_change()
    bench = benchmark.reindex(close.index).ffill().pct_change()
    joined = pd.concat([returns, bench], axis=1).dropna().tail(120)
    beta = joined.iloc[:, 0].cov(joined.iloc[:, 1]) / max(joined.iloc[:, 1].var(), 1e-12)
    residual_60d = close.pct_change(60).iloc[-1] - beta * benchmark.pct_change(60).iloc[-1]
    volatility = returns.tail(60).std() * np.sqrt(252)
    atr = pd.concat([(frame.High - frame.Low), (frame.High - close.shift()).abs(), (frame.Low - close.shift()).abs()], axis=1).max(axis=1).rolling(14).mean().iloc[-1]
    volume_ratio = volume.iloc[-1] / max(volume.tail(20).mean(), 1)
    turnover = close.iloc[-1] * volume.iloc[-1]
    return {
        **asdict(security),
        "close": _finite(close.iloc[-1], 4),
        "return_20d": _finite(close.pct_change(20).iloc[-1] * 100),
        "return_60d": _finite(close.pct_change(60).iloc[-1] * 100),
        "return_120d": _finite(close.pct_change(120).iloc[-1] * 100),
        "rs_20d": _finite((close.pct_change(20).iloc[-1] - benchmark.pct_change(20).iloc[-1]) * 100),
        "residual_momentum": _finite(residual_60d * 100),
        "volatility": _finite(volatility * 100),
        "beta": _finite(beta),
        "rsi": _finite(_rsi(close)),
        "atr": _finite(atr, 4),
        "volume_ratio": _finite(volume_ratio),
        "turnover": _finite(turnover, 0),
        "above_20dma": bool(close.iloc[-1] > close.tail(20).mean()),
        "above_50dma": bool(close.iloc[-1] > close.tail(50).mean()),
        "above_200dma": bool(close.iloc[-1] > close.tail(200).mean()),
        "new_20d_high": bool(close.iloc[-1] >= close.tail(20).max()),
        "new_52w_high": bool(close.iloc[-1] >= close.tail(252).max()),
        "last_bar": frame.index[-1].date().isoformat(),
        "price_z20": _finite((returns.iloc[-1] - returns.tail(20).mean()) / max(returns.tail(20).std(), 1e-12)),
        "volume_z20": _finite((volume.iloc[-1] - volume.tail(20).mean()) / max(volume.tail(20).std(), 1)),
    }


def _score(rows: list[dict]) -> list[dict]:
    table = pd.DataFrame(rows)
    table["momentum_score"] = (_percentile(table.residual_momentum) * .45 + _percentile(table.return_20d) * .25 + _percentile(table.return_120d) * .30)
    table["quality_score"] = (_percentile(table.turnover) * .45 + _percentile(table.volatility, False) * .35 + _percentile((table.rsi - 55).abs(), False) * .20)
    table["trend_score"] = (table.above_20dma.astype(int) * 25 + table.above_50dma.astype(int) * 30 + table.above_200dma.astype(int) * 35 + table.new_20d_high.astype(int) * 10)
    table["quant_score"] = table.momentum_score * .45 + table.quality_score * .25 + table.trend_score * .30
    table["expected_edge"] = ((table.quant_score - 50) / 10 + table.residual_momentum * .08).clip(-12, 18)
    table["confidence"] = (55 + (table.quant_score - 50).abs() * .7 + _percentile(table.turnover) * .1).clip(50, 95)
    table["risk_score"] = (100 - _percentile(table.volatility, False)).clip(0, 100)
    table["action"] = np.select(
        [table.quant_score >= 80, table.quant_score >= 67, table.quant_score >= 52, table.quant_score >= 40, table.quant_score >= 28],
        ["ADD", "HOLD", "WATCH", "TRIM", "REDUCE"], default="EXIT",
    )
    table = table.sort_values(["quant_score", "turnover"], ascending=False)
    table["rank"] = np.arange(1, len(table) + 1)
    return json.loads(table.replace({np.nan: None}).to_json(orient="records"))


def _breadth(scored: list[dict], benchmark: pd.Series) -> tuple[dict, dict]:
    table = pd.DataFrame(scored)
    sector = table.groupby("sector").above_50dma.mean() * 100
    advances = int((table.return_20d > 0).sum())
    declines = int((table.return_20d < 0).sum())
    breadth = {
        "above_20dma": _finite(table.above_20dma.mean() * 100),
        "above_50dma": _finite(table.above_50dma.mean() * 100),
        "above_200dma": _finite(table.above_200dma.mean() * 100),
        "advance_decline": _finite(advances / max(declines, 1), 3),
        "new_20d_highs": int(table.new_20d_high.sum()),
        "new_52w_highs": int(table.new_52w_high.sum()),
        "volume_breadth": _finite((table.volume_ratio > 1).mean() * 100),
        "sector_breadth": _finite((sector >= 50).mean() * 100),
        "participation": _finite((table.return_20d > benchmark.pct_change(20).iloc[-1] * 100).mean() * 100),
        "dispersion": _finite(table.return_20d.std()),
        "equal_weight_return_20d": _finite(table.return_20d.mean()),
    }
    trend = 100 if benchmark.iloc[-1] > benchmark.tail(200).mean() else (60 if benchmark.iloc[-1] > benchmark.tail(50).mean() else 20)
    volatility = max(0, 100 - min(100, float(benchmark.pct_change().tail(20).std() * np.sqrt(252) * 400)))
    components = {
        "klci_trend": trend,
        "bursa_breadth": breadth["above_50dma"],
        "sector_breadth": breadth["sector_breadth"],
        "volume_breadth": breadth["volume_breadth"],
        "volatility": _finite(volatility),
        "market_participation": breadth["participation"],
    }
    score = float(np.mean(list(components.values())))
    state = "STRONG RISK-ON" if score >= 75 else "RISK-ON" if score >= 60 else "NEUTRAL" if score >= 43 else "RISK-OFF" if score >= 28 else "STRONG RISK-OFF"
    return breadth, {"state": state, "score": _finite(score), "confidence": _finite(min(95, 55 + abs(score - 50))), "components": components, "summary": "KLCI trend, Bursa breadth, sector breadth, volume, volatility and participation."}


def _assign_quant_sextiles(frame: pd.DataFrame, group_labels: list[str]) -> pd.DataFrame:
    """Assign balanced, score-ordered groups independently inside each period."""
    assigned = frame.copy()
    assigned["group"] = None
    valid_indices: list[int] = []
    for _, cohort in assigned.groupby("period", sort=False):
        if len(cohort) < len(group_labels):
            continue
        ordered = cohort.sort_values("quant_score", kind="mergesort")
        for label, indices in zip(
            group_labels,
            np.array_split(ordered.index.to_numpy(), len(group_labels)),
            strict=True,
        ):
            assigned.loc[indices, "group"] = label
        valid_indices.extend(cohort.index.tolist())
    return assigned.loc[valid_indices]


def _backtest(metadata: dict[str, Security], prices: dict[str, pd.DataFrame], benchmark: pd.Series) -> dict:
    group_labels = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
    empty = {
        "total_trades": 0, "win_rate": 0, "expectancy": 0, "max_drawdown": 0,
        "groups": [], "cohorts": [],
        "methodology": {
            "grouping": "cross_sectional_quant_score_sextiles_by_period",
            "group_order": "Q1 lowest Quant Score; Q6 highest Quant Score",
            "minimum_signal_history_sessions": 200,
            "forward_horizon_sessions": 20,
            "drawdown": "equal_weight_period_cohort",
            "universe_basis": "current_fresh_universe",
            "transaction_costs_bps": 0,
        },
    }
    records = []
    for lag in (80, 60, 40, 20):
        signal_date = benchmark.index[-lag - 1]
        benchmark_asof = benchmark.loc[:signal_date]
        historical_rows = []
        forward_returns: dict[str, float] = {}
        for symbol, security in metadata.items():
            price_frame = prices.get(symbol)
            if price_frame is None:
                continue
            history = price_frame.loc[price_frame.index <= signal_date]
            future = price_frame.loc[price_frame.index > signal_date].head(20)
            if len(history) < 200 or len(future) < 20:
                continue
            row = _features(security, history, benchmark_asof, signal_date)
            if row is None:
                continue
            forward = float(future.Close.iloc[-1] / history.Close.iloc[-1] - 1)
            if math.isfinite(forward):
                historical_rows.append(row)
                forward_returns[symbol] = forward
        if len(historical_rows) < len(group_labels):
            continue
        for row in _score(historical_rows):
            forward = forward_returns.get(row["symbol"])
            if forward is not None:
                records.append({"quant_score": row["quant_score"], "forward": forward, "period": lag})
    if not records:
        return empty
    frame = _assign_quant_sextiles(pd.DataFrame(records), group_labels)
    if frame.empty:
        return empty
    groups = []
    for name in group_labels:
        group = frame[frame.group == name]
        wins = group.forward[group.forward > 0]
        losses = group.forward[group.forward <= 0]
        profit_factor = wins.sum() / max(abs(losses.sum()), 1e-9)
        groups.append({"name": str(name), "trades": len(group), "win_rate": _finite((group.forward > 0).mean() * 100), "expectancy": _finite(group.forward.mean() * 100), "profit_factor": _finite(profit_factor)})
    # Each lag is one cross-sectional observation period. Compounding every
    # stock return in row order treats simultaneous signals as sequential
    # trades and can manufacture a near -100% drawdown. Use the equal-weight
    # cohort return for each period to form a chronological equity curve.
    cohorts = []
    equity = 1.0
    peak = 1.0
    for period, cohort in sorted(frame.groupby("period"), key=lambda item: item[0], reverse=True):
        cohort_return = max(-0.999, float(cohort.forward.mean()))
        equity *= 1 + cohort_return
        peak = max(peak, equity)
        drawdown = equity / peak - 1
        cohorts.append({
            "lag_sessions": int(period),
            "observations": len(cohort),
            "wins": int((cohort.forward > 0).sum()),
            "return_pct": _finite(cohort_return * 100),
            "equity": _finite(equity, 8),
            "drawdown_pct": _finite(drawdown * 100),
        })
    return {
        "total_trades": len(frame),
        "win_rate": _finite((frame.forward > 0).mean() * 100),
        "expectancy": _finite(frame.forward.mean() * 100),
        "max_drawdown": _finite(min(row["drawdown_pct"] for row in cohorts)),
        "groups": groups,
        "cohorts": cohorts,
        "methodology": empty["methodology"],
    }


REGIME_EXPOSURE_CAP = {
    "STRONG RISK-ON": 100.0,
    "RISK-ON": 85.0,
    "NEUTRAL": 70.0,
    "RISK-OFF": 45.0,
    "STRONG RISK-OFF": 25.0,
}
MAX_POSITION_WEIGHT = 15.0
ACTION_SIZE_MULTIPLIER = {
    "ADD": 1.0,
    "HOLD": 0.85,
    "WATCH": 0.55,
    "TRIM": 0.35,
    "REDUCE": 0.15,
    "EXIT": 0.0,
}


def _capped_weights(raw: np.ndarray, total: float, cap: float) -> np.ndarray:
    """Proportionally allocate exposure without breaching a single-name cap."""
    weights = np.zeros(len(raw), dtype=float)
    active = [index for index, value in enumerate(raw) if value > 0]
    remaining = min(float(total), cap * len(active))
    while active and remaining > 1e-9:
        raw_total = float(sum(raw[index] for index in active))
        if raw_total <= 0:
            break
        provisional = {index: remaining * raw[index] / raw_total for index in active}
        capped = [index for index, value in provisional.items() if value > cap + 1e-9]
        if not capped:
            for index, value in provisional.items():
                weights[index] = value
            break
        for index in capped:
            weights[index] = cap
            remaining -= cap
            active.remove(index)
    return weights


def _portfolio(scored: list[dict], regime_state: str) -> tuple[list[dict], dict]:
    requested = {item.strip().upper() for item in os.getenv("PORTFOLIO_SYMBOLS", "").split(",") if item.strip()}
    selected = [row for row in scored if row["symbol"] in requested]
    if not selected:
        return [], {}
    exposure_cap = REGIME_EXPOSURE_CAP.get(regime_state, REGIME_EXPOSURE_CAP["NEUTRAL"])
    raw = np.array([
        max(0, row["expected_edge"]) * ACTION_SIZE_MULTIPLIER.get(row["action"], 0) / max(row["volatility"], 5)
        for row in selected
    ])
    weights = _capped_weights(raw, exposure_cap, MAX_POSITION_WEIGHT)
    result = []
    for row, weight in zip(selected, weights, strict=True):
        stop_price = max(0.001, row["close"] - 3 * row["atr"])
        result.append({
            "symbol": row["symbol"], "action": row["action"],
            "target_weight": _finite(weight), "position_size": _finite(weight),
            "risk_contribution": _finite(weight * row["volatility"] / 100),
            "stop_price": _finite(stop_price, 4), "beta": row["beta"],
        })
    deployed = sum(float(row["target_weight"] or 0) for row in result)
    active = [row for row in result if float(row["target_weight"] or 0) > 0]
    if deployed > 0:
        portfolio_beta = sum(float(row["target_weight"]) * float(row["beta"]) for row in active) / deployed
        normalized = [float(row["target_weight"]) / deployed for row in active]
        effective_positions = 1 / sum(weight * weight for weight in normalized)
    else:
        portfolio_beta, effective_positions = 0.0, 0.0
    summary = {
        "regime": regime_state,
        "exposure_cap": _finite(exposure_cap),
        "capital_deployed": _finite(deployed),
        "cash_reserve": _finite(100 - deployed),
        "beta": _finite(portfolio_beta),
        "risk_used": _finite(sum(float(row["risk_contribution"] or 0) for row in result)),
        "max_single_weight": _finite(max((float(row["target_weight"] or 0) for row in result), default=0)),
        "effective_positions": _finite(effective_positions),
        "diversification_score": _finite(min(100, effective_positions * 12.5)),
    }
    return result, summary


def build_quant_payload(max_symbols: int = 0) -> tuple[dict, list[dict]]:
    universe = get_universe()
    if len(universe) < MIN_UNIVERSE:
        raise RuntimeError(f"universe_too_small:{len(universe)}<{MIN_UNIVERSE}")
    if max_symbols:
        universe = universe[:max_symbols]
    benchmark = _benchmark()
    completed_session = benchmark.index[-1]
    prices = _download_prices(universe)
    metadata = {security.symbol: security for security in universe}
    rows = [row for symbol, frame in prices.items() if (row := _features(metadata[symbol], frame, benchmark, completed_session))]
    required = min(MIN_UNIVERSE, len(universe)) if not max_symbols else max(1, int(len(universe) * .7))
    if len(rows) < required:
        raise RuntimeError(f"fresh_universe_too_small:{len(rows)}<{required}")
    scored = _score(rows)
    breadth, regime = _breadth(scored, benchmark)
    portfolio, portfolio_summary = _portfolio(scored, regime["state"])
    anomalies = [{"symbol": row["symbol"], "activity_score": _finite(max(abs(row["price_z20"] or 0), abs(row["volume_z20"] or 0)) * 20), "reason": "Unusual price/volume deviation", "factors": {"price": row["price_z20"], "volume": row["volume_z20"]}} for row in scored if max(abs(row["price_z20"] or 0), abs(row["volume_z20"] or 0)) >= 2.5]
    generated = datetime.now(timezone.utc).isoformat()
    seed = f"{completed_session.date().isoformat()}|{len(scored)}|{regime['state']}"
    run_id = f"qv5-{completed_session.date().isoformat()}-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    research = [{"symbol": row["symbol"], "sector": row["sector"], "quality_score": _finite(row["quality_score"]), "momentum_score": _finite(row["momentum_score"]), "risk_score": _finite(row["risk_score"]), "summary": f"Rank {row['rank']} with quant score {row['quant_score']:.1f}; {row['action']} under {regime['state']} regime."} for row in scored]
    payload = {
        "version": "5.0.0", "engine": "Bursa MusangKing Quant v5", "run_id": run_id,
        "scan_date": completed_session.date().isoformat(), "generated_at": generated, "market": "MYX", "benchmark": "^KLSE",
        "universe_size": len(universe), "fresh_symbols": len(scored), "regime": regime, "breadth": breadth,
        "stocks": scored, "portfolio": portfolio, "portfolio_summary": portfolio_summary,
        "research": research[:120], "abnormal_activity": sorted(anomalies, key=lambda row: row["activity_score"], reverse=True)[:100],
        "backtest": _backtest(metadata, prices, benchmark),
        "performance": {"live_trades": 0, "open_trades": 0, "closed_trades": 0, "hit_rate": 0, "realized_return": 0, "equity_curve": []},
        "methodology": {"price_adjustment": "unadjusted", "session_gate": completed_session.date().isoformat(), "stale_symbols_excluded": len(prices) - len(rows)},
    }
    return payload, research


def write_artifacts(destination: str | Path, max_symbols: int = 0) -> Path:
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    payload, research = build_quant_payload(max_symbols=max_symbols)
    (target / "latest.json").write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (target / "research.json").write_text(json.dumps(research, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return target
