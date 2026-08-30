from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

from quant.bmk_quant.engine import MIN_BARS
from quant.bmk_quant.universe import Security, get_universe

BATCH_SIZE = 60
MAX_WORKERS = 3
RETRIES = 3


def _frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    try:
        frame = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
        frame = frame.rename(columns=str.title)[["Open", "High", "Low", "Close", "Volume"]]
        frame = frame.dropna(subset=["Close"])
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        return frame if not frame.empty else None
    except (KeyError, TypeError, ValueError):
        return None


def _download_batch(securities: list[Security]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    tickers = [f"{security.symbol}.KL" for security in securities]
    last_error = ""
    for attempt in range(1, RETRIES + 1):
        try:
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
            frames: dict[str, pd.DataFrame] = {}
            missing: list[str] = []
            for security, ticker in zip(securities, tickers, strict=True):
                frame = _frame(raw, ticker)
                if frame is None:
                    missing.append(security.symbol)
                else:
                    frames[security.symbol] = frame
            return frames, missing
        except Exception as exc:  # noqa: BLE001 - preflight must continue and report failures
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < RETRIES:
                time.sleep(attempt * 2)

    # A transient batch failure must not hide healthy symbols. Retry each
    # symbol individually so the preflight can distinguish provider failure
    # from a genuinely unavailable listing.
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for security, ticker in zip(securities, tickers, strict=True):
        try:
            raw = yf.download(
                tickers=ticker,
                period="18mo",
                interval="1d",
                auto_adjust=False,
                actions=False,
                progress=False,
                timeout=30,
            )
            frame = _frame(raw, ticker)
            if frame is None:
                missing.append(security.symbol)
            else:
                frames[security.symbol] = frame
        except Exception:  # noqa: BLE001 - report as unavailable, never abort the universe
            missing.append(security.symbol)
    return frames, missing


def _benchmark() -> pd.Series:
    for attempt in range(1, RETRIES + 1):
        try:
            raw = yf.download(
                "^KLSE",
                period="18mo",
                interval="1d",
                auto_adjust=False,
                actions=False,
                progress=False,
                timeout=30,
            )
            close = raw.get("Close")
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            if close is not None:
                close = close.dropna()
                close.index = pd.to_datetime(close.index).tz_localize(None)
                if len(close) >= MIN_BARS:
                    return close
        except Exception:  # noqa: BLE001 - retry benchmark fetch
            pass
        if attempt < RETRIES:
            time.sleep(attempt * 2)
    raise RuntimeError("benchmark_unavailable_or_short")


def main() -> int:
    universe = get_universe()
    total = len(universe)
    if total == 0:
        raise SystemExit("FULL_UNIVERSE_PREFLIGHT_FAILED: universe is empty")

    numeric = [security for security in universe if security.symbol.isdigit()]
    non_numeric = [security.symbol for security in universe if not security.symbol.isdigit()]
    duplicate_codes = total - len({security.symbol for security in universe})

    benchmark = _benchmark()
    completed_session = benchmark.index[-1].normalize()

    chunks = [list(universe[i : i + BATCH_SIZE]) for i in range(0, total, BATCH_SIZE)]
    prices: dict[str, pd.DataFrame] = {}
    missing: set[str] = set()
    batch_errors = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_download_batch, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            try:
                frames, missing_symbols = future.result()
                prices.update(frames)
                missing.update(missing_symbols)
            except Exception:  # noqa: BLE001 - preflight must never die on one batch
                batch_errors += 1
                missing.update(security.symbol for security in futures[future])

    no_data = 0
    short_history = 0
    stale = 0
    fresh = 0
    latest_dates: dict[str, int] = {}

    for security in universe:
        frame = prices.get(security.symbol)
        if frame is None:
            no_data += 1
            continue
        if len(frame) < MIN_BARS:
            short_history += 1
            latest_dates[frame.index[-1].date().isoformat()] = latest_dates.get(frame.index[-1].date().isoformat(), 0) + 1
            continue
        last_session = frame.index[-1].normalize()
        latest_dates[last_session.date().isoformat()] = latest_dates.get(last_session.date().isoformat(), 0) + 1
        if last_session < completed_session:
            stale += 1
        else:
            fresh += 1

    expected_fresh = max(900, math.ceil(total * 0.80))
    coverage = fresh / total * 100
    print("=== Quant Terminal Full-Universe Preflight ===")
    print(f"Universe discovered: {total}")
    print(f"Numeric Bursa codes: {len(numeric)}")
    print(f"Non-numeric fallback symbols: {len(non_numeric)}")
    if non_numeric:
        print("Non-numeric examples:", ", ".join(non_numeric[:20]))
    print(f"Duplicate codes after dedupe: {duplicate_codes}")
    print(f"KLCI completed session: {completed_session.date().isoformat()}")
    print(f"Price frames returned: {len(prices)}")
    print(f"No price data: {no_data}")
    print(f"Short history (<{MIN_BARS} bars): {short_history}")
    print(f"Stale latest bar: {stale}")
    print(f"Fresh valid symbols: {fresh}")
    print(f"Fresh coverage: {coverage:.1f}%")
    print(f"Required production fresh coverage for current engine: >= {expected_fresh}")
    print(f"Batch-level hard failures: {batch_errors}")
    print("Latest-bar distribution:")
    for date, count in sorted(latest_dates.items(), key=lambda item: item[0], reverse=True)[:10]:
        print(f"  {date}: {count}")

    if total < 900:
        print("RESULT: FAIL — discovered Bursa universe is below MIN_UNIVERSE=900")
        return 1
    if fresh < 900:
        print("RESULT: FAIL — current production engine would reject this run")
        return 1
    if len(numeric) / total < 0.98:
        print("RESULT: WARN — more than 2% of the universe lacks a numeric Bursa code")
    if stale or short_history or no_data:
        print("RESULT: PASS WITH DATA-QUALITY EXCLUSIONS — enough fresh symbols for production")
    else:
        print("RESULT: PASS — full universe is clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
