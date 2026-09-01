import assert from "node:assert/strict";
import test from "node:test";
import { __test, onRequest } from "../functions/api/[[path]].js";

test("stable JSON sorts object keys recursively", () => {
  assert.equal(__test.stable({ z: 1, a: { y: 2, b: 3 } }), '{"a":{"b":3,"y":2},"z":1}');
});

test("stable JSON preserves array order", () => {
  assert.equal(__test.stable([{ b: 2, a: 1 }, 3]), '[{"a":1,"b":2},3]');
});

test("sha256 returns a lowercase 64-character digest", async () => {
  assert.match(await __test.sha256("quant"), /^[0-9a-f]{64}$/);
});

test("manual run keys use constant-time digest comparison", async () => {
  assert.equal(await __test.secureEqual("private-key", "private-key"), true);
  assert.equal(await __test.secureEqual("private-key", "wrong-key"), false);
  assert.equal(await __test.secureEqual("", "private-key"), false);
});

test("workflow dispatch defaults to the active replacement repository", () => {
  assert.equal(
    __test.workflowDispatchUrl({}),
    "https://api.github.com/repos/yankkhaing-watermelon/QuantTerminal/actions/workflows/daily-quant.yml/dispatches",
  );
});

test("manual run endpoint fails safely when GitHub token is absent", async () => {
  const response = await onRequest({
    request: new Request("https://example.test/api/run", { method: "POST" }),
    env: {},
  });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { ok: false, error: "github_token_not_configured" });
});
