from __future__ import annotations

from collections import Counter
from typing import Iterable


REGIME_COMPATIBILITY = {
    "STRONG RISK-ON": 100.0,
    "RISK-ON": 85.0,
    "NEUTRAL": 60.0,
    "RISK-OFF": 30.0,
    "STRONG RISK-OFF": 10.0,
}

RISK_BUDGET_PCT = {
    "STRONG RISK-ON": 1.00,
    "RISK-ON": 0.75,
    "NEUTRAL": 0.50,
    "RISK-OFF": 0.25,
    "STRONG RISK-OFF": 0.00,
}

WIZARD_METHODOLOGY = {
    "version": "1.0",
    "purpose": "Decision layer derived from the already-published daily Quant snapshot; it does not download market data.",
    "candidate_limit": 20,
    "core_setups": {
        "W1": "Momentum Breakout",
        "W2": "Exhaustion Reversal",
        "W3": "In-Play Opportunity",
        "W4": "Event / Special Situation when event fields are available",
    },
    "wizard_score_weights": {
        "setup_quality": 30,
        "quant_strength": 25,
        "relative_strength": 15,
        "abnormal_activity": 10,
        "regime_compatibility": 10,
        "risk_reward_quality": 10,
    },
    "actions": ["BUY", "BUY CANDIDATE", "WATCH", "ADD", "HOLD", "TRIM", "SELL", "AVOID"],
    "note": "MW-inspired implementation rules, not proprietary book formulas or investment advice.",
}


