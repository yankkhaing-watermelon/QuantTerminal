from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

ACTIVITY_BASELINE_SESSIONS = 20
ACTIVITY_SINGLE_FACTOR_SIGMA = 3.0
ACTIVITY_MULTI_FACTOR_SIGMA = 2.0
ACTIVITY_LIMIT = 100

ACTIVITY_METHODOLOGY = {
    "name": "Neutral Unexplained Activity Monitor",
    "baseline_sessions": ACTIVITY_BASELINE_SESSIONS,
    "factors": {
        "price_return": "Latest daily return versus the prior 20 completed sessions.",
        "volume": "Latest log volume versus the prior 20 completed sessions.",
        "turnover": "Latest log price-times-volume turnover versus the prior 20 completed sessions.",
        "relative_strength": "Latest daily stock return minus KLCI return versus the prior 20 completed sessions.",
    },
    "trigger": "Flag when any factor is at least 3.0 sigma from its own history, or when at least two factors are each at least 2.0 sigma.",
    "score_range": [0, 100],
    "interpretation": "Descriptive deviation monitor only; a high score is not a directional trade signal and does not establish a cause.",
    "exclusions": [
        "No event-study database.",
        "No pre/post-announcement matching database.",
        "No insider-trading detection.",
        "No leaked-information identification.",
    ],
}


def _finite(value: float | int | np.number | None, digits: int = 3):
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def latest_prior_zscore(series: pd.Series, window: int = ACTIVITY_BASELINE_SESSIONS) -> float | None:
    """Standardize the latest observation against only the preceding sessions.

    Excluding the current observation from the reference window prevents a
    large move from diluting its own z-score. Values are clipped to +/-10 sigma
    so one numerical edge case cannot dominate the activity score.
    """
    clean = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < window + 1:
        return None
    current = float(clean.iloc[-1])
    reference = clean.iloc[-window - 1:-1]
    mean = float(reference.mean())
    std = float(reference.std(ddof=1))
    if not math.isfinite(std) or std <= 1e-12:
        # A completely flat baseline followed by a genuine change is still a
        # deviation. Scale by 5% of the baseline magnitude (or 1 for a zero
        # baseline) rather than dividing by an effectively zero variance.
        floor = max(abs(mean) * 0.05, 1.0)
        z = (current - mean) / floor
    else:
        z = (current - mean) / std
    return _finite(max(-10.0, min(10.0, z)))


def _factor_values(frame: pd.DataFrame, benchmark: pd.Series) -> dict[str, float | None]:
    close = pd.to_numeric(frame["Close"], errors="coerce")
    volume = pd.to_numeric(frame["Volume"], errors="coerce").clip(lower=0)
    returns = close.pct_change()
    benchmark_returns = benchmark.reindex(close.index).ffill().pct_change()
    relative = returns - benchmark_returns
    turnover = close.clip(lower=0) * volume

    return {
        "price_return": latest_prior_zscore(returns),
        "volume": latest_prior_zscore(np.log1p(volume)),
        "turnover": latest_prior_zscore(np.log1p(turnover)),
        "relative_strength": latest_prior_zscore(relative),
    }


def activity_score(factors: Mapping[str, float | int | None]) -> float:
    magnitudes = sorted(
        [abs(float(value)) for value in factors.values() if value is not None and math.isfinite(float(value))],
        reverse=True,
    )
    if not magnitudes:
        return 0.0
    peak = magnitudes[0]
    second = magnitudes[1] if len(magnitudes) > 1 else 0.0
    average = sum(magnitudes) / len(magnitudes)
    multi_count = sum(value >= ACTIVITY_MULTI_FACTOR_SIGMA for value in magnitudes)
    score = peak * 15.0 + second * 7.5 + max(0, multi_count - 1) * 10.0 + max(0.0, average - 1.0) * 5.0
    return round(min(100.0, score), 3)


def should_flag(factors: Mapping[str, float | int | None]) -> bool:
    magnitudes = [abs(float(value)) for value in factors.values() if value is not None and math.isfinite(float(value))]
    return (
        any(value >= ACTIVITY_SINGLE_FACTOR_SIGMA for value in magnitudes)
        or sum(value >= ACTIVITY_MULTI_FACTOR_SIGMA for value in magnitudes) >= 2
    )


def _activity_level(score: float) -> str:
    if score >= 85:
        return "VERY HIGH"
    if score >= 70:
        return "HIGH"
    return "ELEVATED"


def _reason(factors: Mapping[str, float | int | None]) -> str:
    labels = {
        "price_return": "price-return",
        "volume": "volume",
        "turnover": "turnover",
        "relative_strength": "relative-strength",
    }
    ranked = sorted(
        (
            (key, abs(float(value)))
            for key, value in factors.items()
            if value is not None and math.isfinite(float(value)) and abs(float(value)) >= ACTIVITY_MULTI_FACTOR_SIGMA
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    selected = [labels[key] for key, _ in ranked[:3]]
    if not selected:
        return "Elevated multi-factor deviation"
    if len(selected) == 1:
        return f"Elevated {selected[0]} deviation"
    if len(selected) == 2:
        return f"Elevated {selected[0]} and {selected[1]} deviation"
    return f"Elevated {selected[0]}, {selected[1]} and {selected[2]} deviation"


def build_unexplained_activity(
    scored: list[dict],
    prices: Mapping[str, pd.DataFrame],
    benchmark: pd.Series,
    limit: int = ACTIVITY_LIMIT,
) -> list[dict]:
    """Build a neutral four-factor deviation list for the latest session."""
    rows: list[dict] = []
    for stock in scored:
        symbol = str(stock.get("symbol") or "").upper()
        frame = prices.get(symbol)
        if not symbol or frame is None or frame.empty:
            continue
        try:
            factors = _factor_values(frame, benchmark)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not should_flag(factors):
            continue
        score = activity_score(factors)
        rows.append({
            "symbol": symbol,
            "name": stock.get("name"),
            "sector": stock.get("sector"),
            "activity_score": score,
            "activity_level": _activity_level(score),
            "reason": _reason(factors),
            "factors": factors,
            "observation_date": pd.Timestamp(frame.index[-1]).date().isoformat(),
            "baseline_sessions": ACTIVITY_BASELINE_SESSIONS,
        })

    rows.sort(key=lambda row: (-float(row["activity_score"]), row["symbol"]))
    return rows[:max(0, int(limit))]
