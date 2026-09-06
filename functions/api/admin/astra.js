import { authorized, claim, config, json, schema, sha256 } from "../../../lib/astra.js";

export async function onRequestPost({ request, env }) {
  if (!(await authorized(request, env))) return json({ ok: false, error: "unauthorized" }, 401);
  if (!env.DB) return json({ ok: false, error: "database_not_configured" }, 503);
  try {
    const body = await request.json();
    const id = body.request_id;
    if (typeof id !== "string" || !/^[a-zA-Z0-9-]{1,100}$/.test(id)) return json({ ok: false, error: "invalid_request_id" }, 422);
    await schema(env.DB);
    let job = await env.DB.prepare("SELECT * FROM astra_job WHERE id=1").first();
    if (body.action === "start" && job?.request_id !== id) {
      if (!(await claim(env.DB, id, "running"))) return json({ ok: false, error: "astra_busy" }, 409);
      job = await env.DB.prepare("SELECT * FROM astra_job WHERE id=1").first();
    }
    if (job?.request_id !== id) return json({ ok: false, error: "job_superseded" }, 409);
    const now = Math.floor(Date.now() / 1000);
    if (body.action === "publish") {
      const data = body.data;
      if (!data || !["astra-1.0.0", "astra-2.0.0"].includes(data.version) || !/^astra-[a-f0-9]{24}$/.test(data.run_id || "") ||
          !/^\d{4}-\d{2}-\d{2}$/.test(data.scan_date || "") || !Number.isFinite(Date.parse(data.generated_at)) ||
          !data.strategies?.breakout || !data.strategies?.pullback || !data.coverage ||
          data.coverage.processed !== data.coverage.discovered || data.coverage.discovered < 900 ||
          data.coverage.fresh_with_history < 1) return json({ ok: false, error: "invalid_astra_payload" }, 422);
      config(data.config);
      const serialized = JSON.stringify(data);
      if (new TextEncoder().encode(serialized).length > 1800000) return json({ ok: false, error: "astra_payload_too_large" }, 413);
      const hash = await sha256(serialized);
      const existing = await env.DB.prepare("SELECT payload_hash FROM astra_reports WHERE run_id=?").bind(data.run_id).first();
      if (existing && existing.payload_hash !== hash) return json({ ok: false, error: "immutable_astra_run_conflict" }, 409);
      await env.DB.batch([
        env.DB.prepare("INSERT OR IGNORE INTO astra_reports(run_id,scan_date,generated_at,payload_hash,payload_json) VALUES(?,?,?,?,?)")
          .bind(data.run_id, data.scan_date, data.generated_at, hash, serialized),
        env.DB.prepare("UPDATE astra_job SET state=?,updated_at=?,lock_until=?,message='Astra published; finalizing shared run' WHERE request_id=?")
          .bind(body.defer_completion ? "running" : "completed", now, body.defer_completion ? now + 15000 : 0, id),
        env.DB.prepare("DELETE FROM astra_reports WHERE run_id NOT IN (SELECT run_id FROM astra_reports ORDER BY scan_date DESC,generated_at DESC LIMIT 3)"),
      ]);
      return json({ ok: true, run_id: data.run_id, payload_hash: hash });
    }
    if (!["start", "progress", "failed", "complete"].includes(body.action)) return json({ ok: false, error: "invalid_action" }, 422);
    const processed = Number(body.processed ?? job.processed ?? 0), total = Number(body.total ?? job.total ?? 0);
    if (!Number.isInteger(processed) || !Number.isInteger(total) || processed < 0 || total < processed) return json({ ok: false, error: "invalid_progress" }, 422);
    const failed = body.action === "failed";
    const complete = body.action === "complete";
    await env.DB.prepare("UPDATE astra_job SET state=?,updated_at=?,lock_until=?,processed=?,total=?,message=? WHERE request_id=?")
      .bind(failed ? "failed" : complete ? "completed" : "running", now, failed || complete ? 0 : now + 15000, processed, total,
        String(body.message || "Scanning TradingView Bursa stocks").slice(0, 300), id).run();
    return json({ ok: true });
  } catch { return json({ ok: false, error: "astra_write_failed" }, 500); }
}
export const onRequest = (context) => context.request.method === "POST" ? onRequestPost(context) : json({ ok: false, error: "method_not_allowed" }, 405);
