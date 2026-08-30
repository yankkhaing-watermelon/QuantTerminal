import { sha256, stable } from "./research-archive.js";

export const MIN_PRODUCTION_UNIVERSE = 900;
export const MIN_FRESH_COVERAGE = 0.70;

export class PublicationIntegrityError extends Error {
  constructor(message, status = 422) {
    super(message);
    this.name = "PublicationIntegrityError";
    this.status = status;
  }
}

function integer(value, name) {
  const number = Number(value);
  if (!Number.isInteger(number)) throw new PublicationIntegrityError(`${name}_must_be_integer`);
  return number;
}

function finite(value, name) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new PublicationIntegrityError(`${name}_must_be_finite`);
  return number;
}

function requiredString(value, name) {
  const text = String(value ?? "").trim();
  if (!text) throw new PublicationIntegrityError(`${name}_required`);
  return text;
}

function validIsoDate(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T00:00:00Z`));
}

function validHexHash(value) {
  return /^[0-9a-f]{64}$/.test(String(value || ""));
}

export function validatePublication(run, archive) {
  if (!run || typeof run !== "object" || Array.isArray(run)) {
    throw new PublicationIntegrityError("invalid_quant_payload");
  }

  const runId = requiredString(run.run_id, "run_id");
  const scanDate = requiredString(run.scan_date, "scan_date");
  const generatedAt = requiredString(run.generated_at, "generated_at");
  if (!validIsoDate(scanDate)) throw new PublicationIntegrityError("invalid_scan_date");
  if (!Number.isFinite(Date.parse(generatedAt))) throw new PublicationIntegrityError("invalid_generated_at");
  const runMatch = runId.match(/^qv5-(\d{4}-\d{2}-\d{2})-([0-9a-f]{12})$/);
  if (!runMatch) throw new PublicationIntegrityError("invalid_content_addressed_run_id");
  if (runMatch[1] !== scanDate) throw new PublicationIntegrityError("run_id_scan_date_mismatch");

  const version = requiredString(run.version, "version");
  if (!version.startsWith("5.")) throw new PublicationIntegrityError(`unsupported_quant_version:${version}`);
  if (String(run.market || "") !== "MYX") throw new PublicationIntegrityError("market_must_be_MYX");
  if (String(run.benchmark || "") !== "^KLSE") throw new PublicationIntegrityError("benchmark_must_be_KLSE");

  const universe = integer(run.universe_size, "universe_size");
  const fresh = integer(run.fresh_symbols, "fresh_symbols");
  if (universe < MIN_PRODUCTION_UNIVERSE) {
    throw new PublicationIntegrityError(`production_universe_too_small:${universe}<${MIN_PRODUCTION_UNIVERSE}`);
  }
  if (fresh < MIN_PRODUCTION_UNIVERSE) {
    throw new PublicationIntegrityError(`production_fresh_universe_too_small:${fresh}<${MIN_PRODUCTION_UNIVERSE}`);
  }
  if (fresh > universe) throw new PublicationIntegrityError(`fresh_symbols_exceed_universe:${fresh}>${universe}`);
  const minimumFresh = Math.ceil(universe * MIN_FRESH_COVERAGE);
  if (fresh < minimumFresh) throw new PublicationIntegrityError(`fresh_coverage_too_low:${fresh}<${minimumFresh}`);

  if (!Array.isArray(run.stocks) || run.stocks.length !== fresh) {
    throw new PublicationIntegrityError(`stocks_count_mismatch:${Array.isArray(run.stocks) ? run.stocks.length : 0}/${fresh}`);
  }
  const symbols = new Set();
  const allowedActions = new Set(["ADD", "HOLD", "WATCH", "TRIM", "REDUCE", "EXIT"]);
  for (let index = 0; index < run.stocks.length; index += 1) {
    const row = run.stocks[index];
    if (!row || typeof row !== "object" || Array.isArray(row)) throw new PublicationIntegrityError(`invalid_stock_row:${index}`);
    const symbol = requiredString(row.symbol, `stock_symbol_${index}`).toUpperCase();
    if (symbols.has(symbol)) throw new PublicationIntegrityError(`duplicate_stock_symbol:${symbol}`);
    symbols.add(symbol);
    const rank = integer(row.rank, `rank_${symbol}`);
    if (rank !== index + 1) throw new PublicationIntegrityError(`non_contiguous_rank:${symbol}:${rank}/${index + 1}`);
    const score = finite(row.quant_score, `quant_score_${symbol}`);
    if (score < 0 || score > 100) throw new PublicationIntegrityError(`quant_score_out_of_range:${symbol}:${score}`);
    if (!allowedActions.has(String(row.action || ""))) throw new PublicationIntegrityError(`invalid_action:${symbol}:${row.action}`);
  }

  if (!archive || archive.integrity !== "verified" || archive.status !== "archived") {
    throw new PublicationIntegrityError("research_archive_not_verified", 409);
  }
  const archiveExpected = integer(archive.expected_symbols, "archive_expected_symbols");
  const archiveReceived = integer(archive.received_symbols, "archive_received_symbols");
  if (archiveExpected !== fresh || archiveReceived !== fresh) {
    throw new PublicationIntegrityError(`research_archive_universe_mismatch:${archiveExpected}/${archiveReceived}/${fresh}`, 409);
  }
  if (!validHexHash(archive.payload_hash) || !validHexHash(archive.manifest_hash)) {
    throw new PublicationIntegrityError("research_archive_hash_invalid", 409);
  }

  const methodology = run.methodology || {};
  if (methodology.price_adjustment !== "unadjusted") throw new PublicationIntegrityError("price_adjustment_must_be_unadjusted");
  if (String(methodology.session_gate || "") !== scanDate) throw new PublicationIntegrityError("session_gate_scan_date_mismatch");

  if (!Array.isArray(run.unexplained_activity)) throw new PublicationIntegrityError("unexplained_activity_required");
  if (run.abnormal_activity !== undefined && stable(run.abnormal_activity) !== stable(run.unexplained_activity)) {
    throw new PublicationIntegrityError("activity_alias_mismatch");
  }
  const activitySymbols = new Set();
  const activityLevels = new Set(["ELEVATED", "HIGH", "VERY HIGH"]);
  for (const row of run.unexplained_activity) {
    const symbol = requiredString(row?.symbol, "activity_symbol").toUpperCase();
    if (!symbols.has(symbol)) throw new PublicationIntegrityError(`activity_symbol_not_in_universe:${symbol}`);
    if (activitySymbols.has(symbol)) throw new PublicationIntegrityError(`duplicate_activity_symbol:${symbol}`);
    activitySymbols.add(symbol);
    const score = finite(row.activity_score, `activity_score_${symbol}`);
    if (score < 0 || score > 100) throw new PublicationIntegrityError(`activity_score_out_of_range:${symbol}:${score}`);
    if (!activityLevels.has(String(row.activity_level || ""))) throw new PublicationIntegrityError(`invalid_activity_level:${symbol}:${row.activity_level}`);
  }

  if (Array.isArray(run.portfolio)) {
    const portfolioSymbols = new Set();
    let targetWeight = 0;
    for (const row of run.portfolio) {
      const symbol = requiredString(row?.symbol, "portfolio_symbol").toUpperCase();
      if (!symbols.has(symbol)) throw new PublicationIntegrityError(`portfolio_symbol_not_in_universe:${symbol}`);
      if (portfolioSymbols.has(symbol)) throw new PublicationIntegrityError(`duplicate_portfolio_symbol:${symbol}`);
      portfolioSymbols.add(symbol);
      if (row.target_weight !== undefined && row.target_weight !== null) {
        const weight = finite(row.target_weight, `target_weight_${symbol}`);
        if (weight < 0 || weight > 0.15000001) throw new PublicationIntegrityError(`target_weight_out_of_range:${symbol}:${weight}`);
        targetWeight += weight;
      }
    }
    if (targetWeight > 1.000001) throw new PublicationIntegrityError(`portfolio_target_weight_exceeds_one:${targetWeight}`);
  }

  return {
    run_id: runId,
    scan_date: scanDate,
    universe_size: universe,
    fresh_symbols: fresh,
    stock_count: symbols.size,
    activity_count: activitySymbols.size,
    research_payload_hash: archive.payload_hash,
    research_manifest_hash: archive.manifest_hash,
  };
}

export async function inspectStoredPayload(row, { requireDeclaredHash = true } = {}) {
  if (!row?.payload_json) throw new PublicationIntegrityError("stored_quant_payload_missing", 503);
  let payload;
  try {
    payload = JSON.parse(row.payload_json);
  } catch {
    throw new PublicationIntegrityError("stored_quant_payload_invalid_json", 503);
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new PublicationIntegrityError("stored_quant_payload_invalid_shape", 503);
  }
  const computedHash = await sha256(stable(payload));
  const declaredHash = String(row.payload_hash || "");
  if (requireDeclaredHash && !validHexHash(declaredHash)) {
    throw new PublicationIntegrityError("stored_quant_payload_unsealed", 503);
  }
  if (declaredHash && declaredHash !== computedHash) {
    throw new PublicationIntegrityError("stored_quant_payload_hash_mismatch", 503);
  }
  if (row.run_id && String(payload.run_id || "") !== String(row.run_id)) {
    throw new PublicationIntegrityError("stored_quant_run_id_mismatch", 503);
  }
  return { payload, payload_hash: computedHash, declared_hash: declaredHash || null };
}

export async function assertImmutablePublication(existing, incomingHash) {
  const stored = await inspectStoredPayload(existing, { requireDeclaredHash: false });
  if (stored.declared_hash && stored.declared_hash !== stored.payload_hash) {
    throw new PublicationIntegrityError("stored_quant_payload_hash_mismatch", 409);
  }
  if (stored.payload_hash !== incomingHash) {
    throw new PublicationIntegrityError(`immutable_quant_run_conflict:${existing.run_id || "unknown"}`, 409);
  }
  return { ...stored, idempotent: true };
}
