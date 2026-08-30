import assert from "node:assert/strict";
import test from "node:test";
import { __test } from "../functions/api/[[path]].js";

test("stable JSON sorts object keys recursively", () => {
  assert.equal(__test.stable({ z: 1, a: { y: 2, b: 3 } }), '{"a":{"b":3,"y":2},"z":1}');
});

test("stable JSON preserves array order", () => {
  assert.equal(__test.stable([{ b: 2, a: 1 }, 3]), '[{"a":1,"b":2},3]');
});

test("sha256 returns a lowercase 64-character digest", async () => {
  assert.match(await __test.sha256("quant"), /^[0-9a-f]{64}$/);
});
