import React, { useEffect, useMemo, useState } from "react";

const TABS = ["Overview", "Rankings", "Portfolio", "Regime", "Backtest", "Performance", "Research"];
const COLORS = { strong: "#38f2b0", positive: "#84e46d", neutral: "#ffcb45", negative: "#ff7b72", muted: "#7f91a8" };

const DEMO_DATA = {
  run_id: "demo-preview",
  scan_date: "—",
  generated_at: null,
  universe_size: 0,
  market: "MYX",
  benchmark: "^KLSE",
  regime: { state: "AWAITING DATA", score: 0, confidence: 0, components: {} },
  breadth: { above_20dma: 0, above_50dma: 0, above_200dma: 0, advance_decline: 0, volume_breadth: 0, sector_breadth: 0, participation: 0, dispersion: 0 },
  stocks: [], portfolio: [], research: [], abnormal_activity: [],
  backtest: { total_trades: 0, win_rate: 0, expectancy: 0, max_drawdown: 0, groups: [] },
  performance: { live_trades: 0, open_trades: 0, closed_trades: 0, hit_rate: 0, realized_return: 0, equity_curve: [] },
};

const num = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
const pct = (value, digits = 1) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits)}%` : "—";
const dateTime = (value) => value ? new Intl.DateTimeFormat("en-MY", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kuala_Lumpur" }).format(new Date(value)) : "Awaiting first publication";
const tone = (value) => Number(value) > 0 ? "up" : Number(value) < 0 ? "down" : "flat";

function Badge({ children, kind = "neutral" }) { return <span className={`badge ${kind}`}>{children}</span>; }
function Metric({ label, value, detail, valueClass = "" }) { return <div className="metric"><span>{label}</span><strong className={valueClass}>{value}</strong>{detail && <small>{detail}</small>}</div>; }
function Meter({ value = 0, label, color = COLORS.strong }) { const safe = Math.max(0, Math.min(100, Number(value) || 0)); return <div className="meter-row"><div><span>{label}</span><b>{num(safe, 0)}%</b></div><div className="meter"><i style={{ width: `${safe}%`, background: color }} /></div></div>; }
function Empty({ title, text }) { return <div className="empty"><div className="empty-mark">MK</div><h3>{title}</h3><p>{text}</p></div>; }

function Sparkline({ values = [], color = COLORS.strong }) {
  const points = values.map((entry) => Number(typeof entry === "object" ? entry.value : entry)).filter(Number.isFinite);
  if (points.length < 2) return <div className="spark-empty">No history yet</div>;
  const min = Math.min(...points), max = Math.max(...points), span = max - min || 1;
  const d = points.map((value, index) => `${index ? "L" : "M"}${(index / (points.length - 1)) * 100},${36 - ((value - min) / span) * 32}`).join(" ");
  return <svg className="spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-label="performance history"><path d={d} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg>;
}

function StockTable({ rows, compact = false }) {
  const [sort, setSort] = useState("quant_score");
  const [direction, setDirection] = useState("desc");
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const av = a[sort] ?? "", bv = b[sort] ?? "";
    const result = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
    return direction === "asc" ? result : -result;
  }), [rows, sort, direction]);
  const choose = (key) => { if (sort === key) setDirection((current) => current === "asc" ? "desc" : "asc"); else { setSort(key); setDirection("desc"); } };
  if (!rows.length) return <Empty title="No ranked stocks yet" text="The first successful daily quant publication will populate this table." />;
  const head = (key, text) => <button onClick={() => choose(key)}>{text}{sort === key ? (direction === "desc" ? " ↓" : " ↑") : ""}</button>;
  return <div className="table-wrap"><table className={compact ? "compact" : ""}><thead><tr><th>{head("symbol", "Stock")}</th><th>{head("quant_score", "Quant")}</th><th>{head("expected_edge", "Edge")}</th><th>{head("confidence", "Confidence")}</th><th>{head("close", "Close")}</th><th>{head("rs_20d", "RS 20D")}</th><th>Decision</th></tr></thead><tbody>{sorted.map((row, index) => <tr key={row.symbol}><td data-label="Stock"><div className="stock"><b>{row.symbol}</b><span>{row.name || row.sector || `Rank ${index + 1}`}</span></div></td><td data-label="Quant"><span className={`score ${Number(row.quant_score) >= 70 ? "elite" : ""}`}>{num(row.quant_score, 0)}</span></td><td data-label="Expected edge" className={tone(row.expected_edge)}>{pct(row.expected_edge, 2)}</td><td data-label="Confidence">{pct(row.confidence, 0)}</td><td data-label="Close">RM {num(row.close, Number(row.close) < 1 ? 3 : 2)}</td><td data-label="RS 20D" className={tone(row.rs_20d)}>{pct(row.rs_20d, 2)}</td><td data-label="Decision"><Badge kind={String(row.action || "WATCH").toLowerCase()}>{row.action || "WATCH"}</Badge></td></tr>)}</tbody></table></div>;
}

function Overview({ data }) {
  const top = (data.stocks || []).slice(0, 10);
  const state = data.regime?.state || "UNKNOWN";
  return <><section className="regime-hero"><div><span className="eyebrow">MARKET REGIME v2</span><h2>{state}</h2><p>{data.regime?.summary || "Six-factor breadth and participation model."}</p></div><div className="regime-score"><strong>{num(data.regime?.score, 0)}</strong><span>/ 100</span><small>{pct(data.regime?.confidence, 0)} confidence</small></div></section><section className="metric-grid"><Metric label="Universe" value={data.universe_size || 0} detail="Bursa securities"/><Metric label="Candidates" value={(data.stocks || []).length} detail="Ranked today"/><Metric label="Above 50DMA" value={pct(data.breadth?.above_50dma)} detail="Market breadth"/><Metric label="A/D ratio" value={num(data.breadth?.advance_decline, 2)} detail="Advancers / decliners"/></section><section className="panel"><div className="panel-head"><div><span className="eyebrow">DAILY LEADERBOARD</span><h3>Highest expected edge</h3></div><Badge kind="live">MODEL RANK</Badge></div><StockTable rows={top} compact /></section></>;
}

function Rankings({ data }) {
  const [view, setView] = useState("quant");
  const [query, setQuery] = useState("");
  const [decision, setDecision] = useState("ALL");
  const rows = (data.stocks || []).filter((row) => (!query || `${row.symbol} ${row.name || ""} ${row.sector || ""}`.toLowerCase().includes(query.toLowerCase())) && (decision === "ALL" || row.action === decision));
  return <><div className="ranking-switch" role="tablist" aria-label="Ranking views"><button type="button" role="tab" aria-selected={view === "quant"} className={view === "quant" ? "active" : ""} onClick={() => setView("quant")}>Quant Ranking</button><button type="button" role="tab" aria-selected={view === "activity"} className={view === "activity" ? "active" : ""} onClick={() => setView("activity")}>Abnormal Activity</button></div>{view === "quant" ? <section className="panel ranking-panel"><div className="panel-head stacked-mobile"><div><span className="eyebrow">CROSS-SECTIONAL MODEL</span><h3>Quant rankings</h3></div><div className="filters"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search symbol or sector" aria-label="Search rankings"/><select value={decision} onChange={(event) => setDecision(event.target.value)} aria-label="Filter decision"><option>ALL</option><option>ADD</option><option>HOLD</option><option>WATCH</option><option>TRIM</option><option>REDUCE</option><option>EXIT</option></select></div></div><StockTable rows={rows}/></section> : <Activity data={data}/>}</>;
}

function Portfolio({ data }) {
  const rows = data.portfolio || [];
  return <><section className="metric-grid"><Metric label="Capital deployed" value={pct(data.portfolio_summary?.capital_deployed)} detail="Risk-adjusted"/><Metric label="Portfolio beta" value={num(data.portfolio_summary?.beta, 2)} detail="vs KLCI"/><Metric label="Risk used" value={pct(data.portfolio_summary?.risk_used)} detail="Current budget"/><Metric label="Diversification" value={num(data.portfolio_summary?.diversification_score, 0)} detail="Score / 100"/></section><section className="panel"><div className="panel-head"><div><span className="eyebrow">POSITION ENGINE</span><h3>Portfolio allocation</h3></div></div>{rows.length ? <div className="cards">{rows.map((row) => <article className="position" key={row.symbol}><div><h4>{row.symbol}</h4><Badge kind={String(row.action || "HOLD").toLowerCase()}>{row.action || "HOLD"}</Badge></div><dl><dt>Target weight</dt><dd>{pct(row.target_weight)}</dd><dt>Position size</dt><dd>{pct(row.position_size)}</dd><dt>Risk contribution</dt><dd>{pct(row.risk_contribution)}</dd><dt>Stop</dt><dd>RM {num(row.stop_price, 3)}</dd></dl></article>)}</div> : <Empty title="Portfolio model is awaiting data" text="Published holdings and risk allocations will appear here without creating BUY transactions."/>}</section></>;
}

function Regime({ data }) {
  const breadth = data.breadth || {}, components = data.regime?.components || {};
  const meters = [["Above 20DMA", breadth.above_20dma], ["Above 50DMA", breadth.above_50dma], ["Above 200DMA", breadth.above_200dma], ["Volume breadth", breadth.volume_breadth], ["Sector breadth", breadth.sector_breadth], ["Participation", breadth.participation]];
  return <div className="two-col"><section className="panel"><div className="panel-head"><div><span className="eyebrow">PHASE 11</span><h3>Bursa breadth</h3></div></div><div className="meters">{meters.map(([label, value]) => <Meter key={label} label={label} value={value}/>)}</div></section><section className="panel"><div className="panel-head"><div><span className="eyebrow">SIX-FACTOR ENGINE</span><h3>Regime components</h3></div><Badge kind="live">{data.regime?.state || "UNKNOWN"}</Badge></div><div className="component-grid">{Object.entries(components).length ? Object.entries(components).map(([key, value]) => <Metric key={key} label={key.replaceAll("_", " ")} value={num(value, 0)} detail="component score"/>) : <Empty title="No component history" text="Run the quant engine to calculate the regime factors."/>}</div></section></div>;
}

function Backtest({ data }) {
  const backtest = data.backtest || {};
  return <><section className="metric-grid"><Metric label="Signal observations" value={backtest.total_trades || 0}/><Metric label="Win rate" value={pct(backtest.win_rate)}/><Metric label="Expectancy" value={pct(backtest.expectancy, 2)} valueClass={tone(backtest.expectancy)}/><Metric label="Cohort drawdown" value={pct(backtest.max_drawdown, 2)} valueClass="down"/></section><section className="panel"><div className="panel-head"><div><span className="eyebrow">WALK-FORWARD RESULTS</span><h3>Quant-score sextile validation</h3></div></div>{backtest.groups?.length ? <><p className="disclaimer">Full Quant Scores are reconstructed at each historical signal date, then ranked independently into six groups. Q1 is lowest and Q6 highest. Outcomes use the following 20 sessions; drawdown compounds equal-weight cohort returns. Current-universe diagnostic, excluding costs.</p><div className="backtest-grid">{backtest.groups.map((group) => <article key={group.name}><h4>{group.name}</h4><strong>{pct(group.win_rate)}</strong><span>{group.trades} observations</span><div className="mini-stats"><b className={tone(group.expectancy)}>E {pct(group.expectancy, 2)}</b><b>PF {num(group.profit_factor, 2)}</b></div></article>)}</div></> : <Empty title="No stored backtest payload" text="The engine will publish Quant-score sextile validation after sufficient history is available."/>}</section></>;
}

function Performance({ data }) {
  const performance = data.performance || {};
  const hasHistory = (performance.equity_curve || []).length > 1 || Number(performance.live_trades) > 0 || Number(performance.closed_trades) > 0;
  if (!hasHistory) return <section className="panel"><Empty title="Live performance tracking is awaiting history" text="No out-of-sample signals have been recorded yet. Zeroes are not presented as measured performance."/></section>;
  return <><section className="metric-grid"><Metric label="Live signals" value={performance.live_trades || 0}/><Metric label="Open" value={performance.open_trades || 0}/><Metric label="Closed" value={performance.closed_trades || 0}/><Metric label="Realized return" value={pct(performance.realized_return, 2)} valueClass={tone(performance.realized_return)}/></section><section className="panel chart-panel"><div className="panel-head"><div><span className="eyebrow">OUT-OF-SAMPLE</span><h3>Live equity curve</h3></div><strong>{pct(performance.hit_rate)} hit rate</strong></div><Sparkline values={performance.equity_curve}/></section></>;
}

function Research({ data }) {
  const rows = data.research || [];
  return <section className="panel"><div className="panel-head"><div><span className="eyebrow">RESEARCH ARCHIVE</span><h3>Per-stock model evidence</h3></div><Badge>{rows.length} records</Badge></div>{rows.length ? <div className="research-grid">{rows.map((row) => <article key={row.symbol}><div><h4>{row.symbol}</h4><span>{row.sector || "Bursa Malaysia"}</span></div><p>{row.thesis || row.summary || "Quant evidence archived for this run."}</p><dl><dt>Quality</dt><dd>{num(row.quality_score, 0)}</dd><dt>Momentum</dt><dd>{num(row.momentum_score, 0)}</dd><dt>Risk</dt><dd>{num(row.risk_score, 0)}</dd></dl></article>)}</div> : <Empty title="Research archive is empty" text="Evidence rows will be retained by run after the first complete publication."/>}</section>;
}

function Activity({ data }) {
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("ALL");
  const source = data.unexplained_activity || data.abnormal_activity || [];
  const summary = data.unexplained_activity_summary || {};
  const rows = source.filter((row) => (!query || `${row.symbol} ${row.name || ""} ${row.sector || ""}`.toLowerCase().includes(query.toLowerCase())) && (level === "ALL" || row.activity_level === level));
  return <><section className="activity-summary metric-grid"><Metric label="Flagged" value={summary.flagged ?? source.length} detail="Daily deviations"/><Metric label="Very high" value={summary.very_high ?? source.filter((row) => row.activity_level === "VERY HIGH").length} detail="Highest severity"/><Metric label="High" value={summary.high ?? source.filter((row) => row.activity_level === "HIGH").length} detail="High severity"/><Metric label="Elevated" value={summary.elevated ?? source.filter((row) => row.activity_level === "ELEVATED").length} detail="Monitor"/></section><section className="panel activity-panel"><div className="panel-head stacked-mobile"><div><span className="eyebrow">NEUTRAL MARKET-BEHAVIOUR MONITOR</span><h3>Unexplained activity</h3></div><div className="filters"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search code or company" aria-label="Search unexplained activity"/><select value={level} onChange={(event) => setLevel(event.target.value)} aria-label="Filter activity level"><option value="ALL">ALL LEVELS</option><option value="VERY HIGH">VERY HIGH</option><option value="HIGH">HIGH</option><option value="ELEVATED">ELEVATED</option></select></div></div><p className="disclaimer">Flags unusual price, volume, turnover, volatility, or relative-strength behaviour only. It is not an allegation or a trade instruction and does not identify leaked information or insider trading.</p>{rows.length ? <div className="activity-list">{rows.map((row) => <article key={row.symbol}><div><h4>{row.symbol}</h4><span>{row.name || row.reason || "Multi-factor deviation"}</span><small>{row.reason || "Multi-factor deviation"}</small></div><strong className={`activity-score ${String(row.activity_level || "").toLowerCase().replaceAll(" ", "-")}`}>{num(row.activity_score, 0)}<small>{row.activity_level || "FLAGGED"}</small></strong><div className="activity-factors">{Object.entries(row.factors || {}).map(([key, value]) => <span key={key}>{key.replaceAll("_", " ")} <b>{num(value, 1)}σ</b></span>)}</div></article>)}</div> : <Empty title="No matching activity flags" text="Adjust the search or severity filter to view the published daily deviations."/>}</section></>;
}

export default function App() {
  const [active, setActive] = useState("Overview");
  const [data, setData] = useState(DEMO_DATA);
  const [status, setStatus] = useState("loading");
  const [theme, setTheme] = useState(() => localStorage.getItem("quant-theme") || "dark");
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("quant-theme", theme); }, [theme]);
  useEffect(() => {
    let live = true;
    fetch("/api/latest").then((response) => response.ok ? response.json() : Promise.reject(new Error("API unavailable"))).then((body) => {
      if (!live) return;
      if (body.data) { setData(body.data); setStatus("live"); } else setStatus("empty");
    }).catch(() => live && setStatus("offline"));
    return () => { live = false; };
  }, []);
  const pages = { Overview, Rankings, Portfolio, Regime, Backtest, Performance, Research };
  const Page = pages[active];
  return <div className="app-shell"><header><div className="brand"><div className="brand-mark">MK</div><div><h1>QUANT TERMINAL</h1><span>BURSA MUSANGKING</span></div></div><div className="header-meta"><div><span>SCAN DATE</span><b>{data.scan_date || "—"}</b></div><div><span>STATUS</span><b className={status === "live" ? "up" : "flat"}><i />{status.toUpperCase()}</b></div><button className="theme" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">{theme === "dark" ? "☀" : "◐"}</button></div></header><nav aria-label="Terminal sections">{TABS.map((tab) => <button key={tab} className={active === tab ? "active" : ""} onClick={() => setActive(tab)}>{tab}</button>)}</nav><main><div className="page-title"><div><span className="eyebrow">{data.market || "MYX"} · {data.benchmark || "^KLSE"}</span><h2>{active}</h2></div><div className="updated"><span>LAST UPDATED</span><b>{dateTime(data.generated_at)}</b></div></div>{status !== "live" && <div className={`notice ${status}`}>{status === "loading" ? "Connecting to Quant API…" : status === "empty" ? "Deployment is ready. Waiting for the first daily quant publication." : "Quant API is unavailable. The interface remains ready and will reconnect on reload."}</div>}<Page data={data}/></main><footer><span>Bursa MusangKing Quant Terminal v5.0 · Phases 1–15</span><span>Research and ranking output only. No profitability is guaranteed.</span></footer></div>;
}
