import { useEffect, useMemo, useState } from "react";
import { api, type AgentDef } from "../api";
import { Modal } from "../components/Modal";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";

const CAT_COLOR: Record<string, string> = {
  analysis: "#58a6ff", design: "#a371f7", implementation: "#1f6feb",
  generation: "#3fb950", security: "#f85149", testing: "#d29922", general: "#8b949e",
};
const BLANK: AgentDef = {
  id: "", name: "", type: "producer", category: "analysis", role: "",
  backend: "mock", model: "", description: "", tools_policy: "reasoning",
  system_prompt: "", skill_refs: [],
};

export function AgentsPage() {
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<AgentDef | null>(null);
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
              <div className="agent-card" key={a.id} onClick={() => setEditing({ ...BLANK, ...a })}
                   style={{ borderLeft: `3px solid ${CAT_COLOR[a.category] ?? "var(--border)"}` }}>
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
            <label>Name<input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
            <label>Type
              <select value={editing.type} onChange={(e) => setEditing({ ...editing, type: e.target.value })}>
                <option value="producer">producer</option><option value="verifier">verifier</option>
              </select>
            </label>
            <label>Category
              <select value={editing.category} onChange={(e) => setEditing({ ...editing, category: e.target.value })}>
                {["analysis", "design", "implementation", "generation", "security", "testing", "general"].map((c) => <option key={c}>{c}</option>)}
              </select>
            </label>
            <label>Role (backend key)<input value={editing.role} onChange={(e) => setEditing({ ...editing, role: e.target.value })} placeholder="e.g. code-generator" /></label>
            <label>Default backend
              <select value={editing.backend} onChange={(e) => setEditing({ ...editing, backend: e.target.value })}>
                <option value="mock">mock</option><option value="claude_code">claude_code</option><option value="litellm">litellm</option>
              </select>
            </label>
            <label>Model (optional)<input value={editing.model} onChange={(e) => setEditing({ ...editing, model: e.target.value })} placeholder="e.g. ollama/qwen2.5-coder" /></label>
            <label>Tools policy
              <select value={editing.tools_policy} onChange={(e) => setEditing({ ...editing, tools_policy: e.target.value })}>
                <option value="reasoning">reasoning (tool-light)</option><option value="coding">coding (full tools)</option>
              </select>
            </label>
            <label>Description<textarea value={editing.description} onChange={(e) => setEditing({ ...editing, description: e.target.value })} /></label>
            <label>System prompt (optional)<textarea value={editing.system_prompt} onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })} /></label>
            <label>Skill refs (comma-separated)
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
