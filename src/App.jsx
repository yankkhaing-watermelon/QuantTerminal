import React, { useEffect, useMemo, useState } from "react";
import Wizard from "./Wizard.jsx";
import Astra, { INITIAL as ASTRA_INITIAL } from "./Astra.jsx";
import TerminalNavigation from "./TerminalNavigation.jsx";

const TABS = ["Overview", "Rankings", "Wizard", "Astra", "Portfolio", "Regime", "Backtest", "Performance", "Research"];
const NAV_META = {
  Overview: { label: "Today", icon: "◫" }, Rankings: { label: "Ranking", icon: "≡" },
  Wizard: { label: "Wizard", icon: "◆" }, Portfolio: { label: "Portfolio", icon: "▦" },
  Astra: { label: "Astra", icon: "✦" },
  Regime: { label: "Regime", icon: "◉" }, Backtest: { label: "Backtest", icon: "◇" },
  Performance: { label: "Performance", icon: "∿" }, Research: { label: "Research", icon: "⌁" },
};
const COLORS = { strong: "#4af6c3", positive: "#4af6c3", neutral: "#fb8b1e", negative: "#ff433d", muted: "#0068ff" };

const DEMO_DATA = {
  run_id: "demo-preview",
  scan_date: "—",
  generated_at: null,
  universe_size: 0,
  market: "MYX",
  benchmark: "^KLSE",
  regime: { state: "AWAITING DATA", score: 0, confidence: 0, components: {} },
  breadth: { above_20dma: 0, above_50dma: 0, above_200dma: 0, advance_decline: 0, volume_breadth: 0, sector_breadth: 0, participation: 0, dispersion: 0 },
  stocks: [], portfolio: [], research: [], abnormal_activity: [], wizard_candidates: [], wizard_summary: {},
  backtest: { total_trades: 0, win_rate: 0, expectancy: 0, max_drawdown: 0, groups: [] },
  performance: { live_trades: 0, open_trades: 0, closed_trades: 0, hit_rate: 0, realized_return: 0, equity_curve: [] },
};

