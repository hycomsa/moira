import { useEffect, useMemo, useState } from "react";
import { api, getBudget, setBudget, type ActivityRow, type InboxItem, type RunSummary, type SpendRollup, type Stats } from "../api";
import { Metrics, fmtDur, fmtTokens } from "../components/Metrics";
import { Button } from "../components/ui/Button";

const COLOR: Record<string, string> = {
  succeeded: "#3fb950", failed: "#f85149", waiting_gate: "#d29922",
  running: "#58a6ff", rejected: "#f85149", pending: "#8b949e",
};
const Dot = ({ s }: { s: string }) => <span className="dot" style={{ background: COLOR[s] ?? "#8b949e" }} />;

const ago = (ts: number) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return `${s | 0}s`;
  if (s < 3600) return `${(s / 60) | 0}m`;
  if (s < 86400) return `${(s / 3600) | 0}h`;
  return `${(s / 86400) | 0}d`;
};

function Spark({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return <svg className="spark" width={72} height={24} />;
  const max = Math.max(...values, 1);
  const w = 72, h = 24;
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => `${i * step},${h - (v / max) * (h - 3) - 1.5}`);
  return (
    <svg className="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      <polyline points={`0,${h} ${pts.join(" ")} ${w},${h}`} fill={color} fillOpacity="0.12" stroke="none" />
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.6"
                strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Overview({ onNavigate }: { onNavigate: (view: string) => void }) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [activity, setActivity] = useState<ActivityRow[]>([]);
  const [counts, setCounts] = useState<{ agents: number; pipelines: number }>({ agents: 0, pipelines: 0 });
  const [spend, setSpend] = useState<SpendRollup | null>(null);
  const [budget, setBudgetState] = useState<number>(getBudget());

  useEffect(() => {
    const load = async () => {
      try {
        const [s, r, i, a, sp] = await Promise.all([api.stats(), api.runs(), api.inbox(), api.activity(), api.spend()]);
        setStats(s); setRuns(r.runs); setInbox(i.inbox); setActivity(a.activity); setSpend(sp);
      } catch { /* sidecar starting */ }
    };
    load();
    Promise.all([api.agents(), api.pipelines()])
      .then(([ag, pp]) => setCounts({ agents: ag.agents.length, pipelines: pp.pipelines.length }))
      .catch(() => { /* */ });
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  // 14-day sparklines
  const { runSpark, okSpark } = useMemo(() => {
    const days = 14, now = Date.now() / 1000;
    const all = new Array(days).fill(0), ok = new Array(days).fill(0);
    runs.forEach((r) => {
      const d = Math.floor((now - r.created_at) / 86400);
      if (d >= 0 && d < days) { all[days - 1 - d]++; if (r.status === "succeeded") ok[days - 1 - d]++; }
    });
    return { runSpark: all, okSpark: ok };
  }, [runs]);

  // fleet rollup across runs
  const fleet = useMemo(() => {
    let usd = 0, tok = 0, dur = 0;
    const models: Record<string, number> = {};
    runs.forEach((r) => {
      usd += r.usd || 0; tok += r.tokens || 0; dur += r.duration || 0;
      if (r.model && r.model !== "—") models[r.model] = (models[r.model] || 0) + 1;
    });
    const model = Object.entries(models).sort((a, b) => b[1] - a[1])[0]?.[0];
    return { usd: Math.round(usd * 1e4) / 1e4, tok, dur, model };
  }, [runs]);

  const total = stats?.total ?? 0;
  const succeeded = stats?.succeeded ?? 0;
  const successRate = total ? Math.round((succeeded / total) * 100) : 0;
  const waiting = stats?.waiting_gate ?? 0;
  const running = stats?.running ?? 0;
  const failed = stats?.failed ?? 0;

  const seg = [
    { k: "succeeded", v: succeeded }, { k: "running", v: running },
    { k: "waiting_gate", v: waiting }, { k: "failed", v: failed },
  ].filter((s) => s.v > 0);
  const segTotal = seg.reduce((a, s) => a + s.v, 0) || 1;

  const kpis = [
    { label: "Total runs", value: total, grad: "grad-blue", spark: runSpark, sparkColor: "#58a6ff", foot: `${counts.pipelines} pipelines` },
    { label: "Success rate", value: `${successRate}%`, grad: "grad-green", spark: okSpark, sparkColor: "#3fb950", foot: `${succeeded} succeeded` },
    { label: "Waiting at gate", value: waiting, grad: "grad-amber", spark: [], sparkColor: "#d29922", foot: waiting ? "needs you" : "all clear", action: waiting ? "inbox" : undefined },
    { label: "Total cost", value: `$${stats?.total_cost_usd ?? 0}`, grad: "grad-violet", spark: [], sparkColor: "#a371f7", foot: "across all runs" },
    { label: "Agent library", value: counts.agents, grad: "grad-cyan", spark: [], sparkColor: "#39c5cf", foot: "git-native", action: "agents" },
  ];

  return (
    <div className="page overview">
      {/* hero */}
      <section className="hero">
        <div className="hero-glow" />
        <div className="hero-body">
          <div className="eyebrow">AI-native SDLC cockpit</div>
          <h1 className="hero-title">Mission control<span className="hero-dot">.</span></h1>
          <p className="hero-sub">
            Governed agent orchestration with a git-native audit trail.{" "}
            {waiting > 0
              ? <b className="hero-alert">{waiting} decision{waiting > 1 ? "s" : ""} awaiting you.</b>
              : "Every step traced · every gate accountable."}
          </p>
        </div>
        <div className="hero-actions">
          <Button variant="primary" onClick={() => onNavigate("runs")}>▶ New run</Button>
          {waiting > 0 && <Button variant="ghost" onClick={() => onNavigate("inbox")}>⚑ Review gates ({waiting})</Button>}
        </div>
      </section>

      {/* KPI cards */}
      <div className="kpis">
        {kpis.map((k) => (
          <div className={"kpi glass" + (k.action ? " clickable" : "")} key={k.label}
               onClick={() => k.action && onNavigate(k.action)}>
            <div className="kpi-top"><span className="kpi-label">{k.label}</span>
              {k.spark.length > 1 && <Spark values={k.spark} color={k.sparkColor} />}</div>
            <div className={"kpi-value " + k.grad}>{k.value}</div>
            <div className="kpi-foot">{k.foot}</div>
          </div>
        ))}
      </div>

      {/* pipeline status bar */}
      <section className="panel glass status-panel">
        <div className="panel-head"><h3>Pipeline status</h3><span className="muted">{total} runs total</span></div>
        <div className="status-bar">
          {seg.map((s) => (
            <div key={s.k} className="status-seg" title={`${s.k}: ${s.v}`}
                 style={{ width: `${(s.v / segTotal) * 100}%`, background: COLOR[s.k] }} />
          ))}
          {seg.length === 0 && <div className="status-seg empty-seg" style={{ width: "100%" }} />}
        </div>
        <div className="status-legend">
          {[["succeeded", succeeded], ["running", running], ["waiting_gate", waiting], ["failed", failed]].map(([k, v]) => (
            <span key={k as string} className="leg"><Dot s={k as string} />{k as string} <b>{v}</b></span>
          ))}
        </div>
        <div className="fleet">
          {fleet.model && <span className="fleet-model" title="leading model">◆ {fleet.model}</span>}
          <span className="fleet-item" title="total step time">⏱ {fmtDur(fleet.dur)}</span>
          <span className="fleet-item" title="total cost">${fleet.usd}</span>
          <span className="fleet-item" title="total tokens">⛁ {fmtTokens(fleet.tok)} tokens</span>
        </div>
      </section>

      {/* two columns */}
      <div className="ov-grid">
        <section className="panel glass">
          <div className="panel-head"><h3>Recent runs</h3>
            <button className="link" onClick={() => onNavigate("runs")}>View all →</button></div>
          <div className="ov-runs">
            {runs.slice(0, 8).map((r) => (
              <div className="ov-run" key={r.run_id} onClick={() => onNavigate("runs")}>
                <Dot s={r.status} />
                <span className="ov-run-pipe">{r.pipeline_id}</span>
                <Metrics m={r} compact />
                <span className="ov-run-ago">{ago(r.updated_at || r.created_at)}</span>
              </div>
            ))}
            {runs.length === 0 && <div className="empty">No runs yet — start one in Runs.</div>}
          </div>
        </section>

        <aside className="ov-side">
          <section className="panel glass">
            <div className="panel-head"><h3>Needs your decision</h3>
              {inbox.length > 0 && <span className="pill-count">{inbox.length}</span>}</div>
            {inbox.length === 0 && <div className="empty small">Nothing waiting — agents autonomous.</div>}
            {inbox.slice(0, 4).map((it) => (
              <div className="ov-gate" key={it.run_id} onClick={() => onNavigate("inbox")}>
                {it.persona && <span className="persona-tag">{it.persona}</span>}
                <span className="ov-gate-msg">{it.message}</span>
              </div>
            ))}
          </section>

          <section className="panel glass">
            <div className="panel-head"><h3>Spend</h3><span className="muted">{spend?.month || ""}</span></div>
            {!spend ? <div className="empty small">—</div> : (
              <div className="spend">
                <div className="spend-top">
                  <div><div className="spend-v">${spend.month_usd}</div><div className="spend-l">this month</div></div>
                  <div className="spend-budget">
                    <label className="spend-blabel">budget/mo $
                      <input type="number" min={0} step={1} value={budget || ""}
                        onChange={(e) => { const v = Number(e.target.value) || 0; setBudgetState(v); setBudget(v); }}
                        placeholder="—" /></label>
                  </div>
                </div>
                {budget > 0 && (
                  <>
                    <div className="spend-bar">
                      <span className={"spend-fill" + (spend.month_usd > budget ? " over" : "")}
                        style={{ width: Math.min(100, (spend.month_usd / budget) * 100) + "%" }} />
                    </div>
                    {spend.month_usd > budget && <div className="spend-warn">⚠ over budget by ${(spend.month_usd - budget).toFixed(2)}</div>}
                  </>
                )}
                <div className="spend-models">
                  {spend.by_model.slice(0, 4).map((m) => (
                    <div className="spend-mrow" key={m.label}><span>◆ {m.label}</span><b>${m.usd}</b></div>
                  ))}
                </div>
                <div className="spend-foot muted">${spend.total_usd} total · {spend.runs} runs</div>
              </div>
            )}
          </section>

          <section className="panel glass">
            <h3>Live activity</h3>
            <div className="ov-activity">
              {activity.slice(0, 9).map((e) => (
                <div className="ov-evt" key={e.seq}>
                  <span className="evt-kind">{e.kind}</span>
                  <span className="evt-msg">{e.message}</span>
                  <span className="evt-ago">{ago(e.ts)}</span>
                </div>
              ))}
              {activity.length === 0 && <div className="empty small">Quiet for now.</div>}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
