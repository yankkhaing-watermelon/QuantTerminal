CREATE TABLE IF NOT EXISTS quant_runs (
  run_id TEXT PRIMARY KEY,
  scan_date TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The application intentionally does not create a scan_date index here because
-- this migration is also used against legacy D1 databases whose schema may be
-- reconciled at runtime. Sorting is handled by the API query itself.

CREATE TABLE IF NOT EXISTS research_archives (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  expected_symbols INTEGER NOT NULL DEFAULT 0,
  received_symbols INTEGER NOT NULL DEFAULT 0,
  payload_hash TEXT,
  manifest_hash TEXT,
  archived_at TEXT,
  schema_version INTEGER NOT NULL DEFAULT 2,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS research_rows (
  run_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  row_hash TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS manual_run_requests (
  request_id TEXT PRIMARY KEY,
  requested_at_epoch INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued'
);
CREATE INDEX IF NOT EXISTS idx_portfolio_rows_run ON portfolio_rows(run_id);
