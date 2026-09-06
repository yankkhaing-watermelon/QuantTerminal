"""Isolated full-universe collector; never changes Quant's download settings."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import hashlib
import json
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from . import engine
from .universe import Security, TV_SCAN_URL, _stock_code_from_isin


def discover(session=None, details=False):
    session = session or requests.Session()
    columns = ["name", "description", "sector", "market_cap_basic", "type", "isin", "typespecs"]
    securities, seen, excluded, offset, total = {}, set(), [], 0, None
    while total is None or offset < total:
        request = {"filter": [{"left": "exchange", "operation": "equal", "right": "MYX"},
                              {"left": "type", "operation": "equal", "right": "stock"}],
            "options": {"lang": "en"}, "markets": ["malaysia"],
            "symbols": {"query": {"types": []}, "tickers": []}, "columns": columns,
            "sort": {"sortBy": "name", "sortOrder": "asc"}, "range": [offset, offset + 500]}
        response = session.post(TV_SCAN_URL, json=request, timeout=40)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", [])
        reported = payload.get("totalCount")
        if reported is None:
            raise RuntimeError("TradingView did not report universe totalCount")
        total = max(total or 0, int(reported))
        if not rows:
            raise RuntimeError(f"incomplete_universe_page:{offset}/{total}")
        before = len(seen)
        for row in rows:
            values = dict(zip(columns, row.get("d", [])))
            ticker = str(values.get("name") or row.get("s", "").split(":")[-1]).upper().strip()
            if not ticker:
                raise RuntimeError("missing_tradingview_ticker")
            seen.add(ticker)
            if "common" not in (values.get("typespecs") or []):
                excluded.append({"symbol": ticker, "name": str(values.get("description") or ticker),
                                 "reason": "excluded_non_ordinary_share", "bars": 0})
                continue
            code = _stock_code_from_isin(values.get("isin")) or ticker
            if code in securities and securities[code].tv_symbol != ticker:
                raise RuntimeError(f"ambiguous_stock_code:{code}")
            securities[code] = Security(code, str(values.get("description") or ticker),
                str(values.get("sector") or "Unclassified"), float(values.get("market_cap_basic") or 0), ticker)
        if len(seen) == before:
            raise RuntimeError("TradingView repeated a universe page")
        offset += len(rows)
    if len(seen) != total:
        raise RuntimeError(f"universe_changed_during_pagination:{len(seen)}/{total};retry_scan")
    universe = tuple(securities.values())
    return (universe, excluded, total) if details else universe


def completed_cutoff(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
    now = now.astimezone(ZoneInfo("Asia/Kuala_Lumpur"))
    cutoff = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
    while cutoff.weekday() >= 5:
        cutoff -= timedelta(days=1)
    # The actual last session is subsequently taken from the benchmark.
    return pd.Timestamp(cutoff)


def normalize_daily(raw, symbol):
    if raw is None or raw.empty:
        return None
    raw = raw.copy()
    index = pd.DatetimeIndex(raw.index)
    if index.tz is None:
        # tvdatafeed creates naive datetime.fromtimestamp values in the host's
        # local timezone. Recover the epoch before converting to MYT; merely
        # normalizing these local dates shifts sessions on non-Asian hosts.
        index = pd.to_datetime([stamp.to_pydatetime().timestamp() for stamp in index], unit="s", utc=True)
    raw.index = index.tz_convert("Asia/Kuala_Lumpur").normalize().tz_localize(None)
    return engine._extract_price_frame(raw, symbol)


def fetch_frame(symbol, exchange, bars, cutoff):
    bars = min(300, max(1, bars))
    for attempt in range(3):
        try:
            engine._pace_tradingview_connection()
            client, interval = engine._tv_client(reset=attempt > 0)
            raw = client.get_hist(symbol=symbol, exchange=exchange, interval=interval.in_daily,
                                  n_bars=bars, extended_session=False)
            frame = normalize_daily(raw, symbol)
            if frame is not None:
                frame = frame.apply(pd.to_numeric, errors="coerce")
                frame = frame.loc[frame.index <= cutoff].dropna()
                valid = ((frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
                         & (frame.Volume >= 0) & (frame.High >= frame[["Open", "Close", "Low"]].max(axis=1))
                         & (frame.Low <= frame[["Open", "Close", "High"]].min(axis=1)))
                if not valid.all():
                    return None, "invalid_ohlcv"
                if not frame.empty:
                    return frame.tail(bars), None
        except Exception:
            pass
        if attempt < 2:
            time.sleep(1 + attempt)
    return None, "download_failed"


def collect(bars=300, progress=None):
    bars = min(300, max(221, bars))
    universe, exclusions, discovered = discover(details=True)
    if len(universe) < 900:
        raise RuntimeError(f"unexpected_universe_size:{len(universe)}; refusing_unverified_snapshot")
    cutoff = completed_cutoff()
    benchmark, error = fetch_frame("FBMKLCI", "FTSEMYX", bars, cutoff)
    if benchmark is None or len(benchmark) < 221:
        raise RuntimeError(f"benchmark_unavailable_or_insufficient:{error}")
    cutoff = benchmark.index[-1]
    if (completed_cutoff() - cutoff).days > 7:
        raise RuntimeError("benchmark_stale_over_seven_days")
    frames, issues, processed = {}, list(exclusions), 0
    if progress:
        progress(0, len(universe))
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_frame, s.tv_symbol, "MYX", bars, cutoff): s for s in universe}
        for future in as_completed(futures):
            security = futures[future]
            frame, issue = future.result()
            processed += 1
            if frame is not None:
                frames[security.symbol] = frame
                if frame.index[-1] < cutoff:
                    issue = "stale"
                elif len(frame) < 220:
                    issue = "insufficient_history"
            if issue:
                issues.append({"symbol": security.symbol, "name": security.name, "reason": issue,
                    "bars": len(frame) if frame is not None else 0})
            if processed % 50 == 0 or processed == len(universe):
                print(f"Astra {processed}/{len(universe)}; downloaded={len(frames)}", flush=True)
                if progress:
                    progress(processed, len(universe))
    metadata = {s.symbol: s for s in universe}
    digest = hashlib.sha256()
    digest.update(json.dumps([vars(s) for s in universe], sort_keys=True).encode())
    digest.update(benchmark.Close.to_csv().encode())
    for symbol, frame in sorted(frames.items()):
        digest.update(symbol.encode())
        digest.update(frame.to_csv().encode())
    fresh = sum(len(f) >= 220 and f.index[-1] == cutoff for f in frames.values())
    coverage = {"discovered": discovered, "processed": processed + len(exclusions), "downloaded": len(frames),
        "ordinary_shares": len(universe), "attempted": processed, "excluded": len(exclusions),
        "fresh_with_history": fresh, "failed": len(universe) - len(frames),
        "stale": sum(i["reason"] == "stale" for i in issues),
        "insufficient_history": sum(i["reason"] == "insufficient_history" for i in issues),
        "requested_bars": bars, "partial": fresh != len(universe), "issues": issues,
        "data_hash": digest.hexdigest(), "instrument_filter": "TradingView MYX type=stock"}
    return frames, metadata, benchmark.Close, coverage
