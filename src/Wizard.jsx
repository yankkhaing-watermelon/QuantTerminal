import React, { useMemo, useState } from "react";
import "./wizard.css";

const clamp = (value, low = 0, high = 100) => Math.max(low, Math.min(high, Number(value) || 0));
const num = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
const money = (value) => Number.isFinite(Number(value)) ? `RM ${Number(value).toFixed(Number(value) < 1 ? 3 : 2)}` : "RM —";
const BUY_ACTIONS = new Set(["BUY", "BUY CANDIDATE", "ADD"]);
const DEFENSIVE_ACTIONS = new Set(["TRIM", "SELL", "AVOID"]);

const regimeFit = {
  "STRONG RISK-ON": 100,
  "RISK-ON": 85,
  "NEUTRAL": 60,
  "RISK-OFF": 30,
  "STRONG RISK-OFF": 10,
};

function fallbackActivity(row, activity) {
  const published = activity.get(String(row.symbol));
  if (published) return clamp(published.activity_score);
  return clamp(Math.max(Math.abs(Number(row.price_z20) || 0), Math.abs(Number(row.volume_z20) || 0)) * 20);
}

function previewCandidates(data) {
  const activity = new Map((data.unexplained_activity || data.abnormal_activity || []).map((row) => [String(row.symbol), row]));
  const regime = data.regime?.state || "NEUTRAL";
  return (data.stocks || []).map((row) => {
    const activityScore = fallbackActivity(row, activity);
    const trend = Number(row.trend_score) || 0;
    const momentum = Number(row.momentum_score) || 0;
    const rs = clamp(50 + (Number(row.rs_20d) || 0) * 4);
    const volume = clamp(45 + ((Number(row.volume_ratio) || 1) - 1) * 35);
    const breakout = row.new_20d_high ? 100 : row.above_20dma && row.above_50dma ? 75 : 35;
    const w1 = clamp(trend * .30 + momentum * .30 + breakout * .15 + volume * .10 + rs * .15);
    const w3 = clamp(activityScore * .40 + (Number(row.quant_score) || 0) * .20 + (Number(row.quality_score) || 0) * .15 + volume * .15 + 10);
    const setupCode = w1 >= w3 ? "W1" : "W3";
    const setupName = setupCode === "W1" ? "Momentum Breakout" : "In-Play Opportunity";
    const setupQuality = Math.max(w1, w3);
    const wizardScore = clamp(setupQuality * .30 + (Number(row.quant_score) || 0) * .25 + rs * .15 + activityScore * .10 + (regimeFit[regime] ?? 60) * .10 + clamp((Number(row.expected_edge) || 0) * 8) * .10);
    const confirmed = setupCode === "W1"
      ? Boolean(row.new_20d_high && Number(row.volume_ratio) >= 1.1 && Number(row.rs_20d) > 0 && Number(row.quant_score) >= 67)
      : Boolean(activityScore >= 65 && Number(row.price_z20) > 0 && Number(row.volume_ratio) >= 1.1);
    const atr = Number(row.atr) || 0;
    const close = Number(row.close) || 0;
    const stop = Math.max(.001, close - 2.5 * atr);
    const riskPct = close > 0 ? Math.max(.1, (close - stop) / close * 100) : 0;
    const rr = riskPct > 0 ? Math.max(0, Number(row.expected_edge) || 0) / riskPct : 0;
    const liquidityPass = Number(row.turnover) >= 500000 && Number(row.quality_score) >= 30;
    const riskPass = Number(row.volatility) <= 100 && (close > 0 ? atr / close : 1) <= .12;
    let action = "WATCH";
    if (!liquidityPass || !riskPass || regime === "STRONG RISK-OFF" || Number(row.expected_edge) <= 0) action = "AVOID";
    else if (confirmed && wizardScore >= 82 && rr >= .8) action = "BUY";
    else if (wizardScore >= 72 && rr >= .6 && !["RISK-OFF", "STRONG RISK-OFF"].includes(regime)) action = "BUY CANDIDATE";
    return {
      ...row,
      wizard_score: wizardScore,
      activity_score: activityScore,
      setup_code: setupCode,
      setup_name: setupName,
      setup_quality: setupQuality,
      entry_confirmed: confirmed,
      action,
      liquidity_gate: liquidityPass ? "PASS" : "FAIL",
      risk_gate: riskPass ? "PASS" : "FAIL",
      regime,
      levels: {
        entry_trigger: confirmed ? close : close + .5 * atr,
        initial_stop: stop,
        first_trim: close * (1 + Math.max(Number(row.expected_edge) || 0, close > 0 ? 4 * atr / close * 100 : 0) / 100),
        model_reward_risk: rr,
      },
      reasons: [
        setupCode === "W1" ? "trend / breakout strength" : "unusual price / volume activity",
        Number(row.rs_20d) > 0 ? "positive relative strength" : "relative strength not yet positive",
        confirmed ? "entry confirmation present" : "entry confirmation pending",
      ],
      _preview: true,
    };
  }).sort((a, b) => Number(b.wizard_score) - Number(a.wizard_score));
}

