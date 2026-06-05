import { useEffect, useMemo, useState } from "react";
import { api, getUser, type Skill } from "../api";
import { Input } from "../components/ui/Input";
import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Select";
import { Modal } from "../components/Modal";

const AUTHORS = /^(ba@|arch@)/;
const PERSONAS = ["ba", "po", "architect", "client", "lead-dev"];

interface Step { skill: string; persona: string }
interface Preset { id: string; name: string; desc: string; steps: Step[] }

// Chained discovery pipelines (A3): ordered BA skills, each gated by a human.
const PRESETS: Preset[] = [
  { id: "workshop-req-func", name: "Workshop → Requirements → Func-spec",
    desc: "From raw workshop notes/transcript to discovered requirements, then a func-spec for the first new REQ. Topic = path to the notes (e.g. .ai/context/_input/…).",
    steps: [{ skill: "ba@discover-requirements", persona: "po" },
            { skill: "ba@shape-func-spec", persona: "po" }] },
  { id: "workshop-req-func-validate", name: "Workshop → Requirements → Func-spec → Validate",
    desc: "Same as above, plus a quality validation of the produced func-spec (gated by an architect). Topic = path to the notes.",
    steps: [{ skill: "ba@discover-requirements", persona: "po" },
            { skill: "ba@shape-func-spec", persona: "po" },
            { skill: "ba@validate-func-spec", persona: "architect" }] },
  { id: "int-req-func", name: "Intent → Requirements → Func-spec",
    desc: "From a topic to a shaped func-spec, gated at each step.",
    steps: [{ skill: "ba@shape-intent-spec", persona: "ba" },
            { skill: "ba@discover-requirements", persona: "po" },
            { skill: "ba@shape-func-spec", persona: "po" }] },
  { id: "shape-validate-fix", name: "Shape → Validate → Fix func-spec",
    desc: "Draft a func-spec, validate quality, fix the gaps.",
    steps: [{ skill: "ba@shape-func-spec", persona: "ba" },
            { skill: "ba@validate-func-spec", persona: "po" },
            { skill: "ba@fix-func-spec", persona: "architect" }] },
  { id: "func-review", name: "Func-spec review — PO + Architect",
    desc: "Business then technical review of an existing func-spec.",
    steps: [{ skill: "ba@review-func-spec-po", persona: "po" },
            { skill: "ba@review-func-spec-arch", persona: "architect" }] },
];

