const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
});

const encoder = new TextEncoder();
const PRODUCTION_API_BASE = "https://bursamusangking-quant-terminal.pages.dev";

async function sha256(value) {
  const bytes = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function secureEqual(left, right) {
  if (!left || !right) return false;
  const [a, b] = await Promise.all([sha256(left), sha256(right)]);
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) diff |= a.charCodeAt(i % a.length) ^ b.charCodeAt(i % b.length);
  return diff === 0;
}

async function authorized(request, env) {
  if (!env.PUBLISH_TOKEN) return false;
  const bearer = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "") || "";
  const token = bearer || request.headers.get("x-publish-token") || "";
  return secureEqual(token, env.PUBLISH_TOKEN);
}

async function manualRunAuthorized(request, env) {
  if (!env.MANUAL_RUN_KEY) return false;
  return secureEqual(request.headers.get("x-manual-run-key") || "", env.MANUAL_RUN_KEY);
}

function workflowDispatchUrl(env) {
  const owner = env.GITHUB_OWNER || "yankkhaing-watermelon";
  const repository = env.GITHUB_REPOSITORY || "QuantTerminal";
  const workflow = env.GITHUB_WORKFLOW || "daily-quant.yml";
  return `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`;
}

async function tableColumns(db, table) {
  const result = await db.prepare(`PRAGMA table_info(${table})`).all();
  return new Set((result.results || []).map((row) => String(row.name)));
}

