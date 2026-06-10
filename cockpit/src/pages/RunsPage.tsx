import { useCallback, useEffect, useRef, useState } from "react";
import { api, getWorkspace, getUser, type Artifact, type AuditRow, type ChainStatus, type PipelineDef, type ReportResult, type RunDetail, type RunSummary } from "../api";
import { FilesDiff } from "../components/FilesDiff";
import { ProjectWizard } from "../components/ProjectWizard";
import { ArtifactModal } from "../components/ArtifactModal";
import { Modal } from "../components/Modal";
import { Metrics } from "../components/Metrics";
import { Button } from "../components/ui/Button";
import { OrbitGraph } from "../components/OrbitGraph";
import { ScorecardView } from "../components/Scorecard";
import { Markdown } from "../components/Markdown";
import type { EvalResult, Regulation, LiveState, LiveRecord, Traceability } from "../api";
import { getTraceMode } from "../api";

const COLOR: Record<string, string> = {
  succeeded: "#3fb950", failed: "#f85149", waiting_gate: "#d29922",
  running: "#58a6ff", rejected: "#f85149", pending: "#8b949e",
};
const Dot = ({ s }: { s: string }) => <span className="dot" style={{ background: COLOR[s] ?? "#8b949e" }} />;
const ICON = (t: string) => (t === "gate" ? "◆" : t === "verifier" ? "✓" : "●");

