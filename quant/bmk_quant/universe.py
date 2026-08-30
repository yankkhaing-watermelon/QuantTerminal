from __future__ import annotations

import functools
import re
from dataclasses import dataclass

import requests

TV_SCAN_URL = "https://scanner.tradingview.com/malaysia/scan"
_ISIN_CODE = re.compile(r"^MY[A-Z](\d{4,5})")


@dataclass(frozen=True)
class Security:
    # `symbol` is the Bursa/Yahoo-compatible stock code (for example 7054,
    # 6599, 0286), not the TradingView short ticker (AASIA, AEON, EMCC).
    symbol: str
    name: str
    sector: str
    market_cap: float
    tv_symbol: str = ""


def _stock_code_from_isin(isin: str) -> str:
    """Extract the Bursa numeric stock code from a Malaysian equity ISIN.

    Examples: MYL7054OO009 -> 7054, MYQ0328OO003 -> 0328,
    MYQ03032O009 -> 03032. Bursa/TradingView short names are not
    Yahoo-compatible for many Malaysian listings, so the ISIN is the
    stable bridge between the two identifiers.
    """
    match = _ISIN_CODE.match(str(isin or "").strip().upper())
    return match.group(1) if match else ""


@functools.lru_cache(maxsize=1)
def get_universe() -> tuple[Security, ...]:
    """Fetch Bursa equities and return stable, deduplicated metadata.

    TradingView exposes the Bursa short ticker (for example AASIA/AEON),
    while Yahoo Finance's Malaysia historical endpoint generally expects the
    numeric Bursa code (7054.KL/6599.KL). Use the ISIN supplied by the scan
    to build the Yahoo-compatible symbol and retain the TradingView ticker
    separately for display/debugging.
    """
    columns = ["name", "description", "sector", "market_cap_basic", "type", "isin"]
    request = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": "MYX"},
            {"left": "type", "operation": "equal", "right": "stock"},
        ],
        "options": {"lang": "en"},
        "markets": ["malaysia"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": columns,
        # Market-cap ordering makes the controlled MAX_SYMBOLS test use
        # established, liquid listings rather than alphabetically selected
        # newly listed stocks that may not yet have 220 daily bars.
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 2999],
    }
    response = requests.post(TV_SCAN_URL, json=request, timeout=40)
    response.raise_for_status()
    securities: dict[str, Security] = {}
    for item in response.json().get("data", []):
        values = dict(zip(columns, item.get("d", []), strict=False))
        tv_symbol = str(values.get("name") or item.get("s", "").split(":")[-1]).strip().upper()
        stock_code = _stock_code_from_isin(str(values.get("isin") or ""))
        # Some non-standard Bursa instruments may not expose a numeric ISIN
        # code. Keep their TradingView ticker as a fallback; the data layer can
        # report them as unavailable rather than silently inventing a symbol.
        symbol = stock_code or tv_symbol
        if not symbol or symbol in securities:
            continue
        securities[symbol] = Security(
            symbol=symbol,
            name=str(values.get("description") or tv_symbol or symbol).strip(),
            sector=str(values.get("sector") or "Unclassified").strip(),
            market_cap=float(values.get("market_cap_basic") or 0),
            tv_symbol=tv_symbol,
        )
    return tuple(securities.values())
