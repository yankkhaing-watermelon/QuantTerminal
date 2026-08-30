import assert from "node:assert/strict";
import test from "node:test";
import { ArchiveIntegrityError, buildResearchSeal } from "../lib/research-archive.js";

test("research seal is independent of row and object key order", async () => {
  const left = await buildResearchSeal([
    { symbol: "BETA", score: 2, meta: { z: 1, a: 3 } },
    { symbol: "ALPHA", score: 1 },
  ]);
  const right = await buildResearchSeal([
    { score: 1, symbol: "ALPHA" },
    { meta: { a: 3, z: 1 }, score: 2, symbol: "BETA" },
  ]);
  assert.equal(left.payload_hash, right.payload_hash);
  assert.equal(left.manifest_hash, right.manifest_hash);
  assert.deepEqual(left.rows.map((row) => row.symbol), ["ALPHA", "BETA"]);
});

test("research content change changes payload and manifest hashes", async () => {
  const before = await buildResearchSeal([{ symbol: "ALPHA", score: 1 }]);
  const after = await buildResearchSeal([{ symbol: "ALPHA", score: 1.01 }]);
  assert.notEqual(before.payload_hash, after.payload_hash);
  assert.notEqual(before.manifest_hash, after.manifest_hash);
  assert.notEqual(before.rows[0].row_hash, after.rows[0].row_hash);
});

test("duplicate research symbols fail closed", async () => {
  await assert.rejects(
    () => buildResearchSeal([{ symbol: "ALPHA", score: 1 }, { symbol: "ALPHA", score: 2 }]),
    (error) => error instanceof ArchiveIntegrityError && error.status === 422 && error.message === "duplicate_research_symbol:ALPHA",
  );
});
