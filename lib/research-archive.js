const encoder = new TextEncoder();

export class ArchiveIntegrityError extends Error {
  constructor(message, status = 409) {
    super(message);
    this.name = "ArchiveIntegrityError";
    this.status = status;
  }
}

export function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function sha256(value) {
  const bytes = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function normalizedResearchRow(source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    throw new ArchiveIntegrityError("invalid_research_row", 422);
  }
  const row = { ...source };
  delete row.row_hash;
  const symbol = String(row.symbol || "").trim();
  if (!symbol) throw new ArchiveIntegrityError("research_symbol_required", 422);
  row.symbol = symbol;
  return row;
}

export async function buildResearchSeal(sources) {
  if (!Array.isArray(sources)) throw new ArchiveIntegrityError("research_rows_required", 422);
  const rows = sources.map(normalizedResearchRow).sort((a, b) => a.symbol.localeCompare(b.symbol));
  const seen = new Set();
  const sealedRows = [];
  for (const row of rows) {
    if (seen.has(row.symbol)) throw new ArchiveIntegrityError(`duplicate_research_symbol:${row.symbol}`, 422);
    seen.add(row.symbol);
    sealedRows.push({ symbol: row.symbol, row, row_hash: await sha256(stable(row)) });
  }
  const manifest = sealedRows.map(({ symbol, row_hash }) => ({ symbol, row_hash }));
  return {
    rows: sealedRows,
    row_count: sealedRows.length,
    payload_hash: await sha256(stable(sealedRows.map(({ row }) => row))),
    manifest_hash: await sha256(stable(manifest)),
  };
}

async function tableColumns(db, table) {
  const result = await db.prepare(`PRAGMA table_info(${table})`).all();
  return new Set((result.results || []).map((row) => String(row.name)));
}

