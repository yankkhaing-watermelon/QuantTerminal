# Bursa MusangKing Quant Terminal v5

A Cloudflare-native quantitative research terminal for the full Bursa Malaysia universe. This repository is the clean rebuild of phases 1–15 and is designed for GitHub-connected Cloudflare Pages—no Wrangler deployment is required.

## What is included

| Phase | Capability |
|---|---|
| 1 | Bursa universe discovery and metadata |
| 2 | Consistent unadjusted daily-price ingestion |
| 3 | Trend, momentum, relative strength, volatility and liquidity features |
| 4 | Cross-sectional Quant Score and daily rank |
| 5 | Expected-edge and confidence model |
| 6 | ADD / HOLD / WATCH / TRIM / REDUCE / EXIT overlay |
| 7 | Volatility-aware position sizing and portfolio risk |
| 8 | Trade/performance payload contract |
| 9 | Live performance and equity-curve view |
| 10 | Responsive Bloomberg-style PWA terminal |
| 11 | Breadth & Market Regime v2 (six-factor, five-state) |
| 12 | Walk-forward grouped backtest |
| 13 | Immutable per-run research archive |
| 14 | Neutral Unexplained Activity monitor |
| 15 | Fail-closed publication, deterministic integrity checks and CI |

### Step 10 portfolio safeguards

- Regime exposure caps: 100% Strong Risk-On, 85% Risk-On, 70% Neutral, 45% Risk-Off and 25% Strong Risk-Off.
- Maximum target weight of 15% per security.
- Expected-edge, volatility and decision-aware allocation.
- Three-ATR stop validation, target/position parity and per-position risk-contribution reconciliation.
- Portfolio beta, cash reserve, effective positions and diversification are independently recalculated before publication.
- `Validate Portfolio Risk and Position Sizing` checks the current live payload without modifying D1.

## Reliability rules

- Trading inputs and `^KLSE` use `auto_adjust=False`; adjusted and raw histories are never mixed.
- A security is rejected when its last bar is older than the benchmark's last completed session.
- A scan fails closed when the Bursa universe is below 900 or fresh price coverage is insufficient.
- The universe request is memoized for the whole run.
- Research and portfolio writes are capped at 100 rows per D1 batch, avoiding the D1 SQL-variable ceiling.
- The publisher no longer sends Python-generated floating-point row hashes. Cloudflare hashes the received canonical JSON, eliminating Python/JavaScript rounding-boundary mismatches.
- Unexplained Activity is neutral anomaly detection only; it does not claim to identify leaked information or insider trading.

## Cloudflare Dashboard setup (no Wrangler)

### 1. Connect the new repository

In **Workers & Pages → your existing `bursamusangking-quant-terminal` Pages project → Settings → Builds & deployments**, disconnect the inaccessible suspended repository and connect:

`yankkhaing-watermelon/QuantTerminal`

Use these build settings:

- Production branch: `main`
- Framework preset: `Vite`
- Build command: `npm run build`
- Build output directory: `dist`
- Root directory: `/`

### 2. Bind the existing D1 database

In **Settings → Bindings → Add → D1 database**:

- Variable name: `DB`
- Database: select the existing Quant Terminal database

Apply [migrations/0001_initial.sql](migrations/0001_initial.sql) in **D1 → Console**. The API also creates missing v5 tables safely on first request, so existing tables and data are not deleted.

### 3. Add the publication secret

In **Settings → Variables and Secrets**, add an encrypted secret named `PUBLISH_TOKEN` with a long random value.

In the GitHub repository, open **Settings → Secrets and variables → Actions** and add:

- Secret `PUBLISH_TOKEN`: exactly the same value as Cloudflare
- Secret `QUANT_API_BASE`: your production Pages URL, for example `https://bursamusangking-quant-terminal.pages.dev`
- Optional repository variable `PORTFOLIO_SYMBOLS`: comma-separated symbols such as `YINSON,IHH`

The secret protects write endpoints only. The public terminal never contains it.

### 4. Deploy and publish

Cloudflare deploys automatically after a push to `main`. When deployment is green:

1. Open `/api/health` and confirm `{"ok":true,...}`.
2. In GitHub **Actions → Daily Quant Publication → Run workflow**, leave `max_symbols` as `0` for the full Bursa universe.
3. Open `/api/latest`; after publication it returns the v5 payload.

The weekday schedule is 11:45 UTC / 7:45 PM Malaysia time. GitHub schedules can start a few minutes late.

## Local validation

```bash
npm ci
npm test
npm run build
pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py'
```

Run a development-only limited scan:

```bash
python scripts/run_quant.py --max-symbols 30
```

Full scan and publication:

```bash
python scripts/run_quant.py
QUANT_API_BASE=https://your-project.pages.dev PUBLISH_TOKEN=... python scripts/publish.py
```

## API

- `GET /api/health`
- `GET /api/latest`
- `GET /api/history?limit=60`
- `GET /api/research/:runId`
- `POST /api/admin/publish`
- `POST /api/admin/runs/:runId/research/start`
- `POST /api/admin/runs/:runId/research/batch`
- `POST /api/admin/runs/:runId/research/commit`
- `POST /api/admin/runs/:runId/portfolio`

Quant rankings are research outputs, not personalized investment advice, and do not guarantee outperformance or profitability.
