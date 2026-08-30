const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  },
});

export async function onRequestGet({ env }) {
  return json({
    ok: true,
    publish_token_configured: Boolean(env.PUBLISH_TOKEN),
  });
}
