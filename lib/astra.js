import { sha256 } from "./research-archive.js";

export const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});
export const DEFAULTS = { capital: 100000, risk_pct: .5, max_positions: 8,
  max_position_pct: 10, max_sector_pct: 25, min_turnover: 1000000,
  participation_pct: 1, initial_atr: 2.5, trailing_atr: 3,
  fee_bps: 20, minimum_fee: 8, slippage_bps: 10, breadth_filter: false };
export function config(input = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("invalid_config");
  if (Object.keys(input).some((key) => !(key in DEFAULTS))) throw new Error("unknown_config_field");
  const output = { ...DEFAULTS, ...input };
  for (const [key, value] of Object.entries(output)) {
    if (key === "breadth_filter") { if (typeof value !== "boolean") throw new Error("invalid_breadth_filter"); }
    else if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw new Error(`invalid_${key}`);
  }
  if (output.capital < 1000 || output.capital > 100000000 || output.risk_pct <= 0 || output.risk_pct > 5 ||
      output.max_positions < 1 || output.max_positions > 50 || !Number.isInteger(output.max_positions) ||
      output.participation_pct <= 0 || output.participation_pct > 5 || output.initial_atr <= 0 || output.trailing_atr <= 0 ||
      output.fee_bps > 250 || output.slippage_bps > 250 ||
      output.max_position_pct <= 0 || output.max_position_pct > 100 || output.max_sector_pct <= 0 || output.max_sector_pct > 100) {
    throw new Error("config_out_of_range");
  }
  return output;
}
export async function schema(db) {
  await db.batch([
    db.prepare("CREATE TABLE IF NOT EXISTS astra_job (id INTEGER PRIMARY KEY CHECK(id=1), request_id TEXT, state TEXT NOT NULL, requested_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, lock_until INTEGER NOT NULL, processed INTEGER DEFAULT 0, total INTEGER DEFAULT 0, message TEXT DEFAULT '')"),
    db.prepare("CREATE TABLE IF NOT EXISTS astra_reports (run_id TEXT PRIMARY KEY, scan_date TEXT NOT NULL, generated_at TEXT NOT NULL, payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL)"),
    db.prepare("CREATE INDEX IF NOT EXISTS idx_astra_reports_latest ON astra_reports(scan_date DESC, generated_at DESC)"),
  ]);
}
export async function claim(db, requestId, state = "queued") {
  const now = Math.floor(Date.now() / 1000);
  const result = await db.prepare("INSERT INTO astra_job(id,request_id,state,requested_at,updated_at,lock_until) VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET request_id=excluded.request_id,state=excluded.state,requested_at=excluded.requested_at,updated_at=excluded.updated_at,lock_until=excluded.lock_until,processed=0,total=0,message='' WHERE astra_job.lock_until <= ? AND astra_job.requested_at <= ?")
    .bind(requestId, state, now, now, now + 15000, now, now - 900).run();
  return Number(result.meta?.changes) === 1;
}
export async function authorized(request, env) {
  const token = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "") || "";
  return Boolean(env.PUBLISH_TOKEN && token && await sha256(token) === await sha256(env.PUBLISH_TOKEN));
}
export { sha256 };