export function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  // single-skill run
  const [run, setRun] = useState<Skill | null>(null);
  const [input, setInput] = useState("");
  const [elab, setElab] = useState("");
  const [persona, setPersona] = useState(getUser().persona);

  // pipeline run
  const [pipe, setPipe] = useState<Preset | null>(null);
  const [pTopic, setPTopic] = useState("");
  const [pElab, setPElab] = useState("");
  const [pSteps, setPSteps] = useState<Step[]>([]);

  useEffect(() => { api.skills().then((d) => setSkills(d.skills)).catch(() => { /* */ }); }, []);
  const filtered = useMemo(
    () => skills.filter((s) => (s.name + s.description).toLowerCase().includes(q.toLowerCase())), [skills, q]);

  const startSingle = async () => {
    if (!run) return; setBusy(true); setMsg("");
    try {
      const res = await api.runDiscovery({ skill: run.name, input, elaboration: elab, persona });
      setMsg(`Started ${res.run_id.replace("run-", "")} — running. Watch it in Runs/Activity; the review gate appears in the Inbox when ready.`);
      setRun(null); setInput(""); setElab("");
    } catch (e) { setMsg(String((e as Error)?.message || e)); }
    setBusy(false);
  };

  const openPipe = (p: Preset) => { setPipe(p); setPSteps(p.steps.map((s) => ({ ...s }))); setPTopic(""); setPElab(""); setMsg(""); };
  const startPipe = async () => {
    if (!pipe) return; setBusy(true); setMsg("");
    try {
      // only the first step gets the topic; later steps inherit the prior step's artifact id
      const steps = pSteps.map((s, i) => ({ skill: s.skill, input: i === 0 ? pTopic : "", elaboration: pElab, persona: s.persona }));
      const res = await api.runDiscoveryPipeline(steps, pipe.name);
      setMsg(`Started pipeline ${res.run_id.replace("run-", "")} — running. Watch it in Runs/Activity; the first gate appears in the Inbox when ready.`);
      setPipe(null);
    } catch (e) { setMsg(String((e as Error)?.message || e)); }
    setBusy(false);
  };

  return (
    <div className="page">
      <h2>Discovery <span className="muted">· drive AI SDLC skills to author intents / requirements / func-specs</span></h2>
      {msg && <div className="builder-msg" onClick={() => setMsg("")}>{msg}</div>}

      <div className="disc-presets">
        {PRESETS.map((p) => (
          <div className="disc-preset" key={p.id} onClick={() => openPipe(p)}>
            <div className="dp-name">{p.name}</div>
            <div className="dp-desc">{p.desc}</div>
            <div className="dp-steps">{p.steps.map((s, i) => (
              <span key={i}><span className="dp-chip">{s.skill}</span>{i < p.steps.length - 1 && <span className="dp-arr">→</span>}</span>
            ))}</div>
          </div>
        ))}
      </div>

      <div className="toolbar"><Input placeholder="Search skills…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 260 }} /></div>
      <div className="panel">
        <table className="tbl">
          <thead><tr><th>Skill</th><th>Group</th><th>Description</th><th></th></tr></thead>
          <tbody>
            {filtered.map((s) => (
              <tr key={s.name}>
                <td className="mono">{s.name}</td>
                <td><span className="chip sm">{s.group}</span></td>
                <td className="muted">{s.description}</td>
                <td style={{ textAlign: "right" }}>
                  {AUTHORS.test(s.name) && <Button variant="ghost" size="sm" onClick={() => { setRun(s); setMsg(""); }}>▶ Run</Button>}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && <tr><td colSpan={4} className="empty">No skills match.</td></tr>}
          </tbody>
        </table>
      </div>

      {/* single skill */}
      {run && (
        <Modal eyebrow="Discovery run" title={run.name} onClose={() => setRun(null)}
          footer={<><Button variant="ghost" onClick={() => setRun(null)}>Cancel</Button><span className="grow1" />
            <Button variant="primary" disabled={busy} onClick={startSingle}>{busy ? "Starting…" : "▶ Run skill"}</Button></>}>
          <div className="hint">{run.description}</div>
          <div className="field-lg"><label>Input <span className="muted">(topic / REQ-ID / notes path / artifact id)</span></label>
            <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="e.g. REQ-APP-03 · or a topic / notes path" style={{ width: "100%" }} /></div>
          <div className="field-lg"><label>Prompt elaboration <span className="muted">(specialize the skill)</span></label>
            <textarea className="gate-note" value={elab} onChange={(e) => setElab(e.target.value)} placeholder="extra guidance appended to the skill…" /></div>
          <div className="field-lg"><label>Review gate persona</label>
            <Select value={persona} onChange={(e) => setPersona(e.target.value)} style={{ width: 180 }}>
              {PERSONAS.map((p) => <option key={p} value={p}>{p}</option>)}</Select></div>
          <div className="hint">Runs in the AI SDLC repo (writes the artifact), then pauses for review in the Inbox. Real model cost.</div>
        </Modal>
      )}

      {/* chained pipeline */}
      {pipe && (
        <Modal eyebrow="Discovery pipeline" title={pipe.name} onClose={() => setPipe(null)}
          footer={<><Button variant="ghost" onClick={() => setPipe(null)}>Cancel</Button><span className="grow1" />
            <Button variant="primary" disabled={busy} onClick={startPipe}>{busy ? "Starting…" : "▶ Run pipeline"}</Button></>}>
          <div className="hint">{pipe.desc} Each step authors in the AI SDLC repo, then pauses at a human gate in the Inbox before the next.</div>
          <div className="field-lg"><label>Topic / input <span className="muted">(shared starting point for the steps)</span></label>
            <Input value={pTopic} onChange={(e) => setPTopic(e.target.value)} placeholder="e.g. driver onboarding · REQ-APP-03 · notes path" style={{ width: "100%" }} /></div>
          <div className="field-lg"><label>Prompt elaboration <span className="muted">(applied to every step)</span></label>
            <textarea className="gate-note" value={pElab} onChange={(e) => setPElab(e.target.value)} placeholder="focus areas / constraints / tone…" /></div>
          <div className="review-label" style={{ marginTop: 12 }}>Steps</div>
          {pSteps.map((s, i) => (
            <div className="disc-step" key={i}>
              <span className="ds-n">{i + 1}</span>
              <code className="ds-skill">{s.skill}</code>
              <span className="muted small">{i === 0 ? "input: topic" : "input: ← prev artifact"}</span>
              <span className="grow1" />
              <span className="muted small">gate</span>
              <Select value={s.persona} onChange={(e) => setPSteps((st) => st.map((x, j) => j === i ? { ...x, persona: e.target.value } : x))} style={{ width: 140 }}>
                {PERSONAS.map((p) => <option key={p} value={p}>{p}</option>)}</Select>
            </div>
          ))}
        </Modal>
      )}
    </div>
  );
}
