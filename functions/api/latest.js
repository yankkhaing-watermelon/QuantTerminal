import { PublicationIntegrityError, inspectStoredPayload } from "../../lib/publication-integrity.js";

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});
const PRODUCTION_LATEST_URL = "https://bursamusangking-quant-terminal.pages.dev/api/latest";

function getDb(env) {
  if (!env.DB) throw new Error("D1 binding DB is not configured");
  return env.DB;
}

async function columns(db) {
  const result = await db.prepare("PRAGMA table_info(quant_runs)").all();
  return new Set((result.results || []).map((row) => String(row.name)));
}

export async function onRequestGet({ env, request }) {
  try {
    const requestUrl = new URL(request.url);
    if (!env.DB && requestUrl.hostname !== "bursamusangking-quant-terminal.pages.dev") {
      return fetch(PRODUCTION_LATEST_URL, { headers: { accept: "application/json" } });
    }
    const db = getDb(env);
    const table = await db.prepare("SELECT type FROM sqlite_master WHERE name='quant_runs' LIMIT 1").first();
    if (!table?.type) return json({ ok: true, data: null, state: "awaiting_first_publication" });

    const names = await columns(db);
    if (!names.has("payload_json")) return json({ ok: false, error: "quant_runs_payload_json_missing" }, 503);
    const select = [
      names.has("run_id") ? "run_id" : "NULL AS run_id",
      names.has("payload_hash") ? "payload_hash" : "NULL AS payload_hash",
      "payload_json",
    ].join(", ");
    const order = names.has("scan_date") && names.has("generated_at")
      ? "scan_date DESC, generated_at DESC"
      : names.has("created_at") ? "created_at DESC" : "rowid DESC";
    const result = await db.prepare(`SELECT ${select} FROM quant_runs ORDER BY ${order} LIMIT 25`).all();

    for (const row of (result.results || [])) {
      if (!row?.payload_json) continue;
      const modern = String(row.run_id || "").startsWith("qv5-");
      if (!row.payload_hash && !modern) continue;
      const verified = await inspectStoredPayload(row);
      return json({ ok: true, data: verified.payload, payload_hash: verified.payload_hash, integrity: "verified" });
    }
    return json({ ok: true, data: null, state: "awaiting_first_publication" });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const status = error instanceof PublicationIntegrityError ? error.status : 500;
    return json({ ok: false, error: message, integrity: "failed" }, status);
  }
}

export async function onRequest(context) {
  if (context.request.method === "GET") return onRequestGet(context);
  return json({ ok: false, error: "method_not_allowed" }, 405);
}