function mergeCandidateSources(data) {
  const published = Array.isArray(data.wizard_candidates) ? data.wizard_candidates : [];
  const preview = previewCandidates(data);
  const bySymbol = new Map();
  for (const row of published) bySymbol.set(String(row.symbol), row);
  for (const row of preview) if (!bySymbol.has(String(row.symbol))) bySymbol.set(String(row.symbol), row);
  return [...bySymbol.values()].sort((a, b) => Number(b.wizard_score) - Number(a.wizard_score));
}

function ActionBadge({ action }) {
  const key = String(action || "WATCH").toLowerCase().replaceAll(" ", "-");
  return <span className={`wizard-action ${key}`}>{action || "WATCH"}</span>;
}

export default function Wizard({ data }) {
  const [limit, setLimit] = useState(15);
  const [filter, setFilter] = useState("BUY");
  const source = useMemo(() => mergeCandidateSources(data), [data]);
  const buyRows = source.filter((row) => BUY_ACTIONS.has(row.action));
  const watchRows = source.filter((row) => row.action === "WATCH");
  const defensiveRows = source.filter((row) => DEFENSIVE_ACTIONS.has(row.action));
  const filtered = filter === "BUY" ? buyRows : filter === "WATCH" ? watchRows : filter === "DEFENSIVE" ? defensiveRows : source;
  const rows = filtered.slice(0, limit);

  return <>
    <section className="wizard-hero">
      <div>
        <span className="eyebrow">MW-INSPIRED DECISION LAYER</span>
        <h3>Top Bursa buy recommendations</h3>
        <p>Default view only shows BUY, BUY CANDIDATE and ADD names that pass the decision gates. AVOID stocks never fill the Top 15.</p>
      </div>
      <div className="wizard-hero-score"><strong>{rows.length}</strong><span>shown</span><small>{filter === "BUY" ? "BUY FOCUS" : "REVIEW MODE"}</small></div>
    </section>

    <section className="wizard-stats">
      <div><span>Buy recommendations</span><strong>{buyRows.length}</strong></div>
      <div><span>Watch</span><strong>{watchRows.length}</strong></div>
      <div><span>Defensive / Avoid</span><strong>{defensiveRows.length}</strong></div>
      <div><span>Regime</span><strong className="wizard-regime">{data.regime?.state || "—"}</strong></div>
    </section>

    <section className="wizard-controls" aria-label="Wizard shortlist controls">
      <div className="wizard-limits" role="group" aria-label="Candidate limit">{[10, 15, 20].map((value) => <button type="button" key={value} className={limit === value ? "active" : ""} onClick={() => setLimit(value)}>TOP {value}</button>)}</div>
      <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter Wizard action">
        <option value="BUY">BUY RECOMMENDATIONS</option>
        <option value="WATCH">WATCH ONLY</option>
        <option value="DEFENSIVE">TRIM / SELL / AVOID</option>
        <option value="ALL">ALL MODEL OUTPUTS</option>
      </select>
    </section>

    <section className="wizard-list">
      {rows.length ? rows.map((row, index) => <article className="wizard-card" key={row.symbol}>
        <div className="wizard-rank">{String(index + 1).padStart(2, "0")}</div>
        <div className="wizard-main">
          <div className="wizard-identity">
            <div><h4>{row.name || row.sector || "Bursa Malaysia"}</h4><span>{row.tv_symbol || row.symbol} · {row.sector || "MYX"}</span></div>
            <ActionBadge action={row.action}/>
          </div>
          <div className="wizard-setup"><b>{row.setup_code}</b><span>{row.setup_name}</span>{row.entry_confirmed ? <em>ENTRY CONFIRMED</em> : <em className="pending">WAITING ENTRY</em>}</div>
          <div className="wizard-score-grid">
            <div><span>Wizard</span><strong>{num(row.wizard_score, 0)}</strong></div>
            <div><span>Quant</span><strong>{num(row.quant_score, 0)}</strong></div>
            <div><span>Activity</span><strong>{num(row.activity_score, 0)}</strong></div>
            <div><span>R:R</span><strong>{num(row.levels?.model_reward_risk, 2)}x</strong></div>
          </div>
          <div className="wizard-levels">
            <div><span>Close</span><b>{money(row.close)}</b></div>
            <div><span>Entry</span><b>{money(row.levels?.entry_trigger)}</b></div>
            <div><span>Stop</span><b>{money(row.levels?.initial_stop)}</b></div>
            <div><span>1st Trim</span><b>{money(row.levels?.first_trim)}</b></div>
          </div>
          <div className="wizard-gates"><span className={row.liquidity_gate === "PASS" ? "pass" : "fail"}>LIQ {row.liquidity_gate || "—"}</span><span className={row.risk_gate === "PASS" ? "pass" : "fail"}>RISK {row.risk_gate || "—"}</span><span>{row.regime || data.regime?.state || "—"}</span></div>
          <div className="wizard-reasons">{(row.reasons || []).map((reason) => <span key={reason}>{reason}</span>)}</div>
        </div>
      </article>) : <div className="wizard-empty">No stocks currently meet the buy recommendation gates. The list is intentionally left short rather than padded with AVOID names.</div>}
    </section>

    <p className="wizard-disclaimer">MW-inspired decision-support output using the latest published daily Quant snapshot. The default Top 10/15/20 view is buy-focused and excludes AVOID / SELL / TRIM names. Prices are latest published daily snapshots, not intraday ticks.</p>
  </>;
}
