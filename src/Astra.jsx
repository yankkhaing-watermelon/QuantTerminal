import React, { useEffect, useState } from "react";
import "./astra.css";

const INITIAL = { capital: 100000, risk_pct: .5, max_positions: 8, fee_bps: 20, minimum_fee: 8, slippage_bps: 10, breadth_filter: false };
const n = (value, digits = 1) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("en-MY", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const money = (value) => value == null ? "—" : `RM ${n(value, value < 1 ? 3 : 2)}`;
const pct = (value) => value == null ? "—" : `${n(value)}%`;
const timestamp = (value) => value ? new Intl.DateTimeFormat("en-MY", { timeZone: "Asia/Kuala_Lumpur", dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "No publication yet";

function Metric({ label, value, detail }) {
  return <div className="astra-metric"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
}

function Equity({ points }) {
  if (!points?.length) return <p>No simulation history is available.</p>;
  const values = points.map((p) => p.equity), low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
  const line = points.map((p, i) => `${i ? "L" : "M"}${50 + i / Math.max(1, points.length - 1) * 690},${170 - (p.equity - low) / span * 140}`).join(" ");
  return <figure className="astra-equity"><figcaption>Portfolio equity after estimated costs · MYR</figcaption><svg viewBox="0 0 760 210" role="img" aria-label={`Portfolio equity from ${points[0].date} to ${points.at(-1).date}; final ${money(points.at(-1).equity)}`}>
    <path d="M50 25V175H740" className="astra-chart-axis"/><path d={line} className="astra-chart-line"/>
    <text x="50" y="17">{n(high, 0)}</text><text x="50" y="193">{points[0].date}</text><text x="740" y="193" textAnchor="end">{points.at(-1).date}</text>
  </svg><div className="astra-chart-range"><span>Low {money(low)}</span><span>High {money(high)}</span><span>Final {money(points.at(-1).equity)}</span></div></figure>;
}

function download(result, key, runId) {
  const fields = ["symbol", "name", "sector", "signal_date", "entry_date", "exit_date", "entry", "exit", "shares", "initial_stop", "pnl", "r", "fees", "reason"];
  const quote = (value) => `"${(typeof value === "number" ? String(value) : String(value ?? "").replace(/^[=+\-@\t\r]/, "'$&")).replaceAll('"', '""')}"`;
  const csv = [fields.join(","), ...result.trades.map((row) => fields.map((field) => quote(row[field])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${runId}-${key}-trades.csv`; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function Astra() {
  const [payload, setPayload] = useState(null), [job, setJob] = useState(null);
  const [error, setError] = useState(""), [loading, setLoading] = useState(true), [sending, setSending] = useState(false);
  const [strategy, setStrategy] = useState("breakout"), [view, setView] = useState("signals"), [search, setSearch] = useState("");
  const [settings, setSettings] = useState(INITIAL), [notice, setNotice] = useState("");
  useEffect(() => {
    let live = true;
    const refresh = async () => {
      try {
        const response = await fetch(`/api/astra?t=${Date.now()}`, { cache: "no-store" });
        const body = await response.json();
        if (!response.ok || !body.ok) throw new Error(body.error || "Astra data could not be loaded.");
        if (live) { setPayload(body.data); setJob(body.job); setError(""); }
      } catch (err) { if (live) setError(err.message || "Astra is unavailable. Try again shortly."); }
      finally { if (live) setLoading(false); }
    };
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => { live = false; clearInterval(timer); };
  }, []);
  const busy = sending || ["queued", "running"].includes(job?.state);
  async function run(event) {
    event.preventDefault(); setSending(true); setNotice("");
    try {
      const response = await fetch("/api/astra-run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ config: settings }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Astra could not be queued.");
      setJob({ state: "queued", processed: 0, total: 0, request_id: body.request_id });
      setNotice("Astra is queued. You can close the app; the scan continues on the server.");
    } catch (err) { setNotice(err.message); }
    finally { setSending(false); }
  }
  const result = payload?.strategies?.[strategy], coverage = payload?.coverage, history = payload?.history;
  const metrics = result?.metrics;
  const candidates = (result?.candidates || []).filter((r) => `${r.symbol} ${r.name} ${r.tv_symbol}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="astra">
    <div className="astra-heading"><div><span className="eyebrow">FULL BURSA UNIVERSE · TRADINGVIEW</span><h3>Trend research & portfolio backtest</h3><p>Find strong trends. Size the risk. Let the exits decide.</p></div><span className="astra-label">RESEARCH v1</span></div>
    <form onSubmit={run} className="astra-settings">
      <div className="astra-fields">
        <label>Starting capital (RM)<input type="number" min="1000" max="100000000" step="1000" required value={settings.capital} onChange={(e) => setSettings({ ...settings, capital: Number(e.target.value) })}/></label>
        <label>Risk per trade (%)<input type="number" min="0.1" max="5" step="0.1" required value={settings.risk_pct} onChange={(e) => setSettings({ ...settings, risk_pct: Number(e.target.value) })}/></label>
        <label>Maximum holdings<input type="number" min="1" max="50" step="1" required value={settings.max_positions} onChange={(e) => setSettings({ ...settings, max_positions: Number(e.target.value) })}/></label>
        <button className="astra-primary" disabled={busy}>{busy ? "Astra running…" : "Run Astra scan & backtest"}</button>
      </div>
      <details><summary>Execution assumptions</summary><div className="astra-fields">
        <label>Combined fees per side (bps)<input type="number" min="0" max="250" value={settings.fee_bps} onChange={(e) => setSettings({ ...settings, fee_bps: Number(e.target.value) })}/></label>
        <label>Minimum fee per side (RM)<input type="number" min="0" value={settings.minimum_fee} onChange={(e) => setSettings({ ...settings, minimum_fee: Number(e.target.value) })}/></label>
        <label>Slippage per side (bps)<input type="number" min="0" max="250" value={settings.slippage_bps} onChange={(e) => setSettings({ ...settings, slippage_bps: Number(e.target.value) })}/></label>
        <label className="astra-check"><input type="checkbox" checked={settings.breadth_filter} onChange={(e) => setSettings({ ...settings, breadth_filter: e.target.checked })}/> Require breadth above 50%</label>
      </div><p>100 bps = 1%. Costs are estimates; replace them with assumptions suitable for your broker. The scheduled daily run uses the documented defaults.</p></details>
    </form>
    {(notice || job) && <div className="astra-status" role="status"><strong>{job?.state?.replaceAll("_", " ") || "Request"}</strong><span>{["running", "queued"].includes(job?.state) ? `${job.processed || 0} / ${job.total || "—"} stocks processed. ${job.message || "Waiting for the runner."}` : job?.message || notice}</span>{notice && <small>{notice}</small>}{job?.total > 0 && busy && <progress value={job.processed} max={job.total}/>}</div>}
    {error && <p className="astra-error" role="alert">{error}{payload && " Displaying the last successfully loaded publication."}</p>}
    {loading && <p role="status">Loading Astra publication…</p>}
    {!loading && !payload && <section className="astra-empty"><h4>Ready for the first Astra run</h4><p>Run Astra to discover every Bursa stock and request up to 1,500 daily bars per stock. Results will report actual coverage and available history. No backtest returns are assumed.</p></section>}
    {payload && <>
      <p className="astra-publication">Astra market date <b>{payload.scan_date}</b> · Published {timestamp(payload.generated_at)} MYT · {coverage.partial ? "Partial data coverage" : "Full data coverage"}</p>
      <div className="astra-metrics"><Metric label="Universe discovered" value={n(coverage.discovered, 0)} detail={`${n(coverage.attempted, 0)} attempted · ${coverage.excluded || 0} excluded`}/><Metric label="Fresh with history" value={n(coverage.fresh_with_history, 0)} detail={`${n(coverage.failed, 0)} unavailable · ${n(coverage.stale, 0)} stale`}/><Metric label="Liquidity eligible" value={n(payload.eligible_today, 0)} detail="Before trend and entry rules"/><Metric label="Backtest sessions" value={n(history.test_sessions, 0)} detail={`${history.start || "—"} → ${history.end}`}/></div>
      {(history.test_sessions < 756 || coverage.partial) && <p className="astra-caution">{history.test_sessions < 756 ? "Fewer than three years of test sessions are available. " : ""}{coverage.partial ? "Some stocks could not be fully analysed. " : ""}These results are provisional and include current-universe survivorship bias.</p>}
      <div className="astra-switch" role="group" aria-label="Astra strategies">{Object.entries(payload.strategies).map(([key, value]) => <button key={key} aria-pressed={strategy === key} className={strategy === key ? "active" : ""} onClick={() => setStrategy(key)}>{value.name}<small>{value.candidates.length} signals</small></button>)}</div>
      <div className="astra-subnav" role="group" aria-label="Astra views">{["signals", "backtest", "trades", "method"].map((tab) => <button key={tab} aria-pressed={view === tab} className={view === tab ? "active" : ""} onClick={() => setView(tab)}>{tab === "method" ? "Rules & coverage" : tab}</button>)}</div>
      {view === "signals" && <section className="astra-panel"><div className="astra-panel-head"><h4>{candidates.length} qualifying signals</h4><input aria-label="Search Astra stocks" placeholder="Code or company" value={search} onChange={(e) => setSearch(e.target.value)}/></div><p>Signals use the completed close. Entry is evaluated next session, subject to available cash and risk limits. Reference stops are recalculated from the actual entry.</p>
        {!candidates.length ? <p className="astra-empty">No stocks match this strategy and search for the published session. Cash is a valid outcome.</p> : <div className="astra-candidates">{candidates.map((r) => <article key={r.symbol}><div><small>#{r.rank} · {r.symbol} · {r.tv_symbol}</small><h4>{r.name}</h4><span>{r.sector}</span></div><dl><dt>Close</dt><dd>{money(r.close)}</dd><dt>Reference stop</dt><dd>{money(r.reference_stop)}</dd><dt>126D momentum</dt><dd>{pct(r.momentum_pct)}</dd><dt>RS percentile</dt><dd>{n(r.rs_percentile)}</dd><dt>Median turnover</dt><dd>{money(r.median_turnover)}</dd></dl></article>)}</div>}
      </section>}
      {view === "backtest" && <>
        <p className="astra-publication">Published assumptions: {money(payload.config.capital)} · {payload.config.risk_pct}% risk · {payload.config.max_positions} holdings · {payload.config.fee_bps} bps fees + {payload.config.slippage_bps} bps slippage per side. Editing inputs changes the next run only.</p>
        <div className="astra-metrics"><Metric label="Net return" value={pct(metrics.return_pct)} detail={`CAGR ${pct(metrics.cagr_pct)}`}/><Metric label="Maximum drawdown" value={pct(metrics.max_drawdown_pct)}/><Metric label="Expectancy" value={metrics.expectancy_r == null ? "—" : `${n(metrics.expectancy_r, 2)} R`} detail={`${metrics.closed_trades} closed trades`}/><Metric label="Win rate" value={pct(metrics.win_rate)} detail={`Profit factor ${n(metrics.profit_factor, 2)}`}/></div>
        <section className="astra-panel"><Equity points={result.equity_curve}/><div className="astra-table-scroll"><table><caption>Fixed-rule robustness comparisons</caption><thead><tr><th>Period / costs</th><th>Return</th><th>Drawdown</th><th>Expectancy R</th><th>Closed trades</th></tr></thead><tbody>{[[payload.benchmark?.name || "KLCI price return", payload.benchmark || {}], ["Full available period", metrics], ["Final 30% · fresh starting cash", result.validation], ["Full period · doubled costs", result.double_cost]].map(([label, m]) => <tr key={label}><td>{label}</td><td>{pct(m.return_pct)}</td><td>{pct(m.max_drawdown_pct)}</td><td>{n(m.expectancy_r, 2)}</td><td>{m.closed_trades ?? "—"}</td></tr>)}</tbody></table></div><p>Final-period testing starts {result.validation.start || "—"} with no inherited holdings. It is a chronological diagnostic, not untouched prospective validation.</p></section>
        <section className="astra-panel"><h4>Trade behaviour</h4><div className="astra-metrics"><Metric label="Average winner" value={`${n(metrics.average_win_r, 2)} R`}/><Metric label="Average loser" value={`${n(metrics.average_loss_r, 2)} R`}/><Metric label="Longest losing streak" value={metrics.longest_losing_streak}/><Metric label="Average exposure" value={pct(metrics.average_exposure_pct)}/></div><div className="astra-yearly">{Object.entries(metrics.yearly_returns).map(([year, value]) => <span key={year}>{year}<b>{pct(value)}</b></span>)}</div><p>Longest period below a previous equity peak: {metrics.longest_underwater_sessions} sessions. First and last calendar years may be partial. Open positions are marked to market and included in equity, but excluded from closed-trade win rate.</p></section>
      </>}
      {view === "trades" && <section className="astra-panel"><div className="astra-panel-head"><h4>{metrics.closed_trades} closed trades · {metrics.open_positions} open</h4><button disabled={!result.trades.length} onClick={() => download(result, strategy, payload.run_id)}>Download all trades CSV</button></div><p>Showing the latest 100 closed trades. The CSV contains the complete ledger.</p><div className="astra-table-scroll"><table><thead><tr><th>Company</th><th>Entry date</th><th>Exit date</th><th>Entry</th><th>Exit</th><th>Shares</th><th>Net P/L</th><th>R</th></tr></thead><tbody>{result.trades.slice(-100).reverse().map((r, i) => <tr key={`${r.symbol}-${r.entry_date}-${i}`}><td><b>{r.name}</b><small>{r.symbol}</small></td><td>{r.entry_date}</td><td>{r.exit_date}</td><td>{money(r.entry)}</td><td>{money(r.exit)}</td><td>{n(r.shares, 0)}</td><td>{money(r.pnl)}</td><td>{n(r.r, 2)}</td></tr>)}</tbody></table></div>{!result.trades.length && <p>No closed trades in this period.</p>}<h4>Simulated open positions</h4><div className="astra-candidates">{result.open_positions.map((r) => <article key={r.symbol}><h4>{r.name}</h4><small>{r.symbol} · {r.shares} shares</small><p>Entry {money(r.entry)} · Last mark {money(r.mark)} · Stop {money(r.stop)}{r.exit_pending ? " · Exit awaiting liquidity" : ""}</p></article>)}</div></section>}
      {view === "method" && <section className="astra-panel"><h4>Versioned strategy rules</h4><ul><li>Both strategies: close &gt; SMA50 &gt; SMA200, SMA200 rising over 20 sessions, top 20% by 126-session momentum among liquidity-eligible stocks.</li><li>Liquidity: preceding 60-session median traded value ≥ {money(payload.config.min_turnover)}. Entry participation cap {payload.config.participation_pct}%.</li><li>Breakout: close above the preceding 55-session high. Pullback: close crosses back above SMA20 while the long trend remains intact.</li><li>Next-session opening execution; initial stop {payload.config.initial_atr} ATR below entry. Trailing stop: highest close since entry minus {payload.config.trailing_atr} ATR, raised only after the close for the following session.</li><li>100-share lots, {payload.config.max_position_pct}% position cap, {payload.config.max_sector_pct}% sector cap. No leverage, averaging down or fixed profit target.</li><li>Breadth gate {payload.config.breadth_filter ? "enabled: more than 50% above SMA200" : "disabled"}. Portfolio limits apply independently to each strategy.</li></ul><h4>Data & limitations</h4><p>Requested {coverage.requested_bars} bars per stock; actual minimum / median / maximum: {history.stock_bars_min} / {n(history.stock_bars_median, 0)} / {history.stock_bars_max}. No claim of ten-year coverage is made.</p><ul>{payload.limitations.map((text) => <li key={text}>{text}</li>)}</ul><p>A calculated stop is not a broker order. Gaps and unavailable liquidity can cause losses larger than the planned risk.</p><details><summary>{coverage.issues.length} stock data exceptions</summary><div className="astra-table-scroll"><table><thead><tr><th>Stock</th><th>Reason</th><th>Bars received</th></tr></thead><tbody>{coverage.issues.map((r) => <tr key={r.symbol}><td>{r.name} ({r.symbol})</td><td>{r.reason.replaceAll("_", " ")}</td><td>{r.bars}</td></tr>)}</tbody></table></div></details><small className="astra-hash">{payload.version} · {payload.run_id}<br/>Dataset SHA256: {coverage.data_hash}</small></section>}
    </>}
  </div>;
}
