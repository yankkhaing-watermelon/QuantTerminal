from __future__ import annotations

import hashlib
import json
import math
import os
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
BATCH_SIZE = int(os.getenv("YF_BATCH_SIZE", "60"))


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


def _download_chunk(securities: list[Security]) -> dict[str, pd.DataFrame]:
    tickers = [f"{security.symbol}.KL" for security in securities]
    raw = yf.download(
        tickers=tickers,
        period="18mo",
        interval="1d",
        auto_adjust=False,
        actions=False,
        group_by="ticker",
        threads=True,
        progress=False,
        timeout=30,
    )
    result: dict[str, pd.DataFrame] = {}
    for security, ticker in zip(securities, tickers, strict=True):
        try:
            frame = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
            frame = frame.rename(columns=str.title)[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            frame.index = pd.to_datetime(frame.index).tz_localize(None)
            if len(frame) >= MIN_BARS:
                result[security.symbol] = frame
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _download_prices(universe: tuple[Security, ...]) -> dict[str, pd.DataFrame]:
    chunks = [list(universe[index:index + BATCH_SIZE]) for index in range(0, len(universe), BATCH_SIZE)]
    prices: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_download_chunk, chunk) for chunk in chunks]
        for future in as_completed(futures):
            prices.update(future.result())
    return prices


def _benchmark() -> pd.Series:
    for _ in range(3):
        raw = yf.download("^KLSE", period="18mo", interval="1d", auto_adjust=False, actions=False, progress=False, timeout=30)
        close = raw.get("Close")
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        if close is not None:
            close = close.dropna()
            close.index = pd.to_datetime(close.index).tz_localize(None)
            if len(close) >= MIN_BARS:
                return close
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


def _backtest(scored: list[dict], prices: dict[str, pd.DataFrame]) -> dict:
    records = []
    for row in scored:
        frame = prices.get(row["symbol"])
        if frame is None or len(frame) < 140:
            continue
        close = frame.Close.astype(float)
        for lag in (80, 60, 40, 20):
            momentum = close.iloc[-lag - 1] / close.iloc[-lag - 61] - 1
            forward = close.iloc[-lag + 19] / close.iloc[-lag - 1] - 1 if lag >= 20 else np.nan
            if math.isfinite(momentum) and math.isfinite(forward):
                records.append({"momentum": momentum, "forward": forward})
    if not records:
        return {"total_trades": 0, "win_rate": 0, "expectancy": 0, "max_drawdown": 0, "groups": []}
    frame = pd.DataFrame(records)
    frame["group"] = pd.qcut(frame.momentum.rank(method="first"), 6, labels=["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"])
    groups = []
    for name, group in frame.groupby("group", observed=True):
        wins = group.forward[group.forward > 0]
        losses = group.forward[group.forward <= 0]
        profit_factor = wins.sum() / max(abs(losses.sum()), 1e-9)
        groups.append({"name": str(name), "trades": len(group), "win_rate": _finite((group.forward > 0).mean() * 100), "expectancy": _finite(group.forward.mean() * 100), "profit_factor": _finite(profit_factor)})
    equity = (1 + frame.forward.fillna(0)).cumprod()
    drawdown = equity / equity.cummax() - 1
    return {"total_trades": len(frame), "win_rate": _finite((frame.forward > 0).mean() * 100), "expectancy": _finite(frame.forward.mean() * 100), "max_drawdown": _finite(drawdown.min() * 100), "groups": groups}


def _portfolio(scored: list[dict]) -> tuple[list[dict], dict]:
    requested = {item.strip().upper() for item in os.getenv("PORTFOLIO_SYMBOLS", "").split(",") if item.strip()}
    selected = [row for row in scored if row["symbol"] in requested]
    if not selected:
        return [], {}
    raw = np.array([max(0, row["expected_edge"]) / max(row["volatility"], 5) for row in selected])
    weights = raw / raw.sum() * min(100, 12 * len(selected)) if raw.sum() else np.zeros(len(selected))
    result = []
    for row, weight in zip(selected, weights, strict=True):
        result.append({"symbol": row["symbol"], "action": row["action"], "target_weight": _finite(weight), "position_size": _finite(weight), "risk_contribution": _finite(weight * row["volatility"] / 100), "stop_price": _finite(row["close"] - 3 * row["atr"], 4), "beta": row["beta"]})
    summary = {"capital_deployed": _finite(weights.sum()), "beta": _finite(np.average([row["beta"] for row in selected], weights=np.maximum(weights, .001))), "risk_used": _finite(sum(row["risk_contribution"] for row in result)), "diversification_score": _finite(min(100, len(result) * 12.5))}
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
    portfolio, portfolio_summary = _portfolio(scored)
    anomalies = [{"symbol": row["symbol"], "activity_score": _finite(max(abs(row["price_z20"] or 0), abs(row["volume_z20"] or 0)) * 20), "reason": "Unusual price/volume deviation", "factors": {"price": row["price_z20"], "volume": row["volume_z20"]}} for row in scored if max(abs(row["price_z20"] or 0), abs(row["volume_z20"] or 0)) >= 2.5]
    generated = datetime.now(timezone.utc).isoformat()
    seed = f"{completed_session.date().isoformat()}|{len(scored)}|{regime['state']}"
    run_id = f"qv5-{completed_session.date().isoformat()}-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    research = [{"symbol": row["symbol"], "sector": row["sector"], "quality_score": _finite(row["quality_score"]), "momentum_score": _finite(row["momentum_score"]), "risk_score": _finite(row["risk_score"]), "summary": f"Rank {row['rank']} with quant score {row['quant_score']:.1f}; {row['action']} under {regime['state']} regime."} for row in scored]
    payload = {
        "version": "5.0.0", "engine": "Bursa MusangKing Quant v5", "run_id": run_id,
        "scan_date": completed_session.date().isoformat(), "generated_at": generated, "market": "MYX", "benchmark": "^KLSE",
        "universe_size": len(universe), "fresh_symbols": len(scored), "regime": regime, "breadth": breadth,
        "stocks": scored[:300], "portfolio": portfolio, "portfolio_summary": portfolio_summary,
        "research": research[:120], "abnormal_activity": sorted(anomalies, key=lambda row: row["activity_score"], reverse=True)[:100],
        "backtest": _backtest(scored, prices),
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
