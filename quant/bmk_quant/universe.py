from __future__ import annotations

import functools
from dataclasses import dataclass

import requests

TV_SCAN_URL = "https://scanner.tradingview.com/malaysia/scan"


@dataclass(frozen=True)
class Security:
    symbol: str
    name: str
    sector: str
    market_cap: float


@functools.lru_cache(maxsize=1)
def get_universe() -> tuple[Security, ...]:
    """Fetch Bursa equities once per run and return stable, deduplicated metadata."""
    columns = ["name", "description", "sector", "market_cap_basic", "type"]
    request = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": "MYX"},
            {"left": "type", "operation": "equal", "right": "stock"},
        ],
        "options": {"lang": "en"},
        "markets": ["malaysia"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": columns,
        "sort": {"sortBy": "name", "sortOrder": "asc"},
        "range": [0, 2999],
    }
    response = requests.post(TV_SCAN_URL, json=request, timeout=40)
    response.raise_for_status()
    securities: dict[str, Security] = {}
    for item in response.json().get("data", []):
        values = dict(zip(columns, item.get("d", []), strict=False))
        symbol = str(values.get("name") or item.get("s", "").split(":")[-1]).strip().upper()
        if not symbol or symbol in securities:
            continue
        securities[symbol] = Security(
            symbol=symbol,
            name=str(values.get("description") or symbol).strip(),
            sector=str(values.get("sector") or "Unclassified").strip(),
            market_cap=float(values.get("market_cap_basic") or 0),
        )
    return tuple(securities.values())