export function RunsPage({ onDecided, focusRun }: { onDecided: () => void; focusRun?: string | null }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [step, setStep] = useState<AuditRow | null>(null);
  const [funcId, setFuncId] = useState("FUNC-MOIRA-audit-record");
  const [pipelines, setPipelines] = useState<PipelineDef[]>([]);
  const [pipelineId, setPipelineId] = useState("");
  const [backend, setBackend] = useState(getUser().backend);
  const [wizard, setWizard] = useState(false);
  const [report, setReport] = useState<ReportResult | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [reportTab, setReportTab] = useState<"preview" | "raw">("preview");
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [codePath, setCodePath] = useState<string | undefined>();
  const [chain, setChain] = useState<ChainStatus | null>(null);
  const [trace, setTrace] = useState<Traceability | null>(null);
  const [confBusy, setConfBusy] = useState(false);
  const [evalRes, setEvalRes] = useState<EvalResult | null>(null);
  const [evalBusy, setEvalBusy] = useState(false);
  const [rerunBusy, setRerunBusy] = useState(false);
  const [live, setLive] = useState<LiveState | null>(null);
  const [liveEvents, setLiveEvents] = useState<LiveRecord[]>([]);
  const liveStop = useRef(false);
  const [regs, setRegs] = useState<Regulation[]>([]);
  const [compOpen, setCompOpen] = useState(false);
  const [selRefs, setSelRefs] = useState<string[]>([]);

  useEffect(() => {
    if (!selected) { setChain(null); return; }
    api.verify(selected).then(setChain).catch(() => setChain(null));
  }, [selected, detail]);

  useEffect(() => {
    if (!selected) { setTrace(null); return; }
    api.runTraceability(selected).then(setTrace).catch(() => setTrace(null));
  }, [selected, detail]);

  useEffect(() => {
    api.workspaces().then((d) => {
      const ws = d.workspaces.find((w) => w.id === getWorkspace());
      setCodePath(ws?.code_path || undefined);
    }).catch(() => { /* */ });
  }, []);

  const genReport = async () => {
    if (!detail) return;
    setReportBusy(true);
    try { setReport(await api.report(detail.run.run_id)); } finally { setReportBusy(false); }
  };
  const openArtifact = async (id: string) => {
    try { setArtifact(await api.artifact(id)); } catch { /* not in repo */ }
  };
  const rerun = async () => {
    if (!detail) return;
    setRerunBusy(true);
    try {
      const res = await api.rerun(detail.run.run_id);
      await refresh(); setSelected(res.run_id); setStep(null);
    } catch { /* surfaced via run list */ } finally { setRerunBusy(false); }
  };
  const downloadDebug = async () => {
    if (!detail) return;
    const bundle = await api.debugBundle(detail.run.run_id);
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `moira-debug-${detail.run.run_id}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const runConformance = async () => {
    if (!trace?.func_id) return;
    setConfBusy(true);
    try { await api.evalConformance(trace.func_id); if (selected) setTrace(await api.runTraceability(selected)); }
    catch { /* surfaced via run list */ } finally { setConfBusy(false); }
  };
  const runEval = async () => {
    if (!detail) return;
    setEvalBusy(true);
    try { setEvalRes(await api.evalQuality(detail.run.run_id)); await refresh(); }
    catch (e) { setEvalRes({ run_id: "", status: "failed", kind: "quality",
      scorecard: { kind: "quality", criteria: [], overall: 0, missing: [], parsed: false, summary: String((e as Error)?.message || e) } }); }
    finally { setEvalBusy(false); }
  };
  const openCompliance = () => {
    setCompOpen(true); setSelRefs([]);
    if (regs.length === 0) api.regulations().then((d) => setRegs(d.regulations)).catch(() => { /* */ });
  };
  const runCompliance = async () => {
    if (!detail || selRefs.length === 0) return;
    setCompOpen(false); setEvalBusy(true);
    const funcId = (detail.audit.find((a) => a.lineage?.length)?.lineage || []).find((l) => l.startsWith("FUNC"));
    try { setEvalRes(await api.evalCompliance(selRefs, funcId ? { func_id: funcId } : undefined)); await refresh(); }
    catch (e) { setEvalRes({ run_id: "", status: "failed", kind: "compliance",
      scorecard: { kind: "compliance", criteria: [], overall: 0, missing: [], findings: [], parsed: false, summary: String((e as Error)?.message || e) } }); }
    finally { setEvalBusy(false); }
  };
  const downloadReport = () => {
    if (!report) return;
    const blob = new Blob([report.markdown], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${detail?.run.run_id}.md`; a.click();
  };

  const refresh = useCallback(async () => {
    try { setRuns((await api.runs()).runs); } catch { /* */ }
  }, []);
  const load = useCallback(async (id: string) => { try { setDetail(await api.run(id)); } catch { /* */ } }, []);

  useEffect(() => { refresh(); const t = setInterval(refresh, 2500); return () => clearInterval(t); }, [refresh]);
  // deep-link: when another page (e.g. Discovery) opens a specific run, select it
  useEffect(() => { if (focusRun) { setSelected(focusRun); setStep(null); refresh(); } }, [focusRun, refresh]);
  // live stream: tail the active node's reasoning/tools/tokens while the run executes
  useEffect(() => {
    if (!selected) return;
    liveStop.current = false;
    setLive(null); setLiveEvents([]);
    let from = 0;
    const loop = async () => {
      if (liveStop.current) return;
      try {
        const d = await api.liveRun(selected, from);
        if (liveStop.current) return;
        if (d.events.length) setLiveEvents((p) => [...p, ...d.events]);
        from = d.next; setLive(d);
        setTimeout(loop, d.status === "running" ? 1200 : 4000);
      } catch { setTimeout(loop, 3000); }
    };
    loop();
    return () => { liveStop.current = true; };
  }, [selected]);
  useEffect(() => {
    api.pipelines().then((d) => { setPipelines(d.pipelines); if (d.pipelines[0]) setPipelineId(d.pipelines[0].id); }).catch(() => { /* */ });
  }, []);
  useEffect(() => {
    if (!selected) return;
    load(selected); const t = setInterval(() => load(selected), 2500); return () => clearInterval(t);
  }, [selected, load]);

  const start = async () => {
    const res = await api.start({ func_id: funcId, pipeline_id: pipelineId, backend });
    await refresh(); setSelected(res.run_id); setStep(null); onDecided();
  };

  return (
    <div className="cols3">
      <aside className="c-left">
        <section className="panel">
          <h3>New run</h3>
          <Button variant="ghost" style={{ width: "100%" }} onClick={() => setWizard(true)}>✨ Guided run (from repo)</Button>
          <div className="or-sep">or quick start</div>
          <label>Func spec<input value={funcId} onChange={(e) => setFuncId(e.target.value)} /></label>
          <label>Pipeline
            <select value={pipelineId} onChange={(e) => setPipelineId(e.target.value)}>
              {pipelines.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label>Backend
            <select value={backend} onChange={(e) => setBackend(e.target.value)}>
              <option value="mock">mock (fast, offline)</option>
              <option value="claude_code">claude_code (real)</option>
              <option value="litellm">litellm (model-agnostic)</option>
            </select>
          </label>
          <Button variant="primary" style={{ width: "100%" }} onClick={start}>▶ Run pipeline</Button>
        </section>
        <section className="panel">
          <h3>Runs</h3>
          <div className="run-list">
            {runs.map((r) => (
              <div key={r.run_id} className={"run-row" + (selected === r.run_id ? " active" : "")}
                   onClick={() => { setSelected(r.run_id); setStep(null); }}>
                <Dot s={r.status} /><span className="rid">{r.run_id.replace("run-", "")}</span>
                <span className="rstatus">{r.status}</span>
              </div>
            ))}
            {runs.length === 0 && <div className="empty">No runs yet.</div>}
          </div>
        </section>
      </aside>

      <main className="c-center">
        {!detail ? <div className="placeholder">Select or start a run.</div> : (
          <>
            <div className="run-header">
              <Dot s={detail.run.status} /><strong>{detail.pipeline.name}</strong>
              <span className="muted">· {detail.run.run_id}</span>
              {(() => {
                const dur = detail.audit.reduce((a, x) => a + (x.duration || 0), 0);
                const c: Record<string, number> = {};
                detail.audit.forEach((a) => {
                  const inp = a.input as { model?: string; backend?: string };
                  const l = inp?.model && inp.model !== "(default)" ? inp.model : inp?.backend;
                  if (l) c[l] = (c[l] || 0) + 1;
                });
                const model = Object.entries(c).sort((x, y) => y[1] - x[1])[0]?.[0];
                return <Metrics m={{ usd: detail.cost.usd, tokens: detail.cost.tokens_in + detail.cost.tokens_out, duration: dur, model }} />;
              })()}
              {chain && (
                chain.sealed
                  ? <span className={"chain-badge " + (chain.ok ? "ok" : "bad")}
                          title={chain.ok ? `audit hash-chain verified · head ${chain.head.slice(0, 12)}` : `chain broken at record ${chain.broken_at}`}>
                      {chain.ok ? `🛡 audit verified (${chain.length})` : "⚠ audit chain broken"}
                    </span>
                  : <span className="chain-badge legacy" title="records written before hash-chaining">🛡 unsealed</span>
              )}
              {trace?.available && getTraceMode() !== "llm" && (() => {
                const c = trace.tasks;
                const lvl = c?.level ?? "none";
                const cls = lvl === "complete" ? "ok" : lvl === "partial" ? "warn" : "legacy";
                const label = c && c.tasks.total > 0
                  ? `🔗 ${c.tasks.done}/${c.tasks.total} tasks · ${c.ac.done}/${c.ac.total} AC`
                  : "🔗 not decomposed";
                const title = [
                  trace.spec?.present ? "spec ✓" : "spec ✗",
                  trace.tests?.present ? `tests ${trace.tests.ac_covered}/${trace.tests.ac_total} AC` : "no test plan",
                  c ? `tasks ${c.tasks.done}/${c.tasks.total} done` : "no tasks",
                  trace.lineage ? `lineage ${trace.lineage.resolved}/${trace.lineage.refs.length} resolved` : "",
                ].filter(Boolean).join(" · ");
                return <span className={"trace-badge " + cls} title={title}>{label}</span>;
              })()}
              {trace?.available && getTraceMode() !== "structural" && trace.conformance && (() => {
                const o = trace.conformance.overall;
                const cls = o >= 0.8 ? "ok" : o >= 0.5 ? "warn" : "legacy";
                return <span className={"trace-badge " + cls} title={trace.conformance.summary || "LLM conformance (spec ↔ code)"}>
                  ⚖ {Math.round(o * 100)}%</span>;
              })()}
              <span className="grow1" />
              <Button variant="ghost" size="sm" disabled={rerunBusy} onClick={rerun}
                title="Re-run this pipeline as a fresh run (same inputs)">{rerunBusy ? "…" : "↻ Re-run"}</Button>
              <Button variant="ghost" size="sm" disabled={evalBusy} onClick={runEval}
                title="Score this run's output quality (LLM-as-judge → scorecard)">
                {evalBusy ? "scoring…" : "⚖ Evaluate"}</Button>
              <Button variant="ghost" size="sm" disabled={evalBusy} onClick={openCompliance}
                title="Check code compliance against a chosen regulation (GDPR, WCAG, NIS2…)">🛡 Compliance</Button>
              <Button variant="ghost" size="sm" disabled={reportBusy} onClick={genReport}>{reportBusy ? "…" : "⤓ Report"}</Button>
              <Button variant="ghost" size="sm" onClick={downloadDebug}
                title="Download a reproducibility bundle: run state, events, live stream (incl. the exact command/prompt when MOIRA_DEBUG=1) + this run's sidecar log">🐞 Debug bundle</Button>
            </div>
            <section className="panel">
              <h3>Execution plan</h3>
              <div className="plan">
                {detail.pipeline.nodes.map((n) => {
                  const rec = detail.audit.find((a) => a.node_id === n.id);
                  // live status from run state (shows "running" while a node executes,
                  // before any audit record exists); fall back to the audit record.
                  const status = detail.state?.[n.id] ?? rec?.status ?? "pending";
                  return (
                    <div key={n.id} className={"plan-node " + (n.type === "gate" ? "gate " : "") + (step?.node_id === n.id ? "sel" : "")}
                         onClick={() => rec && setStep(rec)}>
                      <span className="nicon">{ICON(n.type)}</span>
                      <span className="nname">{n.name}</span>
                      {n.gate && <span className="gmode">{n.gate.mode}</span>}
                      <Dot s={status} />
                    </div>
                  );
                })}
              </div>
            </section>
            {(() => {
              // Outputs rollup: artifacts authored + files changed across all steps
              const artifacts = [...new Set(detail.audit.map((a) => (a.output as Record<string, unknown>)?.artifact as string).filter(Boolean))];
              const files = new Map<string, { status: string; add: number; del: number }>();
              detail.audit.forEach((a) => {
                ((a.output as Record<string, unknown>)?.files as { path: string; status?: string; additions?: number; deletions?: number }[] | undefined || []).forEach((f) => {
                  const cur = files.get(f.path) || { status: f.status || "M", add: 0, del: 0 };
                  files.set(f.path, { status: f.status || cur.status, add: cur.add + (f.additions || 0), del: cur.del + (f.deletions || 0) });
                });
              });
              const fileList = [...files.entries()];
              if (!artifacts.length && !fileList.length) return null;
              return (
                <section className="panel">
                  <h3>Outputs <span className="muted" style={{ fontSize: 12, textTransform: "none" }}>· what this run produced</span></h3>
                  {artifacts.length > 0 && (
                    <div className="out-artifacts">
                      {artifacts.map((id) => <button key={id} className="chip chip-btn" onClick={() => openArtifact(id)}>📄 {id}</button>)}
                    </div>
                  )}
                  {fileList.length > 0 && (
                    <ul className="out-files">
                      {fileList.map(([path, m]) => (
                        <li key={path}>
                          <span className={"fstat fstat-" + m.status}>{m.status}</span>
                          <code>{path}</code>
                          {m.add > 0 && <span className="add">+{m.add}</span>}
                          {m.del > 0 && <span className="del">−{m.del}</span>}
                          {codePath && <a className="open-ed" title="Open in VS Code" href={`vscode://file/${codePath}/${path}`}>↗</a>}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              );
            })()}
            {trace?.available && trace.func_id && (() => {
              const fid = trace.func_id;
              const c = trace.tasks;
              const acTotal = c?.ac.total || 0;
              const Bar = ({ label, frac, val }: { label: string; frac: number; val: string }) => (
                <div className="tr-row">
                  <span className="tr-name">{label}</span>
                  <span className="tr-bar"><span className="tr-fill" style={{ width: Math.round(Math.max(0, Math.min(1, frac)) * 100) + "%" }} /></span>
                  <span className="tr-val">{val}</span>
                </div>
              );
              return (
                <section className="panel">
                  <h3>Traceability <span className="muted" style={{ fontSize: 12, textTransform: "none" }}>· {fid} — spec ↔ tests ↔ tasks ↔ code</span></h3>
                  <div className="tr-grid">
                    <Bar label="Spec" frac={trace.spec?.present ? 1 : 0} val={trace.spec?.present ? "present" : "missing"} />
                    <Bar label="Tests" frac={acTotal ? (trace.tests?.ac_covered || 0) / acTotal : 0} val={trace.tests?.present ? `${trace.tests.ac_covered}/${acTotal} AC` : "no test plan"} />
                    <Bar label="Tasks" frac={c && c.tasks.total ? c.build_pct : 0} val={c && c.tasks.total ? `${c.tasks.done}/${c.tasks.total} done · ${c.ac.done}/${acTotal} AC` : (c?.has_epic ? "epic, no tasks" : "not decomposed")} />
                    <Bar label="Lineage" frac={trace.lineage?.refs.length ? (trace.lineage.resolved / trace.lineage.refs.length) : (trace.lineage?.present ? 1 : 0)} val={trace.lineage?.refs.length ? `${trace.lineage.resolved}/${trace.lineage.refs.length} resolved` : "—"} />
                  </div>
                  <div className="tr-chips">
                    {fid && <button className="chip chip-btn func" onClick={() => openArtifact(fid)}>📄 {fid}</button>}
                    {(trace.lineage?.refs || []).map((id) => <button key={id} className="chip chip-btn" onClick={() => openArtifact(id)}>{id}</button>)}
                  </div>
                  {getTraceMode() !== "structural" && (
                    <div className="tr-conf">
                      {trace.conformance ? (
                        <>
                          <Bar label="⚖ LLM" frac={trace.conformance.overall}
                               val={`${Math.round(trace.conformance.overall * 100)}% conformance`} />
                          {trace.conformance.summary && <p className="tr-conf-sum muted">{trace.conformance.summary}</p>}
                          {(trace.conformance.missing || []).length > 0 && (
                            <ul className="tr-conf-gaps">{trace.conformance.missing.slice(0, 4).map((m, i) => <li key={i}>{m}</li>)}</ul>
                          )}
                          <button className="chip chip-btn" disabled={confBusy} onClick={runConformance}>{confBusy ? "scoring…" : "↻ re-run conformance"}</button>
                        </>
                      ) : (
                        <div className="tr-row">
                          <span className="muted small" style={{ flex: 1 }}>No LLM conformance score yet (spec ↔ code).</span>
                          <button className="chip chip-btn" disabled={confBusy} onClick={runConformance}>{confBusy ? "scoring…" : "⚖ Run conformance"}</button>
                        </div>
                      )}
                    </div>
                  )}
                </section>
              );
            })()}
            {(() => {
              const lin = (detail.audit.find((a) => a.lineage?.length)?.lineage || []);
              const fnId = lin.find((l) => l.startsWith("FUNC")) || lin[0] || detail.run.run_id;
              const srcs = lin.filter((l) => l !== fnId);
              return srcs.length > 0 ? (
                <section className="panel">
                  <h3>Context orbit <span className="muted" style={{ fontSize: 12 }}>· what was in the model's analysis</span></h3>
                  <OrbitGraph center={{ label: fnId, kind: fnId.startsWith("FUNC") ? "FUNC" : "RUN" }} size={300}
                              sources={srcs.map((id) => ({ id }))} onOpen={openArtifact} />
                </section>
              ) : null;
            })()}
            {(live?.status === "running" || liveEvents.length > 0) && (
              <section className="panel">
                <h3>Live{" "}
                  <span className="muted" style={{ fontSize: 12, textTransform: "none" }}>
                    {live?.active_node ? `· ▶ ${live.active_node} ` : ""}
                    · ⏱ {live?.elapsed ?? 0}s · ⛁ {(live?.tokens_in || 0) + (live?.tokens_out || 0)} tok
                  </span>
                </h3>
                <div className="log live-log">
                  {liveEvents.map((e, i) => (
                    <div className={"live-row lr-" + e.kind} key={i}>
                      <span className="lr-ic">{e.kind === "tool" ? "🔧" : e.kind === "result" ? "✓" : e.kind === "debug" ? "🐞" : "💬"}</span>
                      <span className="lr-text">{e.text}</span>
                    </div>
                  ))}
                  {liveEvents.length === 0 && <div className="muted small">waiting for the model…</div>}
                </div>
              </section>
            )}
            <section className="panel grow">
              <h3>Activity log</h3>
              <div className="log">
                {detail.events.map((e) => (
                  <div className={"log-row k-" + e.kind.split(".")[0]} key={e.seq}>
                    <span className="seq">{e.seq}</span><span className="kind">{e.kind}</span><span className="lmsg">{e.message}</span>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </main>

      <aside className="c-right">
        <section className="panel grow">
          <h3>Audit record</h3>
          {!step ? <div className="placeholder small">Click a step in the execution plan.</div> : (
            <div className="audit">
              <div className="arow"><b>{step.node_name}</b> <Dot s={step.status} /></div>
              <div className="field"><span className="flabel">owner</span><span className="fval">{step.owner}</span></div>
              {step.decisions.length > 0 && <div className="block"><div className="flabel">decisions</div><ul>{step.decisions.map((d, i) => <li key={i}>{d}</li>)}</ul></div>}
              {step.tools.length > 0 && <div className="field"><span className="flabel">tools</span><span className="fval">{step.tools.join(", ")}</span></div>}
              {step.approvals.length > 0 && <div className="block"><div className="flabel">approvals</div><ul>{step.approvals.map((a, i) => <li key={i}>{a.decision} by {a.by} — {a.confirmed}</li>)}</ul></div>}
              {step.lineage.length > 0 && (
                <div className="block"><div className="flabel">lineage (click to trace)</div>
                  <div className="lineage-chips">
                    {step.lineage.map((l) => (
                      <button key={l} className="chip chip-btn" onClick={() => openArtifact(l)}>{l}</button>
                    ))}
                  </div>
                </div>
              )}
              <div className="field"><span className="flabel">cost</span><span className="fval">${step.cost.usd ?? 0} · {step.duration.toFixed(2)}s</span></div>
              {(() => {
                const o: Record<string, unknown> = step.output || {};
                const rest = Object.fromEntries(
                  Object.entries(o).filter(([k]) => !["files", "patch", "truncated"].includes(k)));
                return (
                  <>
                    <FilesDiff output={o} codePath={codePath} />
                    {Object.keys(rest).length > 0 && (
                      <div className="block"><div className="flabel">output</div>
                        <pre>{JSON.stringify(rest, null, 2)}</pre></div>
                    )}
                  </>
                );
              })()}
            </div>
          )}
        </section>
      </aside>

      {wizard && (
        <ProjectWizard onClose={() => setWizard(false)}
          onStarted={async (id) => { setWizard(false); await refresh(); setSelected(id); setStep(null); onDecided(); }} />
      )}
      {report && (
        <Modal eyebrow="Run report" title="Audit report (Markdown)" onClose={() => setReport(null)}
          footer={<>
            {report.committed && <span className="muted small">committed → <code>{report.path}</code></span>}
            <span className="grow1" />
            <Button variant="ghost" onClick={downloadReport}>⤓ Download .md</Button>
            <Button variant="primary" onClick={() => setReport(null)}>Done</Button>
          </>}>
          <div className="seg" style={{ width: 220, marginBottom: 12 }}>
            <button className={"seg-btn" + (reportTab === "preview" ? " on" : "")} onClick={() => setReportTab("preview")}>Preview</button>
            <button className={"seg-btn" + (reportTab === "raw" ? " on" : "")} onClick={() => setReportTab("raw")}>Markdown</button>
          </div>
          {reportTab === "preview"
            ? <Markdown md={report.markdown} />
            : <pre className="artifact-text">{report.markdown}</pre>}
        </Modal>
      )}
      {artifact && <ArtifactModal artifact={artifact} onClose={() => setArtifact(null)} onOpen={openArtifact} />}
      {evalRes && (
        <Modal eyebrow="Quality evaluation" title="Scorecard"
          onClose={() => setEvalRes(null)}
          footer={<>
            {evalRes.run_id && <span className="muted small">this evaluation is an audited run <code>{evalRes.run_id.replace("run-", "")}</code></span>}
            <span className="grow1" />
            {evalRes.run_id && <Button variant="ghost" onClick={() => { setSelected(evalRes.run_id); setEvalRes(null); }}>Open run</Button>}
            <Button variant="primary" onClick={() => setEvalRes(null)}>Done</Button>
          </>}>
          <ScorecardView sc={evalRes.scorecard} />
        </Modal>
      )}
      {compOpen && (
        <Modal eyebrow="Compliance" title="Check regulatory compliance"
          onClose={() => setCompOpen(false)}
          footer={<>
            <span className="muted small">{selRefs.length} selected · evaluates code in code_path</span>
            <span className="grow1" />
            <Button variant="ghost" onClick={() => setCompOpen(false)}>Cancel</Button>
            <Button variant="primary" disabled={selRefs.length === 0} onClick={runCompliance}>🛡 Check</Button>
          </>}>
          <div className="hint">Pick regulations to check. A read-only auditor reviews the code and returns a scorecard with findings and severity.</div>
          <div className="reg-pick">
            {regs.map((r) => {
              const on = selRefs.includes(r.id);
              return (
                <label key={r.id} className={"reg-opt" + (on ? " on" : "")}>
                  <input type="checkbox" checked={on}
                    onChange={() => setSelRefs((s) => on ? s.filter((x) => x !== r.id) : [...s, r.id])} />
                  <span className="reg-id">{r.id}</span>
                  <span className="reg-title">{r.title}</span>
                </label>
              );
            })}
            {regs.length === 0 && <div className="empty small">No regulation corpus in this repo (.ai/standards/compliance).</div>}
          </div>
        </Modal>
      )}
    </div>
  );
}