const num = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
const pct = (value, digits = 1) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits)}%` : "—";
const money = (value) => Number.isFinite(Number(value)) ? `RM ${Number(value).toFixed(Number(value) < 1 ? 3 : 2)}` : "RM —";
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

function RankingList({ rows }) {
  const [selected, setSelected] = useState(null);
  useEffect(() => {
    if (!selected) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") setSelected(null); };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [selected]);
  if (!rows.length) return <Empty title="No matching ranked stocks" text="Adjust the search, sector, or score filters." />;
  const openDetails = (row) => setSelected(row);
  return <><div className="ranking-list">{rows.map((row, index) => <article className="ranking-row" key={row.symbol} role="button" tabIndex="0" aria-label={`View ${row.symbol} details, latest price ${money(row.close)}`} onClick={() => openDetails(row)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDetails(row); } }}><span className="ranking-number">{String(index + 1).padStart(3, "0")}</span><div className="ranking-name"><strong>{row.tv_symbol || row.symbol}</strong><span>{row.name || row.sector || "Bursa Malaysia"}</span><em className="ranking-price">{money(row.close)}</em></div><div className="ranking-factor"><small>TRD</small><b>{num(row.trend_score, 0)}</b><i style={{ width: `${Math.max(0, Math.min(100, Number(row.trend_score) || 0))}%` }} /></div><div className="ranking-factor"><small>MOM</small><b>{num(row.momentum_score, 0)}</b><i style={{ width: `${Math.max(0, Math.min(100, Number(row.momentum_score) || 0))}%` }} /></div><span className={`ranking-score ${Number(row.quant_score) >= 80 ? "elite" : ""}`}>{num(row.quant_score, 1)}</span><span className="ranking-chevron" aria-hidden="true">›</span></article>)}</div>{selected && <div className="stock-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}><section className="stock-detail" role="dialog" aria-modal="true" aria-labelledby="stock-detail-title"><div className="stock-detail-head"><div><span>{selected.symbol}</span><h3 id="stock-detail-title">{selected.name || selected.sector || "Bursa Malaysia"}</h3></div><button type="button" onClick={() => setSelected(null)} aria-label="Close stock details">×</button></div><div className="stock-detail-price"><span>Latest closing price</span><strong>{money(selected.close)}</strong><small>Market date {selected.last_bar || "—"}</small></div><dl><dt>Quant score</dt><dd>{num(selected.quant_score, 1)}</dd><dt>Expected edge</dt><dd className={tone(selected.expected_edge)}>{pct(selected.expected_edge, 2)}</dd><dt>Confidence</dt><dd>{pct(selected.confidence, 0)}</dd><dt>RS 20D</dt><dd className={tone(selected.rs_20d)}>{pct(selected.rs_20d, 2)}</dd><dt>RSI</dt><dd>{num(selected.rsi, 1)}</dd><dt>ATR</dt><dd>{money(selected.atr)}</dd><dt>Model decision</dt><dd><Badge kind={String(selected.action || "WATCH").toLowerCase()}>{selected.action || "WATCH"}</Badge></dd></dl></section></div>}</>;
}

function Overview({ data }) {
  const top = (data.stocks || []).slice(0, 10);
  const state = data.regime?.state || "UNKNOWN";
  return <><section className="regime-hero"><div><span className="eyebrow">MARKET REGIME v2</span><h2>{state}</h2><p>{data.regime?.summary || "Six-factor breadth and participation model."}</p></div><div className="regime-score"><strong>{num(data.regime?.score, 0)}</strong><span>/ 100</span><small>{pct(data.regime?.confidence, 0)} confidence</small></div></section><section className="metric-grid"><Metric label="Universe" value={data.universe_size || 0} detail="Bursa securities"/><Metric label="Candidates" value={(data.stocks || []).length} detail="Ranked today"/><Metric label="Above 50DMA" value={pct(data.breadth?.above_50dma)} detail="Market breadth"/><Metric label="A/D ratio" value={num(data.breadth?.advance_decline, 2)} detail="Advancers / decliners"/></section><section className="panel"><div className="panel-head"><div><span className="eyebrow">DAILY LEADERBOARD</span><h3>Highest expected edge</h3></div><Badge kind="live">MODEL RANK</Badge></div><RankingList rows={top}/></section></>;
}

function Rankings({ data }) {
  const [view, setView] = useState("quant");
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("ALL");
  const [scoreBand, setScoreBand] = useState("ALL");
  const [rankSort, setRankSort] = useState("quant_score");
  const sectors = [...new Set((data.stocks || []).map((row) => row.sector).filter(Boolean))].sort();
  const rows = (data.stocks || []).filter((row) => {
    const score = Number(row.quant_score) || 0;
    const scoreMatch = scoreBand === "ALL" || (scoreBand === "80" && score >= 80) || (scoreBand === "70" && score >= 70 && score < 80) || (scoreBand === "60" && score >= 60 && score < 70) || (scoreBand === "LT60" && score < 60);
    return (!query || `${row.symbol} ${row.tv_symbol || ""} ${row.name || ""} ${row.sector || ""}`.toLowerCase().includes(query.toLowerCase())) && (sector === "ALL" || row.sector === sector) && scoreMatch;
  }).sort((a, b) => Number(b[rankSort] || 0) - Number(a[rankSort] || 0));
  return <><div className="ranking-switch" role="tablist" aria-label="Ranking views"><button type="button" role="tab" aria-selected={view === "quant"} className={view === "quant" ? "active" : ""} onClick={() => setView("quant")}>Quant Ranking</button><button type="button" role="tab" aria-selected={view === "activity"} className={view === "activity" ? "active" : ""} onClick={() => setView("activity")}>Abnormal Activity</button></div>{view === "quant" ? <><section className="ranking-hero"><div><span className="eyebrow">FULL BURSA UNIVERSE</span><h3>Daily Quant Ranking</h3><p>Every eligible stock receives the same transparent, versioned scoring treatment.</p></div><strong>{Number(data.fresh_symbols ?? data.stocks?.length ?? 0).toLocaleString()}<small>counters</small></strong></section><section className="ranking-controls" aria-label="Ranking filters"><label className="ranking-search"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Code or company" aria-label="Search rankings"/></label><select value={sector} onChange={(event) => setSector(event.target.value)} aria-label="Filter sector"><option value="ALL">All sectors</option>{sectors.map((entry) => <option key={entry} value={entry}>{entry}</option>)}</select><select value={scoreBand} onChange={(event) => setScoreBand(event.target.value)} aria-label="Filter score"><option value="ALL">All scores</option><option value="80">Score ≥ 80</option><option value="70">70–79.9</option><option value="60">60–69.9</option><option value="LT60">Below 60</option></select><select className="ranking-sort" value={rankSort} onChange={(event) => setRankSort(event.target.value)} aria-label="Sort rankings"><option value="quant_score">Sort: Quant Score</option><option value="trend_score">Sort: Trend</option><option value="momentum_score">Sort: Momentum</option><option value="expected_edge">Sort: Expected Edge</option><option value="confidence">Sort: Confidence</option></select></section><section className="panel ranking-panel"><RankingList rows={rows}/></section></> : <Activity data={data}/>}</>;
}

function Portfolio({ data }) {
  const rows = data.portfolio || [];
  const companyNames = new Map((data.stocks || []).map((stock) => [String(stock.symbol), stock.name]));
  return <><section className="metric-grid"><Metric label="Capital deployed" value={pct(data.portfolio_summary?.capital_deployed)} detail="Risk-adjusted"/><Metric label="Portfolio beta" value={num(data.portfolio_summary?.beta, 2)} detail="vs KLCI"/><Metric label="Risk used" value={pct(data.portfolio_summary?.risk_used)} detail="Current budget"/><Metric label="Diversification" value={num(data.portfolio_summary?.diversification_score, 0)} detail="Score / 100"/></section><section className="panel"><div className="panel-head"><div><span className="eyebrow">POSITION ENGINE</span><h3>Portfolio allocation</h3></div></div>{rows.length ? <div className="cards">{rows.map((row) => { const companyName = row.name || row.company_name || companyNames.get(String(row.symbol)) || "Bursa Malaysia"; return <article className="position" key={row.symbol}><div className="position-head"><div><h4>{row.symbol}</h4><span>{companyName}</span></div><Badge kind={String(row.action || "HOLD").toLowerCase()}>{row.action || "HOLD"}</Badge></div><dl><dt>Target weight</dt><dd>{pct(row.target_weight)}</dd><dt>Position size</dt><dd>{pct(row.position_size)}</dd><dt>Risk contribution</dt><dd>{pct(row.risk_contribution)}</dd><dt>Stop</dt><dd>RM {num(row.stop_price, 3)}</dd></dl></article>; })}</div> : <Empty title="Portfolio model is awaiting data" text="Published holdings and risk allocations will appear here without creating BUY transactions."/>}</section></>;
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
  const companyNames = new Map((data.stocks || []).map((stock) => [String(stock.symbol), stock.name]));
  return <section className="panel"><div className="panel-head"><div><span className="eyebrow">RESEARCH ARCHIVE</span><h3>Per-stock model evidence</h3></div><Badge>{rows.length} records</Badge></div>{rows.length ? <div className="research-grid">{rows.map((row) => { const companyName = row.name || row.company_name || companyNames.get(String(row.symbol)) || "Bursa Malaysia"; return <article key={row.symbol}><div className="research-identity"><div><h4>{row.symbol}</h4><strong>{companyName}</strong></div><span>{row.sector || "Bursa Malaysia"}</span></div><p>{row.thesis || row.summary || "Quant evidence archived for this run."}</p><dl><dt>Quality</dt><dd>{num(row.quality_score, 0)}</dd><dt>Momentum</dt><dd>{num(row.momentum_score, 0)}</dd><dt>Risk</dt><dd>{num(row.risk_score, 0)}</dd></dl></article>; })}</div> : <Empty title="Research archive is empty" text="Evidence rows will be retained by run after the first complete publication."/>}</section>;
}

function Activity({ data }) {
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState("ALL");
  const [strengthSort, setStrengthSort] = useState("DESC");
  const stockBySymbol = new Map((data.stocks || []).map((stock) => [String(stock.symbol), stock]));
  const source = (data.unexplained_activity || data.abnormal_activity || []).map((row) => {
    const stock = stockBySymbol.get(String(row.symbol));
    return { ...row, close: row.close ?? stock?.close, observation_date: row.observation_date ?? stock?.last_bar };
  });
  const summary = data.unexplained_activity_summary || {};
  const classify = (row) => row.direction || ((Math.abs(Number(row.factors?.relative_strength) || 0) > Math.abs(Number(row.factors?.price_return ?? row.factors?.price) || 0) ? Number(row.factors?.relative_strength) : Number(row.factors?.price_return ?? row.factors?.price)) < 0 ? "NEGATIVE" : "POSITIVE");
  const positive = source.filter((row) => classify(row) === "POSITIVE").length;
  const negative = source.filter((row) => classify(row) === "NEGATIVE").length;
  const rows = source.filter((row) => (!query || `${row.symbol} ${row.name || ""} ${row.sector || ""}`.toLowerCase().includes(query.toLowerCase())) && (direction === "ALL" || classify(row) === direction)).sort((a, b) => strengthSort === "DESC" ? Number(b.activity_score || 0) - Number(a.activity_score || 0) : Number(a.activity_score || 0) - Number(b.activity_score || 0));
  return <><section className="activity-summary metric-grid"><Metric label="Flagged" value={summary.flagged ?? source.length} detail="Daily deviations"/><Metric label="Positive" value={summary.positive ?? positive} detail="Positive direction" valueClass="up"/><Metric label="Negative" value={summary.negative ?? negative} detail="Negative direction" valueClass="down"/><Metric label="Max strength" value={num(summary.max_score ?? Math.max(0, ...source.map((row) => Number(row.activity_score) || 0)), 0)} detail="Score / 100"/></section><section className="panel activity-panel"><div className="panel-head stacked-mobile"><div><span className="eyebrow">NEUTRAL MARKET-BEHAVIOUR MONITOR</span><h3>Unexplained activity by strength</h3></div><div className="filters activity-filters"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search code or company" aria-label="Search unexplained activity"/><select value={direction} onChange={(event) => setDirection(event.target.value)} aria-label="Filter activity direction"><option value="ALL">ALL DIRECTIONS</option><option value="POSITIVE">POSITIVE</option><option value="NEGATIVE">NEGATIVE</option></select><select value={strengthSort} onChange={(event) => setStrengthSort(event.target.value)} aria-label="Sort activity strength"><option value="DESC">STRONGEST FIRST</option><option value="ASC">WEAKEST FIRST</option></select></div></div><p className="disclaimer">Positive or negative describes the dominant observed price/relative-strength deviation. Strength ranks the magnitude from 0–100. Neither is an allegation or trade instruction.</p>{rows.length ? <div className="activity-list">{rows.map((row, index) => { const rowDirection = classify(row); return <article key={row.symbol}><span className="activity-rank">{String(index + 1).padStart(3, "0")}</span><div><h4>{row.symbol}</h4><span>{row.name || row.reason || "Multi-factor deviation"}</span><em className="activity-price">{money(row.close)}</em><small>{row.reason || "Multi-factor deviation"}</small></div><strong className={`activity-score ${rowDirection.toLowerCase()}`}>{num(row.activity_score, 0)}<small>{rowDirection} {rowDirection === "POSITIVE" ? "↑" : "↓"}</small></strong><div className="activity-factors">{Object.entries(row.factors || {}).map(([key, value]) => <span key={key}>{key.replaceAll("_", " ")} <b>{num(value, 1)}σ</b></span>)}</div></article>})}</div> : <Empty title="No matching activity flags" text="Adjust the search or direction filter to view the published daily deviations."/>}</section></>;
}

export default function App() {
  const [astraSettings, setAstraSettings] = useState(() => {
    try { return { ...ASTRA_INITIAL, ...JSON.parse(localStorage.getItem("astra-settings") || "{}") }; }
    catch { return ASTRA_INITIAL; }
  });
  const [sharedJob, setSharedJob] = useState(null);
  useEffect(() => { localStorage.setItem("astra-settings", JSON.stringify(astraSettings)); }, [astraSettings]);
  const [active, setActive] = useState("Overview");
  const [data, setData] = useState(DEMO_DATA);
  const [status, setStatus] = useState("loading");
  const [runState, setRunState] = useState("idle");
  const [runMessage, setRunMessage] = useState("");
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
  useEffect(() => {
    let live = true;
    async function refresh() {
      try {
        const response = await fetch(`/api/astra?status=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) return;
        const body = await response.json();
        if (!live || !body.ok) return;
        setSharedJob(body.job);
        if (["queued", "running"].includes(body.job?.state)) {
          setRunState(body.job.state === "queued" ? "queued" : "scanning");
          setRunMessage(`${body.job.message || "Shared scan running"} · ${body.job.processed || 0}/${body.job.total || "—"}`);
        } else if (["failed", "timed_out"].includes(body.job?.state)) {
          setRunState("error"); setRunMessage(body.job.message || "Shared run stopped. Check workflow logs.");
        } else if (body.job?.state === "completed" && body.data?.shared_run_id) {
          setRunState("idle"); setRunMessage("Quant, Wizard and Astra · shared run complete");
          const latest = await fetch("/api/latest", { cache: "no-store" });
          const published = latest.ok ? await latest.json() : null;
          if (live && published?.data) { setData(published.data); setStatus("live"); }
        }
      } catch { /* Keep last verified result; the next poll can reconnect. */ }
    }
    refresh(); const timer = setInterval(refresh, 15000);
    return () => { live = false; clearInterval(timer); };
  }, []);
  const startRun = async () => {
    if (["authorizing", "queued", "scanning"].includes(runState)) return;
    setRunState("authorizing"); setRunMessage("Checking the shared Quant and Astra snapshot…");
    try {
      const response = await fetch("/api/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ config: astraSettings }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error === "run_cooldown" ? "A shared run is active or was requested recently. Progress will reconnect automatically." : body.error || "The shared scan could not start.");
      if (body.state === "reused") {
        setData(body.data); setStatus("live"); setRunState("idle");
        setRunMessage("Quant and Astra already share this snapshot and these settings. No new TradingView scan.");
      } else {
        setRunState("queued"); setRunMessage("One shared 300-bar scan queued for Quant, Wizard and Astra. You can close the app.");
      }
    } catch (error) { setRunState("error"); setRunMessage(error.message); }
  };
  const pages = { Overview, Rankings, Wizard, Astra, Portfolio, Regime, Backtest, Performance, Research };
  const Page = pages[active];
  const solidOrange = { background: "#fb8b1e", backgroundImage: "none", border: 0, boxShadow: "none", outline: 0, color: "#000000", WebkitAppearance: "none", appearance: "none" };
  const runBusy = ["authorizing", "queued", "scanning"].includes(runState);
  const runLabel = runState === "authorizing" ? "CHECK" : runState === "queued" ? "QUEUED" : runState === "scanning" ? "RUNNING" : runState === "published" ? "DONE" : "RUN";
  return <div className="app-shell"><header><div className="brand"><div className="brand-mark-orange" style={solidOrange}>BMK</div><div><span>BURSA MALAYSIA · QUANT</span><h1>MusangKing Terminal</h1></div></div><div className="header-meta"><button type="button" className={`run-button-orange ${runState}`} style={solidOrange} onClick={startRun} disabled={runBusy} aria-label="Run or reuse shared Quant and Astra scan"><span aria-hidden="true">▶</span>{runLabel}</button><div className={`date-pill ${status === "live" ? "live" : ""}`}><i />{data.scan_date || "—"}</div><button className="theme" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">{theme === "dark" ? "☀" : "◐"}</button></div></header><div className={`public-notice ${runState}`}>{runMessage || "RUN updates Quant, Wizard and Astra · one TradingView scan · 300 daily bars per stock"}</div><TerminalNavigation tabs={TABS} metadata={NAV_META} active={active} onSelect={setActive}/><main><div className={`page-title ${active === "Rankings" ? "ranking-title" : ""}`}><div><span className="eyebrow">{data.market || "MYX"} · {data.benchmark || "^KLSE"}</span><h2>{NAV_META[active].label}</h2></div>{active !== "Astra" && <div className="updated"><span>LAST UPDATED</span><b>{dateTime(data.generated_at)}</b></div>}</div>{active !== "Astra" && status !== "live" && <div className={`notice ${status}`}>{status === "loading" ? "Connecting to Quant API…" : status === "empty" ? "Deployment is ready. Waiting for the first daily quant publication." : "Quant API is unavailable. The interface remains ready and will reconnect on reload."}</div>}<Page data={data} settings={astraSettings} setSettings={setAstraSettings} sharedJob={sharedJob}/></main><footer><span>Bursa MusangKing Quant Terminal v5.0 · Wizard decision layer</span><span>Model decision support only. No profitability is guaranteed.</span></footer></div>;
}