async function addMissingColumns(db, table, definitions) {
  const columns = await tableColumns(db, table);
  for (const [column, definition] of definitions) {
    if (!columns.has(column)) {
      await db.prepare(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`).run();
    }
  }
  return await tableColumns(db, table);
}

async function ensureSchema(db) {
  await db.prepare("CREATE TABLE IF NOT EXISTS quant_runs (run_id TEXT PRIMARY KEY, scan_date TEXT NOT NULL DEFAULT '', generated_at TEXT NOT NULL DEFAULT '', payload_hash TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT '')").run();
  await db.prepare("CREATE TABLE IF NOT EXISTS research_archives (run_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'started', expected_symbols INTEGER NOT NULL DEFAULT 0, received_symbols INTEGER NOT NULL DEFAULT 0, payload_hash TEXT, updated_at TEXT NOT NULL DEFAULT '')").run();
  await db.prepare("CREATE TABLE IF NOT EXISTS research_rows (run_id TEXT NOT NULL, symbol TEXT NOT NULL, row_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (run_id, symbol))").run();
  await db.prepare("CREATE TABLE IF NOT EXISTS portfolio_rows (run_id TEXT NOT NULL, symbol TEXT NOT NULL, row_hash TEXT NOT NULL DEFAULT '', row_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (run_id, symbol))").run();
  await db.prepare("CREATE TABLE IF NOT EXISTS manual_run_requests (request_id TEXT PRIMARY KEY, requested_at_epoch INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued')").run();

  // The connected production D1 has a legacy quant_runs table whose primary
  // key is named `id` rather than `run_id`. ALTER TABLE cannot add a new
  // primary key, so add a nullable run_id and enforce uniqueness separately.
  // Existing legacy rows remain intact; new publications use run_id.
  const quantColumns = await addMissingColumns(db, "quant_runs", [
    ["run_id", "TEXT"],
    ["scan_date", "TEXT NOT NULL DEFAULT ''"],
    ["generated_at", "TEXT NOT NULL DEFAULT ''"],
    ["payload_hash", "TEXT NOT NULL DEFAULT ''"],
    ["payload_json", "TEXT NOT NULL DEFAULT '{}'"],
    ["created_at", "TEXT NOT NULL DEFAULT ''"],
  ]);

  if (quantColumns.has("date")) {
    await db.prepare("UPDATE quant_runs SET scan_date = date WHERE (scan_date IS NULL OR scan_date = '') AND date IS NOT NULL").run();
  } else if (quantColumns.has("run_date")) {
    await db.prepare("UPDATE quant_runs SET scan_date = run_date WHERE (scan_date IS NULL OR scan_date = '') AND run_date IS NOT NULL").run();
  }
  if (quantColumns.has("timestamp")) {
    await db.prepare("UPDATE quant_runs SET generated_at = timestamp WHERE (generated_at IS NULL OR generated_at = '') AND timestamp IS NOT NULL").run();
  }
  if (quantColumns.has("created_at")) {
    await db.prepare("UPDATE quant_runs SET generated_at = created_at WHERE (generated_at IS NULL OR generated_at = '') AND created_at IS NOT NULL").run();
  }

  // A legacy table may have no run_id constraint. This unique index makes
  // the UPSERT target valid while allowing all pre-existing NULL run_ids.
  if (quantColumns.has("run_id")) {
    await db.prepare("CREATE UNIQUE INDEX IF NOT EXISTS idx_quant_runs_run_id ON quant_runs(run_id)").run();
  }

  await addMissingColumns(db, "research_archives", [
    ["status", "TEXT NOT NULL DEFAULT 'started'"],
    ["expected_symbols", "INTEGER NOT NULL DEFAULT 0"],
    ["received_symbols", "INTEGER NOT NULL DEFAULT 0"],
    ["payload_hash", "TEXT"],
    ["updated_at", "TEXT NOT NULL DEFAULT ''"],
  ]);
  await addMissingColumns(db, "research_rows", [["row_json", "TEXT NOT NULL DEFAULT '{}'" ]]);
  await addMissingColumns(db, "portfolio_rows", [["row_hash", "TEXT NOT NULL DEFAULT ''"], ["row_json", "TEXT NOT NULL DEFAULT '{}'" ]]);

  if ((await tableColumns(db, "research_rows")).has("run_id")) {
    await db.prepare("CREATE INDEX IF NOT EXISTS idx_research_rows_run ON research_rows(run_id)").run();
  }
  if ((await tableColumns(db, "portfolio_rows")).has("run_id")) {
    await db.prepare("CREATE INDEX IF NOT EXISTS idx_portfolio_rows_run ON portfolio_rows(run_id)").run();
  }
}

async function handleManualRun(request, env) {
  if (!(await manualRunAuthorized(request, env))) return json({ ok: false, error: "invalid_manual_run_key" }, 401);
  if (!env.GITHUB_TOKEN) return json({ ok: false, error: "github_token_not_configured" }, 503);

  const db = getDb(env);
  await ensureSchema(db);
  const now = Math.floor(Date.now() / 1000);
  const cooldown = Math.max(60, Number(env.RUN_COOLDOWN_SECONDS || 300));
  const last = await db.prepare("SELECT requested_at_epoch FROM manual_run_requests ORDER BY requested_at_epoch DESC LIMIT 1").first();
  const elapsed = now - Number(last?.requested_at_epoch || 0);
  if (last && elapsed < cooldown) {
    return json({ ok: false, error: "run_cooldown", retry_after: cooldown - elapsed }, 409);
  }

  const response = await fetch(workflowDispatchUrl(env), {
    method: "POST",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "content-type": "application/json",
      "user-agent": "bursa-musangking-quant-terminal",
      "x-github-api-version": "2022-11-28",
    },
    body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs: { max_symbols: "0" } }),
  });
  if (!response.ok) return json({ ok: false, error: "github_dispatch_failed", github_status: response.status }, 502);

  const requestId = crypto.randomUUID();
  await db.prepare("INSERT INTO manual_run_requests(request_id, requested_at_epoch, status) VALUES(?, ?, 'queued')")
    .bind(requestId, now).run();
  return json({ ok: true, state: "queued", request_id: requestId, poll_seconds: 15 }, 202);
}

function getDb(env) {
  if (!env.DB) throw new Error("D1 binding DB is not configured");
  return env.DB;
}

async function bodyJson(request) {
  try { return await request.json(); } catch { throw new Error("invalid_json"); }
}

async function latest(db) {
  const columns = await tableColumns(db, "quant_runs");
  if (!columns.has("payload_json")) return null;
  const order = columns.has("scan_date") && columns.has("generated_at")
    ? "scan_date DESC, generated_at DESC"
    : columns.has("created_at") ? "created_at DESC" : "rowid DESC";
  const result = await db.prepare(`SELECT payload_json FROM quant_runs ORDER BY ${order} LIMIT 25`).all();
  for (const row of (result.results || [])) {
    if (!row?.payload_json) continue;
    try {
      const parsed = JSON.parse(row.payload_json);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && Object.keys(parsed).length > 0) return parsed;
    } catch {
      // Ignore malformed legacy payloads and continue to the next run.
    }
  }
  return null;
}

async function handleRead(path, url, env) {
  // Cloudflare branch previews do not inherit the production D1 binding.
  // Proxy only the public latest read so visual reviews use real published data.
  if (!env.DB && path === "/api/latest" && url.hostname !== "bursamusangking-quant-terminal.pages.dev") {
    return fetch(`${PRODUCTION_API_BASE}/api/latest`, { headers: { accept: "application/json" } });
  }
  const db = getDb(env);

  if (path === "/api/health") {
    const table = await db.prepare("SELECT type FROM sqlite_master WHERE name='quant_runs' LIMIT 1").first();
    let columns = [];
    if (table?.type) {
      const result = await db.prepare("PRAGMA table_info(quant_runs)").all();
      columns = (result.results || []).map((row) => String(row.name));
    }
    return json({ ok: true, service: "bursa-musangking-quant-terminal", version: "5.0.6", db_bound: true, quant_runs_type: table?.type || null, quant_runs_columns: columns });
  }

  await ensureSchema(db);
  if (path === "/api/latest") {
    const payload = await latest(db);
    return payload ? json({ ok: true, data: payload }) : json({ ok: true, data: null, state: "awaiting_first_publication" });
  }
  if (path === "/api/history") {
    const columns = await tableColumns(db, "quant_runs");
    const order = columns.has("scan_date") && columns.has("generated_at") ? "scan_date DESC, generated_at DESC" : "rowid DESC";
    const select = ["run_id", ...(columns.has("scan_date") ? ["scan_date"] : []), ...(columns.has("generated_at") ? ["generated_at"] : []), ...(columns.has("payload_hash") ? ["payload_hash"] : [])].join(", ");
    const limit = Math.min(180, Math.max(1, Number(url.searchParams.get("limit") || 60)));
    const result = await db.prepare(`SELECT ${select} FROM quant_runs ORDER BY ${order} LIMIT ?`).bind(limit).all();
    return json({ ok: true, data: result.results || [] });
  }
  const research = path.match(/^\/api\/research\/([^/]+)$/);
  if (research) {
    const result = await db.prepare("SELECT symbol, row_json FROM research_rows WHERE run_id = ? ORDER BY symbol").bind(decodeURIComponent(research[1])).all();
    return json({ ok: true, data: (result.results || []).map((row) => JSON.parse(row.row_json)) });
  }
  return json({ ok: false, error: "not_found" }, 404);
}

async function publishQuantRun(db, run, payloadHash) {
  const info = await db.prepare("PRAGMA table_info(quant_runs)").all();
  const idInfo = (info.results || []).find((row) => String(row.name) === "id");
  const idType = String(idInfo?.type || "").toUpperCase();
  const idRequired = Boolean(idInfo && Number(idInfo.notnull) === 1 && idInfo.dflt_value == null);

  // Legacy D1 has a required `id` column with no default. A normal UPSERT
  // cannot reach its run_id conflict target because SQLite checks NOT NULL
  // constraints on the proposed INSERT first. Supply a compatible legacy id.
  if (idRequired && idType.includes("INT")) {
    // Keep id generation in the same INSERT statement. SQLite aggregate
    // queries without GROUP BY return one row even when the table is empty.
    // WHERE true also removes the SQLite INSERT...SELECT/UPSERT parsing
    // ambiguity documented by SQLite.
    await db.prepare("INSERT INTO quant_runs(id, run_id, scan_date, generated_at, payload_hash, payload_json) SELECT COALESCE(MAX(id), 0) + 1, ?, ?, ?, ?, ? FROM quant_runs WHERE true ON CONFLICT(run_id) DO UPDATE SET scan_date=excluded.scan_date, generated_at=excluded.generated_at, payload_hash=excluded.payload_hash, payload_json=excluded.payload_json")
      .bind(run.run_id, run.scan_date, run.generated_at, payloadHash, JSON.stringify(run)).run();
    return;
  }

  if (idRequired) {
    const legacyId = `${run.run_id}-${Date.now()}-${crypto.randomUUID()}`;
    await db.prepare("INSERT INTO quant_runs(id, run_id, scan_date, generated_at, payload_hash, payload_json) VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET scan_date=excluded.scan_date, generated_at=excluded.generated_at, payload_hash=excluded.payload_hash, payload_json=excluded.payload_json")
      .bind(legacyId, run.run_id, run.scan_date, run.generated_at, payloadHash, JSON.stringify(run)).run();
    return;
  }

  await db.prepare("INSERT INTO quant_runs(run_id, scan_date, generated_at, payload_hash, payload_json) VALUES(?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET scan_date=excluded.scan_date, generated_at=excluded.generated_at, payload_hash=excluded.payload_hash, payload_json=excluded.payload_json")
    .bind(run.run_id, run.scan_date, run.generated_at, payloadHash, JSON.stringify(run)).run();
}

async function handleWrite(request, path, env) {
  if (!(await authorized(request, env))) return json({ ok: false, error: "unauthorized" }, 401);
  const db = getDb(env);
  await ensureSchema(db);
  const payload = await bodyJson(request);

  if (path === "/api/admin/publish") {
    const run = payload.data || payload;
    if (!run.run_id || !run.scan_date || !run.generated_at) return json({ ok: false, error: "missing_run_identity" }, 422);
    const serialized = stable(run);
    const payloadHash = await sha256(serialized);
    if (payload.payload_hash && payload.payload_hash !== payloadHash) return json({ ok: false, error: "payload_hash_mismatch" }, 422);
    await publishQuantRun(db, run, payloadHash);
    return json({ ok: true, run_id: run.run_id, payload_hash: payloadHash });
  }

  const start = path.match(/^\/api\/admin\/runs\/([^/]+)\/research\/start$/);
  if (start) {
    const runId = decodeURIComponent(start[1]);
    const current = await db.prepare("SELECT status FROM research_archives WHERE run_id=?").bind(runId).first();
    if (current?.status === "archived") return json({ ok: true, run_id: runId, status: "already_archived" });
    await db.prepare("INSERT INTO research_archives(run_id,status,expected_symbols,received_symbols,payload_hash,updated_at) VALUES(?, 'started', ?, 0, ?, datetime('now')) ON CONFLICT(run_id) DO UPDATE SET status='started', expected_symbols=excluded.expected_symbols, payload_hash=excluded.payload_hash, updated_at=datetime('now')")
      .bind(runId, Number(payload.expected_symbols || 0), payload.payload_hash || null).run();
    return json({ ok: true, run_id: runId, status: "archive_started" });
  }

  const batch = path.match(/^\/api\/admin\/runs\/([^/]+)\/research\/batch$/);
  if (batch) {
    const runId = decodeURIComponent(batch[1]);
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    if (rows.length > 100) return json({ ok: false, error: "batch_too_large:max_100" }, 422);
    const statements = rows.map((row) => db.prepare("INSERT INTO research_rows(run_id,symbol,row_json) VALUES(?,?,?) ON CONFLICT(run_id,symbol) DO UPDATE SET row_json=excluded.row_json").bind(runId, String(row.symbol || ""), JSON.stringify(row)));
    if (statements.length) await db.batch(statements);
    const count = await db.prepare("SELECT COUNT(*) AS count FROM research_rows WHERE run_id=?").bind(runId).first();
    await db.prepare("UPDATE research_archives SET received_symbols=?, updated_at=datetime('now') WHERE run_id=?").bind(Number(count?.count || 0), runId).run();
    return json({ ok: true, run_id: runId, received_symbols: Number(count?.count || 0) });
  }

  const commit = path.match(/^\/api\/admin\/runs\/([^/]+)\/research\/commit$/);
  if (commit) {
    const runId = decodeURIComponent(commit[1]);
    const archive = await db.prepare("SELECT * FROM research_archives WHERE run_id=?").bind(runId).first();
    if (!archive) return json({ ok: false, error: "archive_not_started" }, 409);
    if (Number(archive.expected_symbols) && Number(archive.received_symbols) < Number(archive.expected_symbols)) return json({ ok: false, error: `archive_incomplete:${archive.received_symbols}/${archive.expected_symbols}` }, 422);
    await db.prepare("UPDATE research_archives SET status='archived', updated_at=datetime('now') WHERE run_id=?").bind(runId).run();
    return json({ ok: true, run_id: runId, status: "archived", received_symbols: Number(archive.received_symbols) });
  }

  const portfolio = path.match(/^\/api\/admin\/runs\/([^/]+)\/portfolio$/);
  if (portfolio) {
    const runId = decodeURIComponent(portfolio[1]);
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    if (rows.length > 100) return json({ ok: false, error: "batch_too_large:max_100" }, 422);
    const statements = [];
    for (const source of rows) {
      const row = { ...source };
      const claimed = row.row_hash || "";
      delete row.row_hash;
      const computed = await sha256(stable(row));
      if (claimed && claimed !== computed) return json({ ok: false, error: `portfolio_row_hash_mismatch:${row.symbol || "unknown"}` }, 422);
      statements.push(db.prepare("INSERT INTO portfolio_rows(run_id,symbol,row_hash,row_json) VALUES(?,?,?,?) ON CONFLICT(run_id,symbol) DO UPDATE SET row_hash=excluded.row_hash,row_json=excluded.row_json").bind(runId, String(row.symbol || ""), computed, JSON.stringify({ ...row, row_hash: computed })));
    }
    if (statements.length) await db.batch(statements);
    return json({ ok: true, run_id: runId, received_rows: rows.length });
  }
  return json({ ok: false, error: "not_found" }, 404);
}

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  try {
    if (request.method === "GET") return await handleRead(url.pathname, url, env);
    if (request.method === "POST" && url.pathname === "/api/run") return await handleManualRun(request, env);
    if (request.method === "POST") return await handleWrite(request, url.pathname, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { allow: "GET,POST,OPTIONS" } });
    return json({ ok: false, error: "method_not_allowed" }, 405);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const status = message === "invalid_json" ? 400 : 500;
    return json({ ok: false, error: message }, status);
  }
}

export const __test = { stable, sha256, secureEqual, workflowDispatchUrl };