def _number(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _rounded(value: float | None, digits: int = 4):
    if value is None:
        return None
    return round(float(value), digits)


def _activity_map(rows: Iterable[dict]) -> dict[str, dict]:
    return {str(row.get("symbol", "")): row for row in rows if row.get("symbol")}


def _activity_direction(row: dict | None, stock: dict) -> str:
    if row and row.get("direction") in {"POSITIVE", "NEGATIVE"}:
        return str(row["direction"])
    price = _number(stock.get("price_z20"))
    rs = _number(stock.get("rs_20d"))
    return "POSITIVE" if (price >= 0 or rs >= 0) else "NEGATIVE"


def _activity_strength(row: dict | None, stock: dict) -> float:
    if row:
        return _clamp(_number(row.get("activity_score")))
    return _clamp(max(abs(_number(stock.get("price_z20"))), abs(_number(stock.get("volume_z20")))) * 20.0)


def _rs_quality(stock: dict) -> float:
    return _clamp(50.0 + _number(stock.get("rs_20d")) * 4.0)


def _volume_quality(stock: dict) -> float:
    return _clamp(45.0 + (_number(stock.get("volume_ratio"), 1.0) - 1.0) * 35.0)


def _w1(stock: dict) -> tuple[float, bool]:
    trend = _number(stock.get("trend_score"))
    momentum = _number(stock.get("momentum_score"))
    breakout = 100.0 if stock.get("new_20d_high") else 75.0 if stock.get("above_20dma") and stock.get("above_50dma") else 35.0
    score = (
        trend * 0.30
        + momentum * 0.30
        + breakout * 0.15
        + _volume_quality(stock) * 0.10
        + _rs_quality(stock) * 0.15
    )
    confirmed = bool(
        stock.get("new_20d_high")
        and _number(stock.get("volume_ratio"), 1.0) >= 1.10
        and _number(stock.get("rs_20d")) > 0
        and _number(stock.get("quant_score")) >= 67
    )
    return _clamp(score), confirmed


def _w2(stock: dict) -> tuple[float, bool]:
    rsi = _number(stock.get("rsi"), 50.0)
    if 35 <= rsi <= 55:
        rsi_fit = 100.0
    elif rsi < 35:
        rsi_fit = 80.0
    elif rsi <= 65:
        rsi_fit = 55.0
    else:
        rsi_fit = 15.0
    bounce = _clamp(max(0.0, _number(stock.get("price_z20"))) * 30.0)
    prior_weakness = _clamp(max(0.0, -_number(stock.get("rs_20d"))) * 4.0)
    volume = _clamp(max(0.0, _number(stock.get("volume_z20"))) * 25.0)
    score = bounce * 0.35 + prior_weakness * 0.25 + rsi_fit * 0.20 + volume * 0.20
    confirmed = bool(
        _number(stock.get("price_z20")) >= 1.0
        and _number(stock.get("volume_z20")) >= 1.0
        and rsi <= 60
        and _number(stock.get("rs_20d")) <= 2
    )
    return _clamp(score), confirmed


def _w3(stock: dict, activity: dict | None) -> tuple[float, bool]:
    activity_score = _activity_strength(activity, stock)
    direction = _activity_direction(activity, stock)
    direction_score = 100.0 if direction == "POSITIVE" else 25.0
    score = (
        activity_score * 0.40
        + _number(stock.get("quant_score")) * 0.20
        + _number(stock.get("quality_score")) * 0.15
        + _volume_quality(stock) * 0.15
        + direction_score * 0.10
    )
    confirmed = bool(
        activity_score >= 65
        and direction == "POSITIVE"
        and _number(stock.get("price_z20")) > 0
        and _number(stock.get("volume_ratio"), 1.0) >= 1.10
    )
    return _clamp(score), confirmed


def _w4(stock: dict) -> tuple[float, bool]:
    event_score = _number(stock.get("event_score"))
    if not stock.get("event_type") and event_score <= 0:
        return 0.0, False
    score = event_score * 0.70 + _number(stock.get("quant_score")) * 0.30
    return _clamp(score), bool(event_score >= 70)


def _best_setup(stock: dict, activity: dict | None) -> tuple[str, str, float, bool, dict[str, float]]:
    candidates = {
        "W1": ("Momentum Breakout", *_w1(stock)),
        "W2": ("Exhaustion Reversal", *_w2(stock)),
        "W3": ("In-Play Opportunity", *_w3(stock, activity)),
        "W4": ("Event / Special Situation", *_w4(stock)),
    }
    code, (name, score, confirmed) = max(candidates.items(), key=lambda item: item[1][1])
    scores = {key: round(value[1], 2) for key, value in candidates.items()}
    return code, name, score, confirmed, scores


def _levels(stock: dict, confirmed: bool) -> dict:
    close = max(0.001, _number(stock.get("close"), 0.001))
    atr = max(0.0, _number(stock.get("atr")))
    expected_edge = max(0.0, _number(stock.get("expected_edge")))
    stop = max(0.001, close - 2.5 * atr)
    entry = close if confirmed else close + 0.5 * atr
    model_reward_pct = max(expected_edge, (4.0 * atr / close * 100.0) if close else 0.0)
    first_trim = close * (1.0 + model_reward_pct / 100.0)
    risk_pct = max(0.1, (close - stop) / close * 100.0)
    reward_risk = expected_edge / risk_pct if expected_edge > 0 else 0.0
    return {
        "entry_trigger": _rounded(entry),
        "initial_stop": _rounded(stop),
        "first_trim": _rounded(first_trim),
        "add_above": _rounded(close + 0.75 * atr),
        "trailing_stop_atr": 3.0,
        "model_reward_risk": _rounded(reward_risk, 2),
        "risk_pct_to_stop": _rounded(risk_pct, 2),
    }


def _reasons(stock: dict, setup_code: str, activity: dict | None, confirmed: bool, liquidity_pass: bool, regime_state: str) -> list[str]:
    reasons: list[str] = []
    if setup_code == "W1":
        if stock.get("new_20d_high"):
            reasons.append("20-day breakout")
        if _number(stock.get("rs_20d")) > 0:
            reasons.append("positive relative strength")
        if _number(stock.get("volume_ratio"), 1.0) >= 1.10:
            reasons.append("volume expansion")
    elif setup_code == "W2":
        reasons.append("reversal after relative weakness")
        if _number(stock.get("price_z20")) >= 1.0:
            reasons.append("positive reversal bar")
    elif setup_code == "W3":
        reasons.append("unusual price/volume activity")
        if _activity_direction(activity, stock) == "POSITIVE":
            reasons.append("positive activity direction")
    elif setup_code == "W4":
        reasons.append(str(stock.get("event_type") or "event-driven setup"))
    if confirmed:
        reasons.append("entry confirmation present")
    if not liquidity_pass:
        reasons.append("liquidity gate failed")
    if regime_state in {"RISK-OFF", "STRONG RISK-OFF"}:
        reasons.append("market regime restricts risk")
    return reasons[:4]


def _action(
    stock: dict,
    *,
    held: bool,
    confirmed: bool,
    wizard_score: float,
    reward_risk: float,
    liquidity_pass: bool,
    risk_pass: bool,
    regime_state: str,
) -> str:
    base_action = str(stock.get("action") or "WATCH").upper()
    expected_edge = _number(stock.get("expected_edge"))
    risk_off = regime_state in {"RISK-OFF", "STRONG RISK-OFF"}

    if held:
        if base_action in {"EXIT", "REDUCE"} or wizard_score < 42:
            return "SELL"
        if base_action == "TRIM" or wizard_score < 58 or risk_off:
            return "TRIM"
        if confirmed and wizard_score >= 82 and base_action == "ADD" and not risk_off:
            return "ADD"
        return "HOLD"

    if not liquidity_pass or not risk_pass or regime_state == "STRONG RISK-OFF" or expected_edge <= 0:
        return "AVOID"
    if confirmed and wizard_score >= 82 and reward_risk >= 0.80:
        return "BUY"
    if wizard_score >= 72 and reward_risk >= 0.60 and not risk_off:
        return "BUY CANDIDATE"
    return "WATCH"


def build_wizard_candidates(
    scored: list[dict],
    activity_rows: list[dict],
    regime: dict,
    *,
    held_symbols: Iterable[str] = (),
    limit: int = 20,
) -> tuple[list[dict], dict]:
    """Build a 10-20 name decision shortlist from the existing daily snapshot.

    This function is intentionally pure with respect to market data: it consumes
    the scored rows and activity rows already created by the Quant pipeline and
    performs no network, TradingView, or database calls.
    """
    activity_by_symbol = _activity_map(activity_rows)
    held = {str(symbol).upper() for symbol in held_symbols}
    regime_state = str(regime.get("state") or "NEUTRAL")
    regime_score = REGIME_COMPATIBILITY.get(regime_state, REGIME_COMPATIBILITY["NEUTRAL"])
    risk_budget = RISK_BUDGET_PCT.get(regime_state, RISK_BUDGET_PCT["NEUTRAL"])
    candidates: list[dict] = []

    for stock in scored:
        symbol = str(stock.get("symbol") or "")
        if not symbol:
            continue
        activity = activity_by_symbol.get(symbol)
        setup_code, setup_name, setup_quality, confirmed, setup_scores = _best_setup(stock, activity)
        activity_score = _activity_strength(activity, stock)
        rs_quality = _rs_quality(stock)
        levels = _levels(stock, confirmed)
        reward_risk = _number(levels.get("model_reward_risk"))
        reward_risk_quality = _clamp(reward_risk * 50.0)
        wizard_score = _clamp(
            setup_quality * 0.30
            + _number(stock.get("quant_score")) * 0.25
            + rs_quality * 0.15
            + activity_score * 0.10
            + regime_score * 0.10
            + reward_risk_quality * 0.10
        )
        turnover = _number(stock.get("turnover"))
        liquidity_pass = bool(turnover >= 500_000 and _number(stock.get("quality_score")) >= 30)
        close = max(0.001, _number(stock.get("close"), 0.001))
        atr_fraction = _number(stock.get("atr")) / close
        risk_pass = bool(_number(stock.get("volatility")) <= 100 and atr_fraction <= 0.12)
        is_held = symbol.upper() in held
        action = _action(
            stock,
            held=is_held,
            confirmed=confirmed,
            wizard_score=wizard_score,
            reward_risk=reward_risk,
            liquidity_pass=liquidity_pass,
            risk_pass=risk_pass,
            regime_state=regime_state,
        )
        candidates.append({
            "symbol": symbol,
            "tv_symbol": stock.get("tv_symbol"),
            "name": stock.get("name"),
            "sector": stock.get("sector"),
            "close": stock.get("close"),
            "last_bar": stock.get("last_bar"),
            "quant_rank": stock.get("rank"),
            "quant_score": stock.get("quant_score"),
            "expected_edge": stock.get("expected_edge"),
            "confidence": stock.get("confidence"),
            "rs_20d": stock.get("rs_20d"),
            "rsi": stock.get("rsi"),
            "atr": stock.get("atr"),
            "volume_ratio": stock.get("volume_ratio"),
            "activity_score": round(activity_score, 2),
            "activity_direction": _activity_direction(activity, stock),
            "wizard_score": round(wizard_score, 2),
            "setup_code": setup_code,
            "setup_name": setup_name,
            "setup_quality": round(setup_quality, 2),
            "setup_scores": setup_scores,
            "entry_confirmed": confirmed,
            "action": action,
            "held": is_held,
            "liquidity_gate": "PASS" if liquidity_pass else "FAIL",
            "risk_gate": "PASS" if risk_pass else "FAIL",
            "regime": regime_state,
            "risk_budget_pct": risk_budget,
            "levels": levels,
            "reasons": _reasons(stock, setup_code, activity, confirmed, liquidity_pass, regime_state),
        })

    maximum = max(10, min(20, int(limit or 20)))
    candidates.sort(key=lambda row: (_number(row.get("wizard_score")), _number(row.get("quant_score"))), reverse=True)
    shortlist = candidates[:maximum]
    action_counts = Counter(row["action"] for row in shortlist)
    setup_counts = Counter(row["setup_code"] for row in shortlist)
    summary = {
        "candidate_limit": maximum,
        "candidate_count": len(shortlist),
        "regime": regime_state,
        "actions": dict(action_counts),
        "setups": dict(setup_counts),
        "source": "same_daily_quant_snapshot",
        "additional_market_scan": False,
        "methodology_version": WIZARD_METHODOLOGY["version"],
    }
    return shortlist, summary
