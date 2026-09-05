# Astra research tab

Astra adds two fixed-rule research simulations. The header RUN and daily workflow
collect one TradingView Bursa snapshot shared by Quant, Wizard and Astra.
Existing Quant scoring and portfolio rules are preserved. No real orders are submitted.

## Deployment

1. Review and merge the Astra pull request into `main`; the existing Cloudflare
   Pages integration builds with the existing `npm run build` command.
2. Keep the existing Cloudflare `DB`, `GITHUB_TOKEN`, and `PUBLISH_TOKEN` bindings.
   Keep GitHub Actions secrets `QUANT_API_BASE` and `PUBLISH_TOKEN` aligned with
   the production site. Do not put their values in repository files.
3. The first authenticated Astra run creates `astra_job` and `astra_reports`
   using additive `CREATE TABLE IF NOT EXISTS` statements. No existing table is
   altered. The manual trigger creates the same tables before claiming a job.
4. Open **Astra**, set capital, risk and cost assumptions, then choose the header **RUN**. It updates all models from the same snapshot.
   On phones, Today, Rankings, Astra and Portfolio remain visible; More opens
   the other five sections.
5. One daily workflow runs at 19:45 Malaysia time on weekdays. Scheduled runs
   use defaults; custom PWA settings apply to the requested run only. The legacy
   Astra workflow delegates to the shared workflow and has no separate schedule.
   A server job lock and GitHub concurrency group prevent overlapping scans.

The workflow must exist on the repository's default branch before GitHub exposes
its manual dispatch. New Cloudflare bindings or a separate hosting project are
not required. Existing optional `GITHUB_OWNER`, `GITHUB_REPOSITORY`, and
`GITHUB_REF` settings are respected; the shared workflow filename is fixed to `daily-quant.yml`.

## Data coverage

- Paginate TradingView's MYX `type=stock` universe to its reported total count;
  sort by ticker and detect missing/repeated pages or ambiguous ordinary codes.
- Read TradingView `typespecs` and retain ordinary (`common`) shares. Preferred
  shares are recorded explicitly as exclusions, preventing common/preferred
  instruments with similar ISINs from collapsing into one numeric code.
- Live discovery verified on 2026-09-05: **1,129 stock listings, 1,127 ordinary
  shares, two preferred-share exclusions**. This is an observation, not a fixed
  expected count.
- Attempt all ordinary shares, with three concurrent workers and up to three
  attempts. No liquidity or market-cap shortlist limits history collection.
- Request at most 300 daily bars per stock and benchmark; clamp imported and
  returned histories to 300 as well. Display actual returned history.
  Convert tvdatafeed's host-local timestamps to MYT before extracting session
  dates; the same bar must retain the same market date on UTC and US hosts.
  Before 18:00 MYT, discard the current date. Use the benchmark's last available
  completed session; refuse a benchmark more than seven calendar days behind.
- Distinguish discovery, attempted downloads, exclusions, missing histories,
  stale bars, short histories, liquidity eligibility and strategy signals.
- Retain stale/short series for historical periods where data exist, but do not
  generate current signals without a current bar and enough warm-up history.
- A partial scan can publish with an explicit partial-coverage label. No usable
  stock histories or an unavailable benchmark fails without replacing results.

## Shared strategy rules

Daily close above SMA50 above SMA200; SMA200 above its value 20 sessions ago.
Rank the 126-session return independently on each historical date among stocks
with sufficient data and at least RM1 million median traded value over the
preceding 60 sessions. Require relative-strength percentile >=80. All qualifying
signals are displayed; portfolio entries are sorted by momentum, then liquidity,
then stock code. No arbitrary aggregate quant score is introduced.

- **Breakout:** close above the high of the preceding 55 sessions.
- **Pullback:** close crosses above SMA20 after yesterday closed at or below
  SMA20, while the shared longer-term trend filter remains true.
- Optional breadth gate: more than 50% of sufficiently seasoned current-universe
  stocks above SMA200 on that historical date.

## Portfolio and execution assumptions

Defaults: RM100,000 capital, 0.5% planned risk, eight positions, 10% position cap,
25% sector cap, 1% trading participation cap. Strategies use independent capital;
their returns cannot be added together as one portfolio.

