CREATE TABLE IF NOT EXISTS quant_runs (
  run_id TEXT PRIMARY KEY,
  scan_date TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_quant_runs_date ON quant_runs(scan_date DESC, generated_at DESC);

CREATE TABLE IF NOT EXISTS research_archives (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  expected_symbols INTEGER NOT NULL DEFAULT 0,
  received_symbols INTEGER NOT NULL DEFAULT 0,
  payload_hash TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS research_rows (
  run_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  row_json TEXT NOT NULL,
  PRIMARY KEY (run_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_research_rows_run ON research_rows(run_id);

CREATE TABLE IF NOT EXISTS portfolio_rows (
  run_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  row_hash TEXT NOT NULL,
  row_json TEXT NOT NULL,
  PRIMARY KEY (run_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_rows_run ON portfolio_rows(run_id);
