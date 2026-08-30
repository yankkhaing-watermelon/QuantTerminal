import {
  ArchiveIntegrityError,
  commitResearchArchive,
  getResearchArchiveStatus,
  putResearchBatch,
  sha256,
  stable,
  startResearchArchive,
} from "../../../../lib/research-archive.js";

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

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

function getDb(env) {
  if (!env.DB) throw new Error("D1 binding DB is not configured");
  return env.DB;
}

async function bodyJson(request) {
  try { return await request.json(); } catch { throw new ArchiveIntegrityError("invalid_json", 400); }
}

async function portfolioWrite(db, runId, payload) {
  await db.prepare("CREATE TABLE IF NOT EXISTS portfolio_rows (run_id TEXT NOT NULL, symbol TEXT NOT NULL, row_hash TEXT NOT NULL DEFAULT '', row_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (run_id, symbol))").run();
  await db.prepare("CREATE INDEX IF NOT EXISTS idx_portfolio_rows_run ON portfolio_rows(run_id)").run();
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (rows.length > 100) throw new ArchiveIntegrityError("batch_too_large:max_100", 422);
  const statements = [];
  for (const source of rows) {
    const row = { ...source };
    const claimed = row.row_hash || "";
    delete row.row_hash;
    const computed = await sha256(stable(row));
    if (claimed && claimed !== computed) throw new ArchiveIntegrityError(`portfolio_row_hash_mismatch:${row.symbol || "unknown"}`, 422);
    statements.push(db.prepare("INSERT INTO portfolio_rows(run_id,symbol,row_hash,row_json) VALUES(?,?,?,?) ON CONFLICT(run_id,symbol) DO UPDATE SET row_hash=excluded.row_hash,row_json=excluded.row_json")
      .bind(runId, String(row.symbol || ""), computed, JSON.stringify({ ...row, row_hash: computed })));
  }
  for (let index = 0; index < statements.length; index += 50) {
    await db.batch(statements.slice(index, index + 50));
  }
  return { ok: true, run_id: runId, received_rows: rows.length };
}

export async function onRequest(context) {
  const { request, env } = context;
  try {
    if (!(await authorized(request, env))) return json({ ok: false, error: "unauthorized" }, 401);
    const db = getDb(env);
    const path = new URL(request.url).pathname;

    const status = path.match(/^\/api\/admin\/runs\/([^/]+)\/research\/status$/);
    if (request.method === "GET" && status) {
      const runId = decodeURIComponent(status[1]);
      return json({ ok: true, ...(await getResearchArchiveStatus(db, runId)) });
    }

    if (request.method !== "POST") return json({ ok: false, error: "method_not_allowed" }, 405);
    const payload = await bodyJson(request);

    const start = path.match(/^\/api\/admin\/runs\/([^/]+)\/research\/start$/);
    if (start) {
      const runId = decodeURIComponent(start[1]);
      return json({ ok: true, ...(await startResearchArchive(db, runId, payload.expected_symbols)) });
    }

    const batch = path.match(/^\/api\/admin\/runs\/([^/]+)\/research\/batch$/);
    if (batch) {
      const runId = decodeURIComponent(batch[1]);
      return json({ ok: true, ...(await putResearchBatch(db, runId, payload.rows)) });
    }

    const commit = path.match(/^\/api\/admin\/runs\/([^/]+)\/research\/commit$/);
    if (commit) {
      const runId = decodeURIComponent(commit[1]);
      return json({ ok: true, ...(await commitResearchArchive(db, runId)) });
    }

    const portfolio = path.match(/^\/api\/admin\/runs\/([^/]+)\/portfolio$/);
    if (portfolio) {
      return json(await portfolioWrite(db, decodeURIComponent(portfolio[1]), payload));
    }

    return json({ ok: false, error: "not_found" }, 404);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const status = error instanceof ArchiveIntegrityError ? error.status : 500;
    return json({ ok: false, error: message }, status);
  }
}
