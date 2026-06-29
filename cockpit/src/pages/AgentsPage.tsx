import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, getUiState, setUiState, type AgentDef } from "../api";
import { Modal } from "../components/Modal";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Help } from "../components/ui/Help";

const CAT_COLOR: Record<string, string> = {
  analysis: "#58a6ff", design: "#a371f7", implementation: "#1f6feb",
  generation: "#3fb950", security: "#f85149", testing: "#d29922", general: "#8b949e",
};
const BLANK: AgentDef = {
  id: "", name: "", type: "producer", category: "analysis", role: "",
  backend: "mock", model: "", effort: "", description: "", tools_policy: "reasoning",
  system_prompt: "", skill_refs: [],
};
const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];

export function AgentsPage({ focusAgent, onFocusConsumed }: {
  focusAgent?: string | null;
  onFocusConsumed?: () => void;
} = {}) {
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [q, setQ] = useState(() => getUiState().agentQuery || "");
  const [editing, setEditing] = useState<AgentDef | null>(null);
  // last agent the user opened — restored (highlight + scroll) on a plain page return.
  const lastId = useRef(getUiState().agentId || "").current;
  const didScroll = useRef(false);

  // open an agent's editor when deep-linked from a pipeline node (node→agent jump)
  useEffect(() => {
    if (!focusAgent || agents.length === 0) return;
    const a = agents.find((x) => x.id === focusAgent);
    if (a) { setEditing({ ...BLANK, ...a }); setUiState({ agentId: a.id }); }
    onFocusConsumed?.();
  }, [focusAgent, agents, onFocusConsumed]);

  // remember the search filter across page switches
  useEffect(() => { setUiState({ agentQuery: q }); }, [q]);

  // scroll the last-opened card into view once, on return
  const highlightRef = useCallback((el: HTMLDivElement | null) => {
    if (el && !didScroll.current) { didScroll.current = true; el.scrollIntoView({ block: "center" }); }
  }, []);
  const [importDir, setImportDir] = useState("");
  const [importing, setImporting] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importMsg, setImportMsg] = useState("");

  const doImport = async () => {
    if (!importDir.trim()) return;
    setImportBusy(true); setImportMsg("");
    try {
      const r = await api.importAgents(importDir) as { imported: number };
      setImportMsg(`Imported ${r.imported} agents.`); load();
    } catch (e) { setImportMsg(String((e as Error)?.message || e)); }
    setImportBusy(false);
  };

  const load = () => api.agents().then((d) => setAgents(d.agents)).catch(() => { /* */ });
  useEffect(() => { load(); }, []);

  const filtered = useMemo(
    () => agents.filter((a) => (a.id + a.name + (a.description || "")).toLowerCase().includes(q.toLowerCase())),
    [agents, q]
  );
  const byCat = useMemo(() => {
    const m: Record<string, AgentDef[]> = {};
    for (const a of filtered) (m[a.category] ||= []).push(a);
    return m;
  }, [filtered]);

  const save = async () => {
    if (!editing) return;
    const def = { ...editing, id: editing.id || editing.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") };
    await api.saveAgent(def);
    setEditing(null); load();
  };
  const del = async (id: string) => {
    if (!window.confirm(`Delete agent "${id}"?`)) return;
    await api.deleteAgent(id); load();
  };

  return (
    <div className="page">
      <h2>Agents <span className="muted">· {agents.length} defined in repo (.ai/context/agents)</span></h2>
      <div className="toolbar">
        <Input placeholder="Search agents…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} />
        <Button variant="ghost" onClick={() => { setImporting(true); setImportMsg(""); }}>⤓ Import</Button>
        <Button variant="primary" onClick={() => setEditing({ ...BLANK })}>+ New agent</Button>
      </div>

      {Object.entries(byCat).map(([cat, list]) => (
        <div className="agent-group" key={cat}>
          <div className="group-head"><i style={{ background: CAT_COLOR[cat] ?? "#8b949e" }} /> {cat}</div>
          <div className="agent-grid">
            {list.map((a) => (
              <div className="agent-card" key={a.id}
                   ref={a.id === lastId ? highlightRef : undefined}
                   onClick={() => { setEditing({ ...BLANK, ...a }); setUiState({ agentId: a.id }); }}
                   style={{
                     borderLeft: `3px solid ${CAT_COLOR[a.category] ?? "var(--border)"}`,
                     ...(a.id === lastId ? { boxShadow: "0 0 0 2px var(--accent)" } : {}),
                   }}>
                <div className="ac-top">
                  <span className="ac-type" data-t={a.type}>{a.type === "verifier" ? "✓ verifier" : "● producer"}</span>
                  <button className="ac-del" onClick={(e) => { e.stopPropagation(); del(a.id); }}>✕</button>
                </div>
                <div className="ac-name">{a.name}</div>
                <div className="ac-desc">{a.description}</div>
                <div className="ac-foot">
                  <span className="chip sm">{a.backend}</span>
                  <span className="chip sm">{a.tools_policy}</span>
                  {a.model && <span className="chip sm model" title="cross-model">⨯ {a.model}</span>}
                  {(a.skill_refs?.length ?? 0) > 0 && <span className="chip sm">✦ {a.skill_refs!.length}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
      {agents.length === 0 && <div className="panel"><div className="empty">No agents yet — click "New agent" or "Import".</div></div>}

      {importing && (
        <Modal eyebrow="Import agents" title="Pull from a subagent collection" onClose={() => setImporting(false)}
          footer={<>
            <Button variant="ghost" onClick={() => setImporting(false)}>Close</Button>
            <span className="grow1" />
            <Button variant="primary" disabled={importBusy} onClick={doImport}>
              {importBusy ? "Importing…" : "Import"}
            </Button>
          </>}>
          <p className="muted" style={{ marginTop: 0 }}>
            Point at a folder of Claude Code subagent <code>.md</code> files (e.g. a cloned
            VoltAgent / wshobson / 0xfurai collection). Each is converted to a git-native
            agent in this workspace's <code>.ai/context/agents</code>.
          </p>
          <div className="field-lg">
            <label>Folder path</label>
            <input value={importDir} onChange={(e) => setImportDir(e.target.value)}
                   placeholder="/path/to/cloned/agents" autoFocus />
          </div>
          {importMsg && <div style={{ fontSize: 12, color: "var(--accent)" }}>{importMsg}</div>}
        </Modal>
      )}

      {editing && (
        <div className="drawer-overlay" onClick={() => setEditing(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <h3>{editing.id ? "Edit agent" : "New agent"}</h3>
            <label>Name <Help text="Display name shown in the palette and on pipeline nodes. Cosmetic — the agent is referenced internally by its id." /><input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
            <label>Type <Help text="producer creates artifacts (specs, code); verifier reviews them and emits findings that feed a gate. Sets the node's kind in a pipeline." />
              <select value={editing.type} onChange={(e) => setEditing({ ...editing, type: e.target.value })}>
                <option value="producer">producer</option><option value="verifier">verifier</option>
              </select>
            </label>
            <label>Category <Help text="Grouping for the Agents list / palette only (analysis, design, implementation…). Organizational — does not affect how the agent runs." />
              <select value={editing.category} onChange={(e) => setEditing({ ...editing, category: e.target.value })}>
                {["analysis", "design", "implementation", "generation", "security", "testing", "general"].map((c) => <option key={c}>{c}</option>)}
              </select>
            </label>
            <label>Role (backend key) <Help text="The key the backend uses to choose behaviour: prompt persona, tool access (reasoning/read-only vs full), time/turn budget tier, eval mode, and Superpowers loading. The most behaviour-defining field — defaults to the agent id." /><input value={editing.role} onChange={(e) => setEditing({ ...editing, role: e.target.value })} placeholder="e.g. code-generator" /></label>
            <label>Default backend <Help text="Engine that runs the agent: mock (offline/tests), claude_code (Claude CLI), litellm (frontier + local routing). Overridable per node or per run." />
              <select value={editing.backend} onChange={(e) => setEditing({ ...editing, backend: e.target.value })}>
                <option value="mock">mock</option><option value="claude_code">claude_code</option><option value="litellm">litellm</option>
              </select>
            </label>
            <label>Model (optional) <Help text="Model hint, e.g. opus or ollama/qwen2.5-coder. Empty = backend default. Lets you wire a specific model per agent (cross-model pipelines)." /><input value={editing.model} onChange={(e) => setEditing({ ...editing, model: e.target.value })} placeholder="e.g. ollama/qwen2.5-coder" /></label>
            <label>Reasoning effort <Help text="How hard the model thinks when running this agent. Empty = backend default. Applied via --effort (claude_code) and reasoning_effort (litellm); the CLI downgrades a level the chosen model doesn't support. A run or per-node override beats this default." />
              <select value={editing.effort || ""} onChange={(e) => setEditing({ ...editing, effort: e.target.value })}>
                {EFFORTS.map((o) => <option key={o} value={o}>{o || "backend default"}</option>)}
              </select>
            </label>
            <label>Tools policy <Help text="Intended tool access: reasoning = tool-light (analysis/review), coding = full read/write/run. Advisory metadata today — the effective tool profile is derived from the agent's role." />
              <select value={editing.tools_policy} onChange={(e) => setEditing({ ...editing, tools_policy: e.target.value })}>
                <option value="reasoning">reasoning (tool-light)</option><option value="coding">coding (full tools)</option>
              </select>
            </label>
            <label>Description <Help text="Free text shown on the agent card. Documentation only — no effect on execution." /><textarea value={editing.description} onChange={(e) => setEditing({ ...editing, description: e.target.value })} /></label>
            <label>System prompt (optional) <Help text="Extra instructions for this agent, appended to the END of its task prompt (applied on claude_code and litellm) to shape behaviour/voice on top of what the role provides. Empty = none. Unlike tools_policy / skill_refs, this one IS used at run time." /><textarea value={editing.system_prompt} onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })} /></label>
            <label>Skill refs (comma-separated) <Help text="AI SDLC skills (e.g. ba@shape-func-spec) associated with this agent, for reference. Advisory today — to actually drive a skill in a run, add a skill node in a pipeline." />
              <input value={(editing.skill_refs || []).join(", ")} onChange={(e) => setEditing({ ...editing, skill_refs: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </label>
            <div className="drawer-actions">
              <Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
              <Button variant="primary" onClick={save}>Save to repo</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
