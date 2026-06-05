import { useEffect, useState } from "react";
import { Modal } from "./Modal";
import { api, getUser, type FuncSpec, type PipelineDef } from "../api";
import { Button } from "./ui/Button";
import { OrbitGraph } from "./OrbitGraph";

// Guided run, grounded in the AI SDLC repo (NOT a free-form objective box):
// pick a git-native func-spec -> a git-native pipeline -> backend -> start.
const STEPS = ["Spec", "Pipeline", "Backend", "Review & start"];
const ICON = (t: string) => (t === "gate" ? "◆" : t === "verifier" ? "✓" : t === "auto_check" ? "▸" : "●");

const BACKENDS = [
  { id: "mock", title: "Mock", sub: "fast, offline — dry-run the flow" },
  { id: "claude_code", title: "Claude Code", sub: "real frontier coding agent ($)" },
  { id: "litellm", title: "LiteLLM", sub: "model-agnostic / local (no lock-in)" },
];

export function ProjectWizard({ onClose, onStarted }: {
  onClose: () => void; onStarted: (runId: string) => void;
}) {
  const [step, setStep] = useState(0);
  const [funcs, setFuncs] = useState<FuncSpec[]>([]);
  const [pipelines, setPipelines] = useState<PipelineDef[]>([]);
  const [fn, setFn] = useState<FuncSpec | null>(null);
  const [pipe, setPipe] = useState<PipelineDef | null>(null);
  const [backend, setBackend] = useState(getUser().backend);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.funcs().then((d) => setFuncs(d.funcs)).catch(() => { /* */ });
    api.pipelines().then((d) => { setPipelines(d.pipelines); }).catch(() => { /* */ });
  }, []);

  const canNext = (step === 0 && fn) || (step === 1 && pipe) || step === 2 || step === 3;

  const start = async () => {
    if (!fn || !pipe) return;
    setBusy(true); setErr("");
    try {
      const res = await api.start({ func_id: fn.id, pipeline_id: pipe.id, backend });
      onStarted(res.run_id);
    } catch (e) { setErr(String((e as Error)?.message || e)); setBusy(false); }
  };

  const gates = (pipe?.nodes || []).filter((n) => n.type === "gate");

  return (
    <Modal eyebrow="Guided run" title="Assemble a run from the AI SDLC repo" onClose={onClose}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <span className="grow1" />
        {step > 0 && <Button variant="ghost" onClick={() => setStep(step - 1)}>Back</Button>}
        {step < 3
          ? <Button variant="primary" disabled={!canNext} onClick={() => setStep(step + 1)}>Next</Button>
          : <Button variant="primary" disabled={busy || !fn || !pipe} onClick={start}>{busy ? "Starting…" : "▶ Start run"}</Button>}
      </>}>
      <div className="wiz-steps">
        {STEPS.map((s, i) => (
          <div key={s} className={"wiz-step" + (i === step ? " on" : "") + (i < step ? " done" : "")}>
            <span className="wiz-num">{i < step ? "✓" : i + 1}</span>{s}
          </div>
        ))}
      </div>

      {/* 1 — func-spec (the grounded intent) */}
      {step === 0 && (
        <div className="wiz-body">
          <div className="hint">Pick a functional spec from the repo — the run traces to it (lineage is preserved in the audit).</div>
          {funcs.length === 0 && <div className="empty">No func-specs in this workspace's repo.</div>}
          {funcs.map((f) => (
            <div key={f.id} className={"opt-card wide" + (fn?.id === f.id ? " sel" : "")} onClick={() => setFn(f)}>
              <div className="oc-title">{f.title}</div>
              <div className="oc-sub"><code>{f.id}</code></div>
              {f.lineage.length > 0 && <div className="lineage-chips">{f.lineage.map((l) => <span key={l} className="chip">{l}</span>)}</div>}
            </div>
          ))}
        </div>
      )}

      {/* 2 — pipeline (git-native definition) */}
      {step === 1 && (
        <div className="wiz-body">
          <div className="hint">Pick a pipeline. Its agents, dependencies and gates are defined as YAML in the repo.</div>
          {pipelines.map((p) => (
            <div key={p.id} className={"opt-card wide" + (pipe?.id === p.id ? " sel" : "")} onClick={() => setPipe(p)}>
              <div className="oc-title">{p.name}</div>
              <div className="oc-sub">{p.nodes.length} steps · {p.nodes.filter((n) => n.type === "gate").length} gate(s)</div>
            </div>
          ))}
          {pipe && (
            <div className="plan-preview">
              {pipe.nodes.map((n) => (
                <div key={n.id} className={"pp-node" + (n.type === "gate" ? " gate" : "")}>
                  <span className="nicon">{ICON(n.type)}</span>{n.name}
                  {n.gate && <span className="gmode">{n.gate.mode}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 3 — backend */}
      {step === 2 && (
        <div className="wiz-body">
          <div className="hint">How should the agent steps execute?</div>
          <div className="card-row">
            {BACKENDS.map((b) => (
              <div key={b.id} className={"opt-card" + (backend === b.id ? " sel" : "")} onClick={() => setBackend(b.id)}>
                <div className="oc-title">{b.title}</div>
                <div className="oc-sub">{b.sub}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4 — review */}
      {step === 3 && (
        <div className="wiz-body">
          <div className="rev-row"><span className="rev-k">Spec</span><span className="rev-v">{fn?.title} <code>{fn?.id}</code></span></div>
          {fn && fn.lineage.length > 0 && <div className="rev-row"><span className="rev-k">Lineage</span><span className="rev-v">{fn.lineage.join(" → ")}</span></div>}
          <div className="rev-row"><span className="rev-k">Pipeline</span><span className="rev-v">{pipe?.name} ({pipe?.nodes.length} steps)</span></div>
          <div className="rev-row"><span className="rev-k">Gates</span><span className="rev-v">{gates.length ? gates.map((g) => `${g.name} (${g.gate?.mode})`).join(", ") : "none"}</span></div>
          <div className="rev-row"><span className="rev-k">Backend</span><span className="rev-v">{BACKENDS.find((b) => b.id === backend)?.title}</span></div>
          {fn && fn.lineage.filter((l) => l !== fn.id).length > 0 && (
            <div className="preflight-orbit">
              <div className="review-label" style={{ marginTop: 10 }}>Context in the model's orbit (pre-flight)</div>
              <OrbitGraph center={{ label: fn.id, kind: "FUNC" }} size={300}
                          sources={fn.lineage.filter((l) => l !== fn.id).map((id) => ({ id }))} />
            </div>
          )}
          {backend === "claude_code" && <div className="hint">Real frontier agent — this run will incur model cost.</div>}
          {err && <div style={{ color: "#f85149", fontSize: 12, marginTop: 6 }}>{err}</div>}
        </div>
      )}
    </Modal>
  );
}
