import { claim, config, json, schema } from "../../lib/astra.js";

export async function onRequestPost({ request, env }) {
  if (!env.DB || !env.GITHUB_TOKEN) return json({ ok: false, error: "Astra requires the existing DB and GITHUB_TOKEN settings." }, 503);
  const origin = request.headers.get("origin");
  if ((origin && origin !== new URL(request.url).origin) || request.headers.get("sec-fetch-site") === "cross-site") return json({ ok: false, error: "cross_origin_request" }, 403);
  let parameters;
  try { parameters = config((await request.json()).config); }
  catch (error) { return json({ ok: false, error: error.message }, 422); }
  const requestId = crypto.randomUUID();
  try {
    await schema(env.DB);
    if (!(await claim(env.DB, requestId))) return json({ ok: false, error: "An Astra run is active or was requested within the last 15 minutes." }, 409);
    const owner = env.GITHUB_OWNER || "yankkhaing-watermelon";
    const repo = env.GITHUB_REPOSITORY || "QuantTerminal";
    const response = await fetch(`https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions/workflows/astra.yml/dispatches`, {
      method: "POST", headers: { accept: "application/vnd.github+json", authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "content-type": "application/json", "user-agent": "quant-astra", "x-github-api-version": "2022-11-28" },
      body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs: { request_id: requestId, config: JSON.stringify(parameters) } }),
    });
    if (!response.ok) throw new Error(`GitHub could not start Astra (HTTP ${response.status}). Check that astra.yml is on the configured branch.`);
    return json({ ok: true, state: "queued", request_id: requestId }, 202);
  } catch (error) {
    await env.DB.prepare("UPDATE astra_job SET state='failed',lock_until=0,message=? WHERE request_id=?")
      .bind(String(error.message).slice(0, 300), requestId).run().catch(() => {});
    return json({ ok: false, error: String(error.message) }, 502);
  }
}
export const onRequest = (context) => context.request.method === "POST" ? onRequestPost(context) : json({ ok: false, error: "method_not_allowed" }, 405);
