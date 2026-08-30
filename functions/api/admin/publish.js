import {
  ArchiveIntegrityError,
  sha256,
  stable,
  verifyResearchArchive,
} from "../../../lib/research-archive.js";
import {
  PublicationIntegrityError,
  assertImmutablePublication,
  inspectStoredPayload,
  validatePublication,
} from "../../../lib/publication-integrity.js";

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

async function authorized(request, env) {
  if (!env.PUBLISH_TOKEN) return false;
  const bearer = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "") || "";
  const token = bearer || request.headers.get("x-publish-token") || "";
  if (!token) return false;
  const [left, right] = await Promise.all([sha256(token), sha256(env.PUBLISH_TOKEN)]);
  return left === right;
}

function getDb(env) {
  if (!env.DB) throw new Error("D1 binding DB is not configured");
  return env.DB;
}

async function schema(db) {
  const result = await db.prepare("PRAGMA table_info(quant_runs)").all();
  return (result.results || []).map((row) => ({
    name: String(row.name),
    type: String(row.type || "").toUpperCase(),
    notnull: Number(row.notnull) === 1,
    defaultValue: row.dflt_value,
  }));
}

function fallbackFor(column, run, payloadHash, payloadJson) {
  const name = column.name.toLowerCase();
  const stocks = Array.isArray(run.stocks) ? run.stocks.length : 0;
  const research = Array.isArray(run.research) ? run.research.length : 0;
  const fresh = Number(run.fresh_symbols ?? stocks ?? research ?? 0);
  const universe = Number(run.universe_size ?? fresh ?? 0);
  const date = String(run.scan_date || run.market_date || "");
  const generated = String(run.generated_at || new Date().toISOString());

  if (name === "run_id") return String(run.run_id);
  if (name === "market_date") return date;
  if (name === "status") return "published";
  if (name === "provider") return String(run.provider || run.data_provider || "quant-terminal");
  if (name === "model_version") return String(run.model_version || run.version || "5.0.0");
  if (name === "payload_hash") return payloadHash;
  if (name === "expected_symbols") return Number(run.expected_symbols ?? universe);
  if (name === "received_symbols") return Number(run.received_symbols ?? fresh);
  if (name === "valid_symbols") return Number(run.valid_symbols ?? fresh);
  if (name === "total_instruments") return Number(run.total_instruments ?? universe);
  if (name === "benchmark_date") return String(run.benchmark_date || date);
  if (name === "validation_json") return JSON.stringify(run.validation || { fresh_symbols: fresh, universe_size: universe });
  if (name === "started_at") return String(run.started_at || generated);
  if (name === "committed_at") return String(run.committed_at || generated);
  if (name === "scan_date") return date;
  if (name === "generated_at") return generated;
  if (name === "payload_json") return payloadJson;
  if (name === "created_at") return generated;

  if (column.type.includes("INT") || column.type.includes("REAL") || column.type.includes("NUM") || column.type.includes("DEC") || column.type.includes("FLOAT")) return 0;
  if (column.type.includes("BLOB")) return new Uint8Array();
  return "";
}

async function storedRun(db, runId) {
  return db.prepare("SELECT run_id,payload_hash,payload_json FROM quant_runs WHERE run_id = ? LIMIT 1").bind(runId).first();
}

async function publishQuantRun(db, run, payloadHash) {
  const columns = await schema(db);
  if (!columns.length) throw new Error("quant_runs_table_missing");

  const payloadJson = JSON.stringify(run);
  const hasRunId = columns.some((column) => column.name === "run_id");
  let existing = null;
  if (hasRunId) existing = await storedRun(db, run.run_id);

  if (existing) {
    const inspected = await assertImmutablePublication(existing, payloadHash);
    if (!inspected.declared_hash && columns.some((column) => column.name === "payload_hash")) {
      await db.prepare("UPDATE quant_runs SET payload_hash = ? WHERE run_id = ? AND (payload_hash IS NULL OR payload_hash = '')")
        .bind(payloadHash, run.run_id).run();
    }
    return { idempotent: true };
  }

  const values = new Map();
  for (const column of columns) {
    const requiredWithoutDefault = column.notnull && column.defaultValue == null;
    if (column.name === "id") {
      if (requiredWithoutDefault) {
        if (column.type.includes("INT")) {
          const row = await db.prepare("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM quant_runs").first();
          values.set(column.name, Number(row?.next_id || 1));
        } else {
          values.set(column.name, `${run.run_id}-${Date.now()}-${crypto.randomUUID()}`);
        }
      }
      continue;
    }
    if (requiredWithoutDefault || ["run_id", "market_date", "status", "provider", "model_version", "payload_hash", "expected_symbols", "received_symbols", "valid_symbols", "total_instruments", "benchmark_date", "validation_json", "started_at", "committed_at", "scan_date", "generated_at", "payload_json", "created_at"].includes(column.name)) {
      values.set(column.name, fallbackFor(column, run, payloadHash, payloadJson));
    }
  }

  const entries = [...values.entries()];
  const names = entries.map(([name]) => `"${name.replaceAll('"', '""')}"`).join(", ");
  const placeholders = entries.map(() => "?").join(", ");
  await db.prepare(`INSERT INTO quant_runs (${names}) VALUES (${placeholders})`).bind(...entries.map(([, value]) => value)).run();
  return { idempotent: false };
}

async function verifyCommittedPublication(db, runId, expectedHash) {
  const row = await storedRun(db, runId);
  if (!row) throw new PublicationIntegrityError("published_quant_run_missing_after_write", 500);
  const verified = await inspectStoredPayload(row);
  if (verified.payload_hash !== expectedHash) {
    throw new PublicationIntegrityError("published_quant_payload_hash_mismatch_after_write", 500);
  }
  return verified;
}

export async function onRequestPost(context) {
  const { request, env } = context;
  try {
    if (!(await authorized(request, env))) return json({ ok: false, error: "unauthorized" }, 401);
    const payload = await request.json();
    const run = payload.data || payload;
    if (!run?.run_id || !run?.scan_date || !run?.generated_at) return json({ ok: false, error: "missing_run_identity" }, 422);
    const serialized = stable(run);
    const payloadHash = await sha256(serialized);
    if (payload.payload_hash && payload.payload_hash !== payloadHash) return json({ ok: false, error: "payload_hash_mismatch" }, 422);

    const db = getDb(env);
    const archive = await verifyResearchArchive(db, run.run_id);
    const validation = validatePublication(run, archive);
    const publication = await publishQuantRun(db, run, payloadHash);
    await verifyCommittedPublication(db, run.run_id, payloadHash);

    return json({
      ok: true,
      run_id: run.run_id,
      payload_hash: payloadHash,
      publication_integrity: "verified",
      idempotent: publication.idempotent,
      validation,
      research_payload_hash: archive.payload_hash,
      research_manifest_hash: archive.manifest_hash,
      research_integrity: archive.integrity,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const status = error instanceof ArchiveIntegrityError || error instanceof PublicationIntegrityError ? error.status : 500;
    return json({ ok: false, error: message }, status);
  }
}

export async function onRequest(context) {
  if (context.request.method === "POST") return onRequestPost(context);
  return json({ ok: false, error: "method_not_allowed" }, 405);
}
