const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

function getDb(env) {
  if (!env.DB) return null;
  return env.DB;
}

function malaysiaParts(value = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kuala_Lumpur",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    hourCycle: "h23",
  }).formatToParts(value);
  const map = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
  return {
    date: `${map.year}-${map.month}-${map.day}`,
    weekday: map.weekday,
    hour: Number(map.hour || 0),
  };
}

async function tableColumns(db, table) {
  const tableRow = await db.prepare("SELECT type FROM sqlite_master WHERE name=? LIMIT 1").bind(table).first();
  if (!tableRow?.type) return new Set();
  const result = await db.prepare(`PRAGMA table_info(${table})`).all();
  return new Set((result.results || []).map((row) => String(row.name)));
}

async function latestPayload(db) {
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
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && Object.keys(parsed).length) return parsed;
    } catch {
      // Skip malformed legacy rows.
    }
  }
  return null;
}

function workflowDispatchUrl(env) {
  const owner = env.GITHUB_OWNER || "yankkhaing-watermelon";
  const repository = env.GITHUB_REPOSITORY || "QuantTerminal";
  const workflow = env.GITHUB_WORKFLOW || "daily-quant.yml";
  return `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`;
}

function reuseReason(latest, now) {
  if (!latest) return null;
  if (String(latest.scan_date || "") === now.date) return "same_market_date";
  const generated = latest.generated_at ? malaysiaParts(new Date(latest.generated_at)).date : null;
  if (generated === now.date) return "already_generated_today";
  if (["Sat", "Sun"].includes(now.weekday)) return "market_weekend";
  if (now.hour < 18) return "before_completed_daily_session";
  return null;
}

async function ensureRequestTable(db) {
  await db.prepare("CREATE TABLE IF NOT EXISTS manual_run_requests (request_id TEXT PRIMARY KEY, requested_at_epoch INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued')").run();
}

async function dispatch(env) {
  if (!env.GITHUB_TOKEN) return { ok: false, error: "github_token_not_configured", status: 503 };
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
  if (!response.ok) return { ok: false, error: "github_dispatch_failed", github_status: response.status, status: 502 };
  return { ok: true };
}

export async function onRequestPost(context) {
  const { env } = context;
  const db = getDb(env);
  if (!db) return json({ ok: false, error: "database_not_configured" }, 503);

  try {
    await ensureRequestTable(db);
    const now = malaysiaParts();
    const latest = await latestPayload(db);
    const reason = reuseReason(latest, now);
    if (reason && latest) {
      return json({
        ok: true,
        state: "reused",
        reason,
        scan_date: latest.scan_date || null,
        run_id: latest.run_id || null,
        additional_market_scan: false,
        data: latest,
      });
    }

    const epoch = Math.floor(Date.now() / 1000);
    const cooldown = Math.max(300, Number(env.RUN_COOLDOWN_SECONDS || 900));
    const last = await db.prepare("SELECT requested_at_epoch FROM manual_run_requests ORDER BY requested_at_epoch DESC LIMIT 1").first();
    const elapsed = epoch - Number(last?.requested_at_epoch || 0);
    if (last && elapsed < cooldown) {
      return json({ ok: false, error: "run_cooldown", retry_after: cooldown - elapsed }, 409);
    }

    const queued = await dispatch(env);
    if (!queued.ok) return json(queued, queued.status);

    const requestId = crypto.randomUUID();
    await db.prepare("INSERT INTO manual_run_requests(request_id, requested_at_epoch, status) VALUES(?, ?, 'queued')")
      .bind(requestId, epoch).run();
    return json({ ok: true, state: "queued", request_id: requestId, poll_seconds: 15, additional_market_scan: true }, 202);
  } catch (error) {
    return json({ ok: false, error: error instanceof Error ? error.message : String(error) }, 500);
  }
}

export async function onRequest(context) {
  if (context.request.method === "POST") return onRequestPost(context);
  return json({ ok: false, error: "method_not_allowed" }, 405);
}

export const __test = { malaysiaParts, reuseReason, workflowDispatchUrl };