async function addMissingColumns(db, table, definitions) {
  const columns = await tableColumns(db, table);
  for (const [column, definition] of definitions) {
    if (!columns.has(column)) await db.prepare(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`).run();
  }
}

export async function ensureResearchSchema(db) {
  await db.prepare("CREATE TABLE IF NOT EXISTS research_archives (run_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'started', expected_symbols INTEGER NOT NULL DEFAULT 0, received_symbols INTEGER NOT NULL DEFAULT 0, payload_hash TEXT, manifest_hash TEXT, archived_at TEXT, schema_version INTEGER NOT NULL DEFAULT 2, updated_at TEXT NOT NULL DEFAULT '')").run();
  await db.prepare("CREATE TABLE IF NOT EXISTS research_rows (run_id TEXT NOT NULL, symbol TEXT NOT NULL, row_hash TEXT NOT NULL DEFAULT '', row_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (run_id, symbol))").run();
  await addMissingColumns(db, "research_archives", [
    ["status", "TEXT NOT NULL DEFAULT 'started'"],
    ["expected_symbols", "INTEGER NOT NULL DEFAULT 0"],
    ["received_symbols", "INTEGER NOT NULL DEFAULT 0"],
    ["payload_hash", "TEXT"],
    ["manifest_hash", "TEXT"],
    ["archived_at", "TEXT"],
    ["schema_version", "INTEGER NOT NULL DEFAULT 2"],
    ["updated_at", "TEXT NOT NULL DEFAULT ''"],
  ]);
  await addMissingColumns(db, "research_rows", [
    ["row_hash", "TEXT NOT NULL DEFAULT ''"],
    ["row_json", "TEXT NOT NULL DEFAULT '{}'"],
  ]);
  await db.prepare("CREATE INDEX IF NOT EXISTS idx_research_rows_run ON research_rows(run_id)").run();
}

async function archiveRow(db, runId) {
  return db.prepare("SELECT run_id,status,expected_symbols,received_symbols,payload_hash,manifest_hash,archived_at,schema_version,updated_at FROM research_archives WHERE run_id=? LIMIT 1").bind(runId).first();
}

async function storedSeal(db, runId) {
  const result = await db.prepare("SELECT symbol,row_hash,row_json FROM research_rows WHERE run_id=? ORDER BY symbol").bind(runId).all();
  const stored = result.results || [];
  const sources = stored.map((item) => {
    try {
      const row = JSON.parse(item.row_json);
      if (!row || typeof row !== "object" || Array.isArray(row)) throw new Error("invalid");
      return row;
    } catch {
      throw new ArchiveIntegrityError(`invalid_stored_research_row:${item.symbol || "unknown"}`, 409);
    }
  });
  const seal = await buildResearchSeal(sources);
  const storedBySymbol = new Map(stored.map((item) => [String(item.symbol), String(item.row_hash || "")]));
  seal.row_hash_updates = seal.rows.filter((item) => storedBySymbol.get(item.symbol) !== item.row_hash);
  return seal;
}

async function updateRowHashes(db, runId, rows) {
  for (let index = 0; index < rows.length; index += 50) {
    const statements = rows.slice(index, index + 50).map((item) =>
      db.prepare("UPDATE research_rows SET row_hash=? WHERE run_id=? AND symbol=?").bind(item.row_hash, runId, item.symbol));
    if (statements.length) await db.batch(statements);
  }
}

function archiveMetadata(archive, integrity = "pending", extra = {}) {
  return {
    run_id: String(archive.run_id),
    status: String(archive.status),
    expected_symbols: Number(archive.expected_symbols || 0),
    received_symbols: Number(archive.received_symbols || 0),
    payload_hash: archive.payload_hash || null,
    manifest_hash: archive.manifest_hash || null,
    archived_at: archive.archived_at || null,
    schema_version: Number(archive.schema_version || 2),
    integrity,
    ...extra,
  };
}

export async function startResearchArchive(db, runId, expectedSymbols) {
  await ensureResearchSchema(db);
  const expected = Number(expectedSymbols);
  if (!Number.isInteger(expected) || expected <= 0) throw new ArchiveIntegrityError("expected_symbols_must_be_positive_integer", 422);
  const current = await archiveRow(db, runId);
  if (current?.status === "archived") {
    const verified = await verifyResearchArchive(db, runId);
    if (verified.expected_symbols !== expected) throw new ArchiveIntegrityError(`research_archive_conflict:expected_symbols:${verified.expected_symbols}/${expected}`, 409);
    return { ...verified, status: "already_archived", idempotent: true };
  }
  if (current) {
    if (String(current.status) !== "started") throw new ArchiveIntegrityError(`research_archive_invalid_state:${current.status}`, 409);
    if (Number(current.expected_symbols) !== expected) throw new ArchiveIntegrityError(`research_archive_conflict:expected_symbols:${current.expected_symbols}/${expected}`, 409);
    const count = await db.prepare("SELECT COUNT(*) AS count FROM research_rows WHERE run_id=?").bind(runId).first();
    await db.prepare("UPDATE research_archives SET received_symbols=?, updated_at=datetime('now') WHERE run_id=?").bind(Number(count?.count || 0), runId).run();
    return { ...archiveMetadata({ ...current, received_symbols: Number(count?.count || 0) }), status: "archive_started", idempotent: true };
  }
  await db.prepare("INSERT INTO research_archives(run_id,status,expected_symbols,received_symbols,payload_hash,manifest_hash,archived_at,schema_version,updated_at) VALUES(?, 'started', ?, 0, NULL, NULL, NULL, 2, datetime('now'))")
    .bind(runId, expected).run();
  return { run_id: runId, status: "archive_started", expected_symbols: expected, received_symbols: 0, payload_hash: null, manifest_hash: null, archived_at: null, schema_version: 2, integrity: "pending", idempotent: false };
}

export async function putResearchBatch(db, runId, sources) {
  await ensureResearchSchema(db);
  if (!Array.isArray(sources)) throw new ArchiveIntegrityError("research_rows_required", 422);
  if (sources.length > 100) throw new ArchiveIntegrityError("batch_too_large:max_100", 422);
  const current = await archiveRow(db, runId);
  if (!current) throw new ArchiveIntegrityError("archive_not_started", 409);
  const incoming = await buildResearchSeal(sources);

  if (String(current.status) === "archived") {
    const verified = await verifyResearchArchive(db, runId);
    for (const item of incoming.rows) {
      const existing = await db.prepare("SELECT row_hash,row_json FROM research_rows WHERE run_id=? AND symbol=? LIMIT 1").bind(runId, item.symbol).first();
      if (!existing) throw new ArchiveIntegrityError(`research_archive_immutable:${item.symbol}`, 409);
      let storedHash = String(existing.row_hash || "");
      if (!storedHash) {
        try { storedHash = await sha256(stable(JSON.parse(existing.row_json))); } catch { storedHash = ""; }
      }
      if (storedHash !== item.row_hash) throw new ArchiveIntegrityError(`research_archive_immutable:${item.symbol}`, 409);
    }
    return { ...verified, status: "already_archived", accepted_rows: incoming.row_count, idempotent: true };
  }
  if (String(current.status) !== "started") throw new ArchiveIntegrityError(`research_archive_invalid_state:${current.status}`, 409);

  for (let index = 0; index < incoming.rows.length; index += 50) {
    const statements = incoming.rows.slice(index, index + 50).map((item) =>
      db.prepare("INSERT INTO research_rows(run_id,symbol,row_hash,row_json) VALUES(?,?,?,?) ON CONFLICT(run_id,symbol) DO UPDATE SET row_hash=excluded.row_hash,row_json=excluded.row_json")
        .bind(runId, item.symbol, item.row_hash, JSON.stringify(item.row)));
    if (statements.length) await db.batch(statements);
  }
  const count = await db.prepare("SELECT COUNT(*) AS count FROM research_rows WHERE run_id=?").bind(runId).first();
  const received = Number(count?.count || 0);
  await db.prepare("UPDATE research_archives SET received_symbols=?, updated_at=datetime('now') WHERE run_id=?").bind(received, runId).run();
  if (received > Number(current.expected_symbols)) throw new ArchiveIntegrityError(`archive_overflow:${received}/${current.expected_symbols}`, 422);
  return { run_id: runId, status: "archive_started", expected_symbols: Number(current.expected_symbols), received_symbols: received, accepted_rows: incoming.row_count, integrity: "pending", idempotent: false };
}

export async function commitResearchArchive(db, runId) {
  await ensureResearchSchema(db);
  const current = await archiveRow(db, runId);
  if (!current) throw new ArchiveIntegrityError("archive_not_started", 409);
  if (String(current.status) === "archived") {
    const verified = await verifyResearchArchive(db, runId);
    return { ...verified, status: "already_archived", idempotent: true };
  }
  if (String(current.status) !== "started") throw new ArchiveIntegrityError(`research_archive_invalid_state:${current.status}`, 409);

  const seal = await storedSeal(db, runId);
  const expected = Number(current.expected_symbols || 0);
  if (expected <= 0 || seal.row_count !== expected) throw new ArchiveIntegrityError(`archive_incomplete:${seal.row_count}/${expected}`, 422);
  await updateRowHashes(db, runId, seal.row_hash_updates);
  await db.prepare("UPDATE research_archives SET status='archived',received_symbols=?,payload_hash=?,manifest_hash=?,archived_at=datetime('now'),schema_version=2,updated_at=datetime('now') WHERE run_id=?")
    .bind(seal.row_count, seal.payload_hash, seal.manifest_hash, runId).run();
  return {
    run_id: runId,
    status: "archived",
    expected_symbols: expected,
    received_symbols: seal.row_count,
    payload_hash: seal.payload_hash,
    manifest_hash: seal.manifest_hash,
    archived_at: new Date().toISOString(),
    schema_version: 2,
    integrity: "verified",
    idempotent: false,
  };
}

export async function verifyResearchArchive(db, runId) {
  await ensureResearchSchema(db);
  let current = await archiveRow(db, runId);
  if (!current) throw new ArchiveIntegrityError("research_archive_required", 409);
  if (String(current.status) !== "archived") throw new ArchiveIntegrityError(`research_archive_not_archived:${current.status}`, 409);

  const seal = await storedSeal(db, runId);
  let expected = Number(current.expected_symbols || 0);
  const legacyUnsealed = !current.payload_hash || !current.manifest_hash;
  if (legacyUnsealed && expected <= 0) expected = seal.row_count;
  if (expected !== seal.row_count) throw new ArchiveIntegrityError(`research_archive_count_mismatch:${seal.row_count}/${expected}`, 409);

  if (legacyUnsealed) {
    await updateRowHashes(db, runId, seal.row_hash_updates);
    await db.prepare("UPDATE research_archives SET expected_symbols=?,received_symbols=?,payload_hash=?,manifest_hash=?,archived_at=COALESCE(NULLIF(archived_at,''),NULLIF(updated_at,''),datetime('now')),schema_version=2,updated_at=datetime('now') WHERE run_id=?")
      .bind(expected, seal.row_count, seal.payload_hash, seal.manifest_hash, runId).run();
    current = await archiveRow(db, runId);
  } else {
    if (String(current.payload_hash) !== seal.payload_hash) throw new ArchiveIntegrityError("research_archive_payload_hash_mismatch", 409);
    if (String(current.manifest_hash) !== seal.manifest_hash) throw new ArchiveIntegrityError("research_archive_manifest_hash_mismatch", 409);
    await updateRowHashes(db, runId, seal.row_hash_updates);
    if (Number(current.received_symbols) !== seal.row_count || !current.archived_at || Number(current.schema_version || 0) !== 2) {
      await db.prepare("UPDATE research_archives SET received_symbols=?,archived_at=COALESCE(NULLIF(archived_at,''),NULLIF(updated_at,''),datetime('now')),schema_version=2,updated_at=datetime('now') WHERE run_id=?")
        .bind(seal.row_count, runId).run();
      current = await archiveRow(db, runId);
    }
  }
  return archiveMetadata(current, "verified", { received_symbols: seal.row_count });
}

export async function getResearchArchiveStatus(db, runId) {
  await ensureResearchSchema(db);
  const current = await archiveRow(db, runId);
  if (!current) throw new ArchiveIntegrityError("research_archive_not_found", 404);
  if (String(current.status) === "archived") return verifyResearchArchive(db, runId);
  const count = await db.prepare("SELECT COUNT(*) AS count FROM research_rows WHERE run_id=?").bind(runId).first();
  return archiveMetadata({ ...current, received_symbols: Number(count?.count || 0) }, "pending");
}