Signals at date t can fill at the next benchmark session's open, with adverse
slippage and conservative Bursa tick rounding, in 100-share lots. Missing,
zero-volume or zero-range bars do not fill. Entry size is constrained by cash,
estimated round-trip fees, stop distance, sector value and both trailing median
turnover and actual daily volume. Daily volume is an **ex-post fill-capacity
approximation**, not information used to select/rank the signal. Actual opening
auction participation is not known.

Initial stop = entry minus 2.5 ATR14. ATR uses Wilder-style exponential smoothing.
At each close, raise tomorrow's stop to the greater of the existing stop and
highest close since entry minus 3 ATR. Never lower a stop. There is no fixed
profit target, leverage or averaging down.

Opening gaps execute at the available opening price plus adverse slippage, not
at the stop. Intraday exits use stops known before that session. Partial exits
retain an exit-pending flag and continue on the next available liquid session.
Cash from intraday exits is not used retroactively for opening entries. Open
positions remain marked to market; they are not forced into the closed ledger.

Costs default to an estimated combined **20 bps per side, minimum RM8**, plus
10 bps slippage per side. These are explicit research assumptions, not current
brokerage, clearing-fee or stamp-duty schedules. Enter suitable estimates before
relying on a result. The stress scenario doubles all three cost inputs.

## Backtest interpretation

Use 220 benchmark bars as indicator warm-up. Show the full available period,
an independent fresh-cash simulation over its final 30%, and a full-period
double-cost scenario. Rules are fixed, with no automated parameter search.
The final-period result is not claimed to be untouched prospective validation.
With 300 bars, warm-up leaves at most 80 backtest sessions. Evidence diagnostics
flag short samples, fewer than 30 closed trades, non-positive expectancy, weak
final-period or stressed returns and benchmark underperformance. These checks
never promote a strategy to validated status. They diagnose weaknesses; they do
not constitute a new or empirically improved trading strategy.

Metrics include equity, price-return KLCI comparison, CAGR only for periods of at
least one year, maximum drawdown, underwater duration, exposure, net expectancy
in initial risk units (including estimated initial round-trip costs), win rate,
profit factor, winning/losing R, annual/partial-year returns and losing streaks.
Absent trade statistics are null, not invented zero-performance evidence.

The UI displays the latest 100 closed trades and downloads the complete CSV.
It also exposes open positions and stock-level coverage exceptions. The dataset
fingerprint, model version and exact run configuration accompany every report.

## Material research limitations

The historical universe contains today's listings. Delisted stocks, historical
PN17/GN3 membership and historical sector changes are not available. PN17/GN3
exclusions are therefore **not applied**. TradingView corporate-action settings
are not independently reconciled, and cash dividends are not included. These are
provisional price-return diagnostics with survivorship and corporate-action risk,
not production-validated expected returns.

Daily OHLCV does not establish order-book depth, queue priority, spread or exact
intraday execution order. Zero-range deferral is a conservative heuristic, not
a complete exchange limit-state model. Stale position marks can understate losses.
A displayed stop is not a broker order and cannot cap gap/suspension losses.

## Reproducing a run

Each workflow uploads `latest.json`, both trade CSVs and a `source/` directory
containing the requested histories, benchmark, universe and coverage metadata.
Artifacts are retained for seven days; download them for longer retention. D1
keeps the latest three Astra reports on publication. Raw OHLCV is stored in
workflow artifacts, not D1. Previously uploaded artifacts retain their original
expiry dates. This version requests histories afresh; it does not claim a
durable incremental bar archive or ten years of historical coverage.

```bash
python scripts/run_astra.py --input /path/to/source --output artifacts/astra-replay
```

`ASTRA_CONFIG` accepts a JSON configuration matching the `Config` dataclass.
The script runs without publishing unless `--publish` is supplied. Live runs use
the existing TradingView client and its request pacing. Production uses
`python scripts/run_shared.py --publish`; the standalone Astra script remains
available for offline replay. All paths cap history at 300 bars.

## Verification

Run the repository's existing Node and Python test commands and build. Astra
tests cover pagination beyond 1,000 stocks, look-ahead resistance, next-session
entry, opening gaps, deferred/partial stops, cash reconciliation, position sizing,
fees, date cutoffs, API authentication, atomic job claiming and immutable reports.

Strategy research references: [equity momentum](https://www.bauer.uh.edu/rsusmel/phd/jegadeesh-titman93.pdf),
[trend following](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing),
[selection bias](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
These references motivate research; they do not validate the selected Bursa rules.
