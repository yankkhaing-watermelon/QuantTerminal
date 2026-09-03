import assert from "node:assert/strict";
import test from "node:test";
import { __test } from "../functions/api/run.js";

test("run route defaults to the active QuantTerminal workflow", () => {
  assert.equal(
    __test.workflowDispatchUrl({}),
    "https://api.github.com/repos/yankkhaing-watermelon/QuantTerminal/actions/workflows/daily-quant.yml/dispatches",
  );
});

test("same market date reuses the published snapshot", () => {
  const reason = __test.reuseReason(
    { scan_date: "2026-09-03", generated_at: "2026-09-03T12:00:00Z" },
    { date: "2026-09-03", weekday: "Thu", hour: 20 },
  );
  assert.equal(reason, "same_market_date");
});

test("weekend reuses the latest published snapshot", () => {
  const reason = __test.reuseReason(
    { scan_date: "2026-09-04", generated_at: "2026-09-04T12:00:00Z" },
    { date: "2026-09-05", weekday: "Sat", hour: 20 },
  );
  assert.equal(reason, "market_weekend");
});

test("pre-close weekday does not dispatch a forming daily-bar rescan", () => {
  const reason = __test.reuseReason(
    { scan_date: "2026-09-02", generated_at: "2026-09-02T12:00:00Z" },
    { date: "2026-09-03", weekday: "Thu", hour: 15 },
  );
  assert.equal(reason, "before_completed_daily_session");
});

test("after-close stale snapshot permits a new scan", () => {
  const reason = __test.reuseReason(
    { scan_date: "2026-09-02", generated_at: "2026-09-02T12:00:00Z" },
    { date: "2026-09-03", weekday: "Thu", hour: 20 },
  );
  assert.equal(reason, null);
});
