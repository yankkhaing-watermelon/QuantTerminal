import { claim, config, schema, sha256, json } from "../../lib/astra.js";

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
  const workflow = "daily-quant.yml";
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

async function dispatch(env, requestId, parameters) {
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
    body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs: { max_symbols: "0", request_id: requestId, config: JSON.stringify(parameters) } }),
  });
  if (!response.ok) return { ok: false, error: "github_dispatch_failed", github_status: response.status, status: 502 };
  return { ok: true };
}

export async function onRequestPost(context) {
  const { env } = context;
  const db = getDb(env);
  if (!db) return json({ ok: false, error: "database_not_configured" }, 503);

  try {
    const origin = context.request?.headers.get("origin");
    if ((origin && origin !== new URL(context.request.url).origin) || context.request?.headers.get("sec-fetch-site") === "cross-site") return json({ ok: false, error: "cross_origin_request" }, 403);
    let parameters;
    try {
      const body = context.request ? await context.request.text() : "";
      parameters = config(body ? JSON.parse(body).config : {});
    } catch (error) { return json({ ok: false, error: error.message }, 422); }
    await schema(db);
    const now = malaysiaParts();
    const latest = await latestPayload(db);
    const reason = reuseReason(latest, now);
    const row = await db.prepare("SELECT payload_json,payload_hash FROM astra_reports ORDER BY scan_date DESC,generated_at DESC LIMIT 1").first();
    const astra = row && await sha256(row.payload_json) === row.payload_hash ? JSON.parse(row.payload_json) : null;
    const matchingConfig = astra && Object.entries(parameters).every(([key, value]) => astra.config?.[key] === value);
    const matchingSnapshot = astra?.shared_run_id && astra.shared_run_id === latest?.shared_run_id;
    if (reason && latest && matchingConfig && matchingSnapshot) {
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

    const requestId = crypto.randomUUID();
    if (!(await claim(db, requestId))) return json({ ok: false, error: "run_cooldown", retry_after: 900 }, 409);
    const queued = await dispatch(env, requestId, parameters);
    if (!queued.ok) {
      await db.prepare("UPDATE astra_job SET state='failed',lock_until=0,message=? WHERE request_id=?").bind(queued.error, requestId).run();
      return json(queued, queued.status);
    }
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
