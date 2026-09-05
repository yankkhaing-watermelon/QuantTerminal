import { json, sha256 } from "../../lib/astra.js";

export async function onRequestGet({ env }) {
  if (!env.DB) return json({ ok: false, error: "database_not_configured" }, 503);
  try {
    const exists = await env.DB.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='astra_reports'").first();
    if (!exists) return json({ ok: true, data: null, job: null });
    const [report, job] = await Promise.all([
      env.DB.prepare("SELECT payload_json,payload_hash FROM astra_reports ORDER BY scan_date DESC, generated_at DESC LIMIT 1").first(),
      env.DB.prepare("SELECT request_id,state,requested_at,updated_at,lock_until,processed,total,message FROM astra_job WHERE id=1").first(),
    ]);
    if (report && await sha256(report.payload_json) !== report.payload_hash) return json({ ok: false, error: "astra_integrity_failed" }, 500);
    if (job && ["queued", "running"].includes(job.state) && job.lock_until < Date.now() / 1000) {
      job.state = "timed_out";
      job.message = "The worker stopped reporting. Check the Astra workflow logs before retrying.";
    }
    return json({ ok: true, data: report ? JSON.parse(report.payload_json) : null, job });
  } catch { return json({ ok: false, error: "astra_read_failed" }, 500); }
}
export const onRequest = (context) => context.request.method === "GET" ? onRequestGet(context) : json({ ok: false, error: "method_not_allowed" }, 405);
