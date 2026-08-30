const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

async function fingerprint(value) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

export async function onRequestGet({ env }) {
  if (!env.PUBLISH_TOKEN) return json({ ok: true, configured: false });
  return json({ ok: true, configured: true, fingerprint: await fingerprint(env.PUBLISH_TOKEN) });
}
