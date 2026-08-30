from __future__ import annotations

import math

from quant.bmk_quant.engine import MIN_BARS, MIN_UNIVERSE, _benchmark, _download_prices
from quant.bmk_quant.universe import get_universe


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
    prices = _download_prices(universe)

    fresh = 0
    stale = 0
    latest_dates: dict[str, int] = {}
    for frame in prices.values():
        last_session = frame.index[-1].normalize()
        latest_dates[last_session.date().isoformat()] = latest_dates.get(last_session.date().isoformat(), 0) + 1
        if last_session < completed_session:
            stale += 1
        else:
            fresh += 1

    unusable = total - len(prices)
    coverage = fresh / total * 100
    expected_fresh = max(MIN_UNIVERSE, math.ceil(total * 0.80))

    print("=== Quant Terminal Full-Universe Preflight ===")
    print(f"Universe discovered: {total}")
    print(f"Numeric Bursa codes: {len(numeric)}")
    print(f"Non-numeric fallback symbols: {len(non_numeric)}")
    if non_numeric:
        print("Non-numeric examples:", ", ".join(non_numeric[:20]))
    print(f"Duplicate codes after dedupe: {duplicate_codes}")
    print(f"KLCI completed session: {completed_session.date().isoformat()}")
    print(f"Usable price frames (>= {MIN_BARS} bars): {len(prices)}")
    print(f"Unusable / unavailable symbols: {unusable}")
    print(f"Stale latest bar: {stale}")
    print(f"Fresh valid symbols: {fresh}")
    print(f"Fresh coverage: {coverage:.1f}%")
    print(f"Required production fresh coverage for current engine: >= {expected_fresh}")
    print("Latest-bar distribution:")
    for date, count in sorted(latest_dates.items(), key=lambda item: item[0], reverse=True)[:10]:
        print(f"  {date}: {count}")

    if total < MIN_UNIVERSE:
        print(f"RESULT: FAIL — discovered Bursa universe is below MIN_UNIVERSE={MIN_UNIVERSE}")
        return 1
    if fresh < MIN_UNIVERSE:
        print("RESULT: FAIL — current production engine would reject this run")
        return 1
    if len(numeric) / total < 0.98:
        print("RESULT: WARN — more than 2% of the universe lacks a numeric Bursa code")
    if stale or unusable:
        print("RESULT: PASS WITH DATA-QUALITY EXCLUSIONS — enough fresh symbols for production")
    else:
        print("RESULT: PASS — full universe is clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
