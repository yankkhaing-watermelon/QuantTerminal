"""Astra v1: fixed-rule, long-only research. No dependency on Quant scores.

Signals use completed daily bars. Execution consumes only yesterday's signals
and stops; today's close can change tomorrow's stop, never today's fill.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math

import numpy as np
import pandas as pd

VERSION = "astra-1.0.0"
STRATEGIES = {
    "breakout": "55-day trend breakout",
    "pullback": "20-day trend pullback",
}


@dataclass(frozen=True)
class Config:
    capital: float = 100000
    risk_pct: float = .5
    max_positions: int = 8
    max_position_pct: float = 10
    max_sector_pct: float = 25
    min_turnover: float = 1000000
    participation_pct: float = 1
    initial_atr: float = 2.5
    trailing_atr: float = 3
    fee_bps: float = 20  # combined per-side cost approximation, not a broker tariff
    minimum_fee: float = 8
    slippage_bps: float = 10
    breadth_filter: bool = False

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key != "breadth_filter" and (not math.isfinite(value) or value < 0):
                raise ValueError(f"invalid_{key}")
        if self.capital <= 0 or self.initial_atr <= 0 or self.trailing_atr <= 0:
            raise ValueError("capital_and_atr_must_be_positive")
        if not 1 <= self.max_positions <= 50 or int(self.max_positions) != self.max_positions:
            raise ValueError("invalid_max_positions")
        if not 0 < self.risk_pct <= 5 or not 0 < self.participation_pct <= 5:
            raise ValueError("invalid_risk_or_participation")
        if not 0 < self.max_position_pct <= 100 or not 0 < self.max_sector_pct <= 100:
            raise ValueError("invalid_exposure_limit")
        if max(self.fee_bps, self.slippage_bps) > 500:
            raise ValueError("cost_assumption_too_large")


def fee(value, config):
    return max(config.minimum_fee, value * config.fee_bps / 10000) if value > 0 else 0


def tick_round(price, up=False):
    """Ordinary-share price grid; conservative rounding of simulated fills."""
    tick = .005 if price < 1 else .01 if price < 10 else .02 if price < 100 else .10
    return round((math.ceil(price / tick - 1e-9) if up else math.floor(price / tick + 1e-9)) * tick, 3)


def features(frame):
    c = frame.Close
    ma20, ma50, ma200 = (c.rolling(n).mean() for n in (20, 50, 200))
    tr = pd.concat([frame.High - frame.Low, (frame.High - c.shift()).abs(),
                    (frame.Low - c.shift()).abs()], axis=1).max(axis=1)
    f = pd.DataFrame(index=frame.index)
    f["close"] = c
    f["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    f["turnover"] = (c * frame.Volume).shift().rolling(60).median()
    f["momentum"] = c.pct_change(126, fill_method=None) * 100
    f["ready"] = ma200.shift(20).notna() & (frame.Volume > 0) & (f.atr > 0)
    f["above200"] = c > ma200
    f["trend"] = (c > ma50) & (ma50 > ma200) & (ma200 > ma200.shift(20))
    f["breakout"] = c > frame.High.shift().rolling(55).max()
    # Pullback confirmation is a close back above MA20 after a close at/below
    # MA20 yesterday, while the longer-term trend remains intact.
    f["pullback"] = (c > ma20) & (c.shift() <= ma20.shift())
    f["breakout_level"] = frame.High.shift().rolling(55).max()
    return f


def prepare(prices, config):
    parts = []
    for symbol, frame in prices.items():
        f = features(frame)
        f["symbol"] = symbol
        f["date"] = f.index
        parts.append(f)
    if not parts:
        return pd.DataFrame(), {key: {} for key in STRATEGIES}
    table = pd.concat(parts, ignore_index=True)
    # Cross-sectional rank is reconstructed independently for every date.
    eligible = table.ready & table.momentum.notna() & (table.turnover >= config.min_turnover)
    table["rs_percentile"] = np.nan
    table.loc[eligible, "rs_percentile"] = table.loc[eligible].groupby("date").momentum.rank(pct=True) * 100
    breadth = table.loc[table.ready].groupby("date").above200.mean() * 100
    table["breadth"] = table.date.map(breadth)
    gate = eligible & table.trend & (table.rs_percentile >= 80)
    if config.breadth_filter:
        gate &= table.breadth > 50
    signals = {}
    for key in STRATEGIES:
        rows = table.loc[gate & table[key]].sort_values(
            ["date", "momentum", "turnover", "symbol"], ascending=[True, False, False, True])
        signals[key] = {date: group.to_dict("records") for date, group in rows.groupby("date")}
    return table, signals


def simulate(prices, metadata, signals, dates, config):
    cash = config.capital
    positions, trades, curve = {}, [], []
    pending = []
    peak = config.capital
    previous_equity = config.capital
    skipped, stale_marks = 0, 0
    for date in dates:
        # Update marks at the open only. Missing bars never fabricate fills.
        bars = {}
        for symbol in set(positions) | {row["symbol"] for row in pending}:
            frame = prices.get(symbol)
            if frame is not None and date in frame.index:
                bars[symbol] = frame.loc[date]
        for symbol, position in positions.items():
            if symbol in bars:
                position["mark"] = float(bars[symbol].Open)
        equity_open = cash + sum(p["shares"] * p["mark"] for p in positions.values())

        def observe_trigger(p, bar):
            # Diagnostic only: retain the first observed breach, including bars
            # on which the execution model cannot fill. Never alter fill rules.
            if p["stop_trigger"] is None and bar.Low <= p["stop"]:
                p["stop_trigger"] = {"date": date.date().isoformat(),
                    "stop": p["stop"], "open": float(bar.Open),
                    "reason": "gap_stop" if bar.Open <= p["stop"] else "stop",
                    "volume": float(bar.Volume),
                    "fillable_bar": bool(bar.Volume > 0 and bar.High != bar.Low)}

        for symbol, p in positions.items():
            if symbol in bars:
                observe_trigger(p, bars[symbol])

        def sell(symbol, raw, reason):
            nonlocal cash
            p = positions[symbol]
            observe_trigger(p, bars[symbol])
            execution = max(.005, tick_round(raw * (1 - config.slippage_bps / 10000)))
            # A limit-down/suspended bar or insufficient volume can delay a stop.
            available = int(float(bars[symbol].Volume) * config.participation_pct / 100 / 100) * 100
            quantity = min(p["shares"], available)
            if quantity < 100:
                return
            value = quantity * execution
            exit_fee = fee(value, config)
            allocated_cost = p["entry_cost"] * quantity / p["initial_shares"]
            pnl = value - exit_fee - allocated_cost
            cash += value - exit_fee
            p["realized_pnl"] += pnl
            p["exit_value"] += value
            p["exit_fees"] += exit_fee
            p["exit_fills"].append({"date": date.date().isoformat(),
                "reason": reason, "stop": p["stop"], "raw_price": raw,
                "price": execution, "shares": quantity, "fee": exit_fee,
                "remaining_shares": p["shares"] - quantity})
            p["shares"] -= quantity
            p["exit_pending"] = True
            p["exit_reason"] = reason
            if p["shares"] == 0:
                trades.append({"symbol": symbol, "name": p["name"], "sector": p["sector"],
                    "signal_date": p["signal_date"], "entry_date": p["entry_date"],
                    "exit_date": date.date().isoformat(), "entry": p["entry"],
                    "exit": p["exit_value"] / p["initial_shares"], "shares": p["initial_shares"],
                    "initial_stop": p["initial_stop"], "pnl": p["realized_pnl"],
                    "r": p["realized_pnl"] / p["initial_risk"],
                    "fees": p["entry_fee"] + p["exit_fees"], "reason": reason,
                    "initial_risk": p["initial_risk"], "entry_fee": p["entry_fee"],
                    "signal_atr": p["signal_atr"], "signal_turnover": p["signal_turnover"],
                    "signal_breadth": p["signal_breadth"], "signal_rs_percentile": p["signal_rs_percentile"],
                    "signal_momentum": p["signal_momentum"],
                    "stop_trigger": p["stop_trigger"], "exit_fills": p["exit_fills"]})
                del positions[symbol]

        exited = set()
        # Only exits known at the opening can release cash for opening entries.
        for symbol, p in list(positions.items()):
            bar = bars.get(symbol)
            if bar is None or bar.Volume <= 0 or bar.High == bar.Low:
                continue
            if p["exit_pending"] or bar.Open <= p["stop"]:
                sell(symbol, float(bar.Open), "delayed_stop" if p["exit_pending"] else "gap_stop")
                exited.add(symbol)

        for row in pending:
            symbol = row["symbol"]
            if symbol in positions or symbol in exited:
                continue
            bar = bars.get(symbol)
            if len(positions) >= config.max_positions or bar is None or bar.Volume <= 0 or bar.High == bar.Low:
                skipped += 1
                continue
            entry = tick_round(float(bar.Open) * (1 + config.slippage_bps / 10000), up=True)
            stop = tick_round(entry - config.initial_atr * row["atr"])
            if stop <= 0 or stop >= entry:
                skipped += 1
                continue
            sector = metadata[symbol].sector or "Unclassified"
            equity_open = cash + sum(p["shares"] * p["mark"] for p in positions.values())
            sector_value = sum(p["shares"] * p["mark"] for p in positions.values() if p["sector"] == sector)
            available_value = min(cash, equity_open * config.max_position_pct / 100,
                                  max(0, equity_open * config.max_sector_pct / 100 - sector_value),
                                  row["turnover"] * config.participation_pct / 100)
            per_share_risk = entry - stop
            shares = int(min(available_value / entry, equity_open * config.risk_pct / 100 / per_share_risk,
                             float(bar.Volume) * config.participation_pct / 100) / 100) * 100
            while shares >= 100:
                entry_fee = fee(shares * entry, config)
                risk = shares * per_share_risk + entry_fee + fee(shares * stop, config)
                if shares * entry + entry_fee <= available_value and risk <= equity_open * config.risk_pct / 100:
                    break
                shares -= 100
            if shares < 100:
                skipped += 1
                continue
            cash -= shares * entry + entry_fee
            positions[symbol] = {"name": metadata[symbol].name, "sector": sector,
                "entry": entry, "entry_date": date.date().isoformat(), "signal_date": row["date"].date().isoformat(),
                "initial_shares": shares, "shares": shares, "stop": stop, "initial_stop": stop,
                "initial_risk": risk, "entry_cost": shares * entry + entry_fee, "entry_fee": entry_fee,
                "atr": row["atr"], "highest_close": None, "mark": entry, "exit_pending": False,
                "signal_atr": row["atr"], "signal_turnover": row["turnover"],
                "signal_breadth": row.get("breadth"), "signal_rs_percentile": row.get("rs_percentile"),
                "signal_momentum": row.get("momentum"), "stop_trigger": None, "exit_fills": [],
                "exit_reason": None, "realized_pnl": 0., "exit_value": 0., "exit_fees": 0.}

        for symbol, p in list(positions.items()):
            bar = bars.get(symbol)
            if bar is None:
                stale_marks += 1
                continue
            if symbol not in exited and bar.Volume > 0 and bar.High != bar.Low and bar.Low <= p["stop"]:
                sell(symbol, min(float(bar.Open), p["stop"]), "stop")
                if symbol not in positions:
                    continue
            p["mark"] = float(bar.Close)
            # Wilder ATR is updated from the previous close, not opening mark.
            prev_close = p.get("previous_close", row_close(prices[symbol], date))
            tr = max(float(bar.High - bar.Low), abs(float(bar.High) - prev_close), abs(float(bar.Low) - prev_close))
            p["atr"] = (p["atr"] * 13 + tr) / 14
            p["highest_close"] = max(p["highest_close"] if p["highest_close"] is not None else float(bar.Close), float(bar.Close))
            p["stop"] = max(p["stop"], tick_round(p["highest_close"] - config.trailing_atr * p["atr"]))
            p["previous_close"] = float(bar.Close)

        equity = cash + sum(p["shares"] * p["mark"] for p in positions.values())
        peak = max(peak, equity)
        curve.append({"date": date.date().isoformat(), "equity": round(equity, 2),
                      "drawdown_pct": round((equity / peak - 1) * 100, 4),
                      "return_pct": (equity / previous_equity - 1) * 100,
                      "exposure_pct": (1 - cash / equity) * 100 if equity else 0})
        previous_equity = equity
        pending = signals.get(date, [])

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    final = curve[-1]["equity"] if curve else config.capital
    years = (dates[-1] - dates[0]).days / 365.25 if len(dates) > 1 else 0
    yearly = {}
    prior = config.capital
    for year in sorted({p["date"][:4] for p in curve}):
        end = [p["equity"] for p in curve if p["date"].startswith(year)][-1]
        yearly[year] = (end / prior - 1) * 100
        prior = end
    streak = longest = 0
    for t in trades:
        streak = streak + 1 if t["pnl"] < 0 else 0
        longest = max(longest, streak)
    underwater = maximum_underwater = 0
    for point in curve:
        underwater = underwater + 1 if point["drawdown_pct"] < 0 else 0
        maximum_underwater = max(maximum_underwater, underwater)
    metrics = {"initial_capital": config.capital, "final_equity": final,
        "return_pct": (final / config.capital - 1) * 100,
        "cagr_pct": ((final / config.capital) ** (1 / years) - 1) * 100 if years >= 1 and final > 0 else None,
        "max_drawdown_pct": min((p["drawdown_pct"] for p in curve), default=0),
        "closed_trades": len(trades), "open_positions": len(positions),
        "win_rate": len(wins) / len(trades) * 100 if trades else None,
        "expectancy_r": float(np.mean([t["r"] for t in trades])) if trades else None,
        "profit_factor": sum(t["pnl"] for t in wins) / -sum(t["pnl"] for t in losses) if losses else None,
        "average_win_r": float(np.mean([t["r"] for t in wins])) if wins else None,
        "average_loss_r": float(np.mean([t["r"] for t in losses])) if losses else None,
        "longest_losing_streak": longest, "skipped_entries": skipped,
        "longest_underwater_sessions": maximum_underwater,
        "stale_position_days": stale_marks,
        "average_exposure_pct": float(np.mean([p["exposure_pct"] for p in curve])) if curve else 0,
        "yearly_returns": yearly}
    open_positions = [{"symbol": s, **p} for s, p in positions.items()]
    return {"metrics": metrics, "equity_curve": curve, "trades": trades, "open_positions": open_positions}


def row_close(frame, date):
    i = frame.index.get_loc(date)
    return float(frame.Close.iloc[max(0, i - 1)])


def evidence(metrics, validation, stress, sessions, benchmark_return):
    """Separate observed weakness from statistical readiness; never optimize it away."""
    reasons = []
    if sessions < 252:
        reasons.append("Less than one year of test sessions after indicator warm-up")
    if metrics["closed_trades"] < 30:
        reasons.append("Fewer than 30 closed trades")
    weaknesses = []
    if metrics["expectancy_r"] is not None and metrics["expectancy_r"] <= 0:
        weaknesses.append("Non-positive net expectancy")
    if validation["closed_trades"] and (validation["expectancy_r"] or 0) <= 0:
        weaknesses.append("Non-positive expectancy in the final-period test")
    if stress["return_pct"] <= 0:
        weaknesses.append("No positive portfolio return after doubled costs")
    if benchmark_return is not None and metrics["return_pct"] < benchmark_return:
        weaknesses.append("Full-period return below the KLCI price-return benchmark")
    return {"status": "WEAK RESULTS" if weaknesses else "INSUFFICIENT EVIDENCE" if reasons else "RESEARCH ONLY",
            "readiness": "NOT VALIDATED", "reasons": reasons, "weaknesses": weaknesses,
            "note": "These are review thresholds, not estimated probabilities of future profit. No strategy is promoted to live trading by this diagnostic."}


def build(prices, metadata, benchmark, config=Config()):
    prices = {symbol: frame.tail(300) for symbol, frame in prices.items()}
    benchmark = benchmark.tail(300)
    table, signals = prepare(prices, config)
    calendar = benchmark.index
    # 220 bars warm-up, independently of whether any strategy finds a trade.
    dates = calendar[220:]
    split = int(len(dates) * .7)
    validation_dates = dates[split:]
    result = {}
    latest = calendar[-1]
    benchmark_values = benchmark.reindex(dates)
    benchmark_equity = benchmark_values / benchmark_values.iloc[0] * config.capital if len(dates) else benchmark_values
    benchmark_drawdown = benchmark_equity / benchmark_equity.cummax() - 1
    for key, name in STRATEGIES.items():
        simulation = simulate(prices, metadata, signals[key], dates, config)
        validation = simulate(prices, metadata, signals[key], validation_dates, config)
        stress = simulate(prices, metadata, signals[key], dates, replace(config,
            fee_bps=config.fee_bps * 2, minimum_fee=config.minimum_fee * 2, slippage_bps=config.slippage_bps * 2))
        candidates = []
        for rank, row in enumerate(signals[key].get(latest, []), 1):
            security = metadata[row["symbol"]]
            candidates.append({"rank": rank, "symbol": security.symbol, "tv_symbol": security.tv_symbol,
                "name": security.name, "sector": security.sector,
                "close": row["close"], "atr": row["atr"], "momentum_pct": row["momentum"],
                "rs_percentile": row["rs_percentile"], "median_turnover": row["turnover"],
                "reference_stop": max(0, tick_round(row["close"] - config.initial_atr * row["atr"])),
                "signal_date": latest.date().isoformat()})
        result[key] = {"name": name, "candidates": candidates, **simulation,
            "validation": {"start": validation_dates[0].date().isoformat() if len(validation_dates) else None,
                           **validation["metrics"]}, "double_cost": stress["metrics"]}
        benchmark_return = float((benchmark_equity.iloc[-1] / config.capital - 1) * 100) if len(dates) else None
        result[key]["evidence"] = evidence(simulation["metrics"], validation["metrics"], stress["metrics"], len(dates), benchmark_return)
    daily = table.loc[table.date == latest] if not table.empty else table
    return {"version": VERSION, "config": asdict(config), "scan_date": latest.date().isoformat(),
        "history": {"benchmark_bars": len(calendar), "test_sessions": len(dates),
            "start": dates[0].date().isoformat() if len(dates) else None,
            "end": latest.date().isoformat(),
            "stock_bars_min": min((len(f) for f in prices.values()), default=0),
            "stock_bars_median": float(np.median([len(f) for f in prices.values()])) if prices else 0,
            "stock_bars_max": max((len(f) for f in prices.values()), default=0)},
        "eligible_today": int((daily.ready & (daily.turnover >= config.min_turnover)).sum()) if not daily.empty else 0,
        "breadth_pct": float(daily.loc[daily.ready, "above200"].mean() * 100) if not daily.empty and daily.ready.any() else None,
        "strategies": result,
        "benchmark": {"name": "KLCI buy and hold · price return only",
            "return_pct": float((benchmark_equity.iloc[-1] / config.capital - 1) * 100) if len(dates) else None,
            "max_drawdown_pct": float(benchmark_drawdown.min() * 100) if len(dates) else None},
        "limitations": [
            "Research simulation, not proven profitability or live trading performance.",
            "Current-universe survivorship bias; historical delisted-stock membership is unavailable.",
            "Corporate-action adjustments and dividends are not independently reconciled; results are provisional price-return diagnostics.",
            "Historical PN17/GN3 designations are unavailable; this exclusion is not applied.",
            "Costs are configurable combined per-side estimates, not a verified broker tariff.",
            "Daily bars cannot establish queue priority, spreads or precise intraday fills; zero-range bars defer execution.",
            "The final 30% is a chronological fixed-rule diagnostic, not untouched prospective evidence.",
            "Open positions are marked to last available close without assumed liquidation; stale marks can understate drawdown.",
            "Entry quantity uses daily volume only as an ex-post fill-capacity model, never as a ranking signal.",
        ]}
