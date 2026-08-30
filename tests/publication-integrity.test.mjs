import assert from "node:assert/strict";
import test from "node:test";

import { sha256, stable } from "../lib/research-archive.js";
import {
  PublicationIntegrityError,
  assertImmutablePublication,
  inspectStoredPayload,
  validatePublication,
} from "../lib/publication-integrity.js";

function fixture() {
  const stocks = Array.from({ length: 900 }, (_, index) => ({
    symbol: `S${String(index + 1).padStart(4, "0")}`,
    rank: index + 1,
    quant_score: 80 - (index / 100),
    action: "HOLD",
  }));
  const run = {
    version: "5.0.0",
    run_id: "qv5-2026-08-29-abcdef123456",
    scan_date: "2026-08-29",
    generated_at: "2026-08-29T12:00:00+00:00",
    market: "MYX",
    benchmark: "^KLSE",
    universe_size: 1000,
    fresh_symbols: 900,
    stocks,
    portfolio: [],
    unexplained_activity: [],
    abnormal_activity: [],
    methodology: {
      price_adjustment: "unadjusted",
      session_gate: "2026-08-29",
    },
  };
  const archive = {
    status: "archived",
    integrity: "verified",
    expected_symbols: 900,
    received_symbols: 900,
    payload_hash: "a".repeat(64),
    manifest_hash: "b".repeat(64),
  };
  return { run, archive };
}

test("Step 15 accepts a reconciled full-universe publication", () => {
  const { run, archive } = fixture();
  const result = validatePublication(run, archive);
  assert.equal(result.stock_count, 900);
  assert.equal(result.fresh_symbols, 900);
});

test("Step 15 rejects partial production publications", () => {
  const { run, archive } = fixture();
  run.universe_size = 899;
  run.fresh_symbols = 899;
  run.stocks = run.stocks.slice(0, 899);
  archive.expected_symbols = 899;
  archive.received_symbols = 899;
  assert.throws(() => validatePublication(run, archive), /production_universe_too_small/);
});

test("Step 15 rejects rank and archive reconciliation drift", () => {
  const { run, archive } = fixture();
  run.stocks[10].rank = 99;
  assert.throws(() => validatePublication(run, archive), /non_contiguous_rank/);

  const second = fixture();
  second.archive.received_symbols = 899;
  assert.throws(() => validatePublication(second.run, second.archive), /research_archive_universe_mismatch/);
});

test("Step 15 rejects activity alias drift", () => {
  const { run, archive } = fixture();
  run.abnormal_activity = [{ symbol: "S0001", activity_score: 90, activity_level: "HIGH" }];
  assert.throws(() => validatePublication(run, archive), /activity_alias_mismatch/);
});

test("published run ids are immutable but identical retries are idempotent", async () => {
  const { run } = fixture();
  const hash = await sha256(stable(run));
  const existing = { run_id: run.run_id, payload_hash: hash, payload_json: JSON.stringify(run) };
  const result = await assertImmutablePublication(existing, hash);
  assert.equal(result.idempotent, true);

  await assert.rejects(
    () => assertImmutablePublication(existing, "f".repeat(64)),
    (error) => error instanceof PublicationIntegrityError && /immutable_quant_run_conflict/.test(error.message),
  );
});

test("stored payload verification fails closed on hash corruption", async () => {
  const { run } = fixture();
  const existing = { run_id: run.run_id, payload_hash: "0".repeat(64), payload_json: JSON.stringify(run) };
  await assert.rejects(
    () => inspectStoredPayload(existing),
    (error) => error instanceof PublicationIntegrityError && error.message === "stored_quant_payload_hash_mismatch",
  );
});
