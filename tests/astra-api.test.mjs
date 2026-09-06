import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { claim, config, schema } from "../lib/astra.js";
import { onRequestPost as publish } from "../functions/api/admin/astra.js";
import { onRequestGet as latest } from "../functions/api/astra.js";
import { onRequestPost as run } from "../functions/api/astra-run.js";
import { sha256 } from "../lib/astra.js";

function database() {
  const sql = new DatabaseSync(":memory:");
  const db = { prepare(query) {
    const make = (args = []) => ({
      bind: (...values) => make(values),
      first: async () => sql.prepare(query).get(...args) || null,
      all: async () => ({ results: sql.prepare(query).all(...args) }),
      run: async () => ({ meta: { changes: Number(sql.prepare(query).run(...args).changes) } }),
    });
    return make();
  }, batch: async (statements) => { const results = []; for (const s of statements) results.push(await s.run()); return results; } };
  return { db, sql };
}
const request = (body, token = "test-only") => new Request("https://example.test/api/admin/astra", {
  method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify(body),
});

test("Astra locks prevent simultaneous public runs and enforce cooldown", async () => {
  const { db, sql } = database(); await schema(db);
  assert.equal(await claim(db, "first"), true);
  assert.equal(await claim(db, "second"), false);
  sql.exec("UPDATE astra_job SET lock_until=0");
  assert.equal(await claim(db, "second"), false);
  sql.exec("UPDATE astra_job SET requested_at=0");
  assert.equal(await claim(db, "second"), true);
  sql.close();
});

test("configuration rejects invalid and unknown inputs", () => {
  assert.equal(config({}).risk_pct, .5);
  for (const input of [{ risk_pct: 100 }, { capital: "10000" }, { max_positions: 2.5 }, { force: true }]) {
    assert.throws(() => config(input));
  }
});

test("Astra publish authenticates, remains isolated and verifies stored hashes", async () => {
  const { db, sql } = database(); const env = { DB: db, PUBLISH_TOKEN: "test-only" };
  assert.equal((await publish({ env, request: request({ action: "start", request_id: "run-1" }, "wrong") })).status, 401);
  assert.equal((await publish({ env, request: request({ action: "start", request_id: "run-1" }) })).status, 200);
  const data = { version: "astra-1.0.0", run_id: `astra-${"a".repeat(24)}`, scan_date: "2026-09-04",
    generated_at: "2026-09-05T10:00:00Z", strategies: { breakout: {}, pullback: {} }, config: config({}),
    coverage: { discovered: 1129, processed: 1129, fresh_with_history: 1000 } };
  const payload = { action: "publish", request_id: "run-1", data };
  assert.equal((await publish({ env, request: request(payload) })).status, 200);
  assert.equal((await publish({ env, request: request(payload) })).status, 200);
  assert.equal((await (await latest({ env })).json()).data.run_id, data.run_id);
  assert.equal(sql.prepare("SELECT name FROM sqlite_master WHERE name='quant_runs'").get(), undefined);
  assert.equal((await publish({ env, request: request({ ...payload, data: { ...data, scan_date: "2026-09-03" } }) })).status, 409);
  sql.exec("UPDATE astra_reports SET payload_json='{}'");
  assert.equal((await latest({ env })).status, 500);
  sql.close();
});

test("public run blocks cross-origin triggers without dispatch", async () => {
  const response = await run({ env: { DB: {}, GITHUB_TOKEN: "test-only" }, request: new Request("https://example.test/api/astra-run", {
    method: "POST", headers: { origin: "https://unrelated.test" }, body: "{}",
  }) });
  assert.equal(response.status, 403);
});

test("superseded jobs cannot update newer job progress", async () => {
  const { db, sql } = database(); const env = { DB: db, PUBLISH_TOKEN: "test-only" };
  await publish({ env, request: request({ action: "start", request_id: "new-job" }) });
  assert.equal((await publish({ env, request: request({ action: "progress", request_id: "old-job", processed: 5, total: 10 }) })).status, 409);
  assert.equal(sql.prepare("SELECT processed FROM astra_job").get().processed, 0);
  sql.close();
});

test("top RUN reuses only a matching shared snapshot and configuration", async () => {
  const { db, sql } = database(); await schema(db);
  sql.exec("CREATE TABLE quant_runs(payload_json TEXT, scan_date TEXT, generated_at TEXT)");
  const date = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kuala_Lumpur", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const quant = { shared_run_id: "shared-one", scan_date: date, generated_at: new Date().toISOString() };
  sql.prepare("INSERT INTO quant_runs VALUES(?,?,?)").run(JSON.stringify(quant), date, quant.generated_at);
  const astra = { version: "astra-2.0.0", ...quant, config: config({}) }, text = JSON.stringify(astra);
  sql.prepare("INSERT INTO astra_reports VALUES(?,?,?,?,?)").run("one", date, quant.generated_at, await sha256(text), text);
  const originalFetch = globalThis.fetch; let dispatches = 0;
  globalThis.fetch = async (url, options) => {
    dispatches++;
    assert.match(url, /workflows\/daily-quant.yml\/dispatches$/);
    assert.equal(JSON.parse(JSON.parse(options.body).inputs.config).capital, 200000);
    return new Response(null, { status: 204 });
  };
  try {
    const env = { DB: db, GITHUB_TOKEN: "test-only" };
    const reused = await (await run({ env, request: request({ config: {} }) })).json();
    assert.equal(reused.state, "reused"); assert.equal(dispatches, 0);
    const queued = await (await run({ env, request: request({ config: { capital: 200000 } }) })).json();
    assert.equal(queued.state, "queued"); assert.equal(dispatches, 1);
  } finally { globalThis.fetch = originalFetch; sql.close(); }
});

test("Astra publication can remain running until the shared job completes", async () => {
  const { db, sql } = database(); const env = { DB: db, PUBLISH_TOKEN: "test-only" };
  await publish({ env, request: request({ action: "start", request_id: "shared" }) });
  const data = { version: "astra-1.0.0", run_id: `astra-${"b".repeat(24)}`, scan_date: "2026-09-04",
    generated_at: "2026-09-05T10:00:00Z", strategies: { breakout: {}, pullback: {} }, config: config({}),
    coverage: { discovered: 1129, processed: 1129, fresh_with_history: 1000 } };
  await publish({ env, request: request({ action: "publish", request_id: "shared", data, defer_completion: true }) });
  assert.equal(sql.prepare("SELECT state FROM astra_job").get().state, "running");
  await publish({ env, request: request({ action: "complete", request_id: "shared" }) });
  assert.equal(sql.prepare("SELECT state FROM astra_job").get().state, "completed");
  sql.close();
});
