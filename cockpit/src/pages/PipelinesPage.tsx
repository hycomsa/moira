import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position, MarkerType, addEdge,
  useNodesState, useEdgesState,
  type Connection, type Node, type Edge, type NodeProps, type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, type AgentDef, type FuncSpec, type PipelineDefRaw, type PipelineNodeDef } from "../api";
import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Select";
import { Input } from "../components/ui/Input";

const TYPE_COLOR: Record<string, string> = {
  producer: "#3b82f6", verifier: "#2ea043", auto_check: "#1f9c8b", gate: "#d29922",
};
const ICON: Record<string, string> = { gate: "◆", verifier: "✓", auto_check: "⚙", producer: "▦" };
const MODELS = ["", "opus", "sonnet", "haiku"];
const BACKENDS = ["", "mock", "claude_code", "litellm"];

type NodeData = {
  label: string; role?: string; ntype: string; desc?: string; model?: string;
  props?: PipelineNodeDef; onDelete: (id: string) => void;
};

// ---------- rich node card ----------
function MoiraNode({ id, data, selected }: NodeProps) {
  const d = data as NodeData;
  const c = TYPE_COLOR[d.ntype] ?? "#777";
  const isGate = d.ntype === "gate", isCheck = d.ntype === "auto_check";
  return (
    <div className={"rf-node2" + (selected ? " sel" : "")} style={{ ["--nc" as string]: c }}>
      <Handle type="target" position={Position.Left} className="rf-port" />
      <div className="rfn-head">
        <span className="rfn-grip">⠿</span>
        <span className="rfn-chip" style={{ background: c }}>{ICON[d.ntype] || "●"}</span>
        <div className="rfn-titles">
          <div className="rfn-title">{d.label}</div>
          {d.role && <div className="rfn-role">{d.role}</div>}
        </div>
        <span className="rfn-badge" style={{ color: c, borderColor: c }}>{d.ntype.replace("_", "-")}</span>
        <button className="rfn-del" title="delete" onClick={(e) => { e.stopPropagation(); d.onDelete(id); }}>✕</button>
      </div>
      {d.desc && <div className="rfn-desc">{d.desc}</div>}
      <div className="rfn-foot">
        {isGate ? <span className="rfn-pill" style={{ borderColor: c, color: c }}>{d.model}</span>
          : isCheck ? <code className="rfn-cmd">{d.desc ? "" : "shell"}</code>
            : <span className="rfn-model">model <b>{d.model || "default"}</b></span>}
      </div>
      <Handle type="source" position={Position.Right} className="rf-port" />
    </div>
  );
}
const nodeTypes = { moira: MoiraNode };

// ---------- edges ----------
const depEdge = (s: string, t: string): Edge => ({
  id: `e-${s}-${t}`, source: s, target: t, type: "smoothstep",
  markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: "var(--muted)", strokeWidth: 2 }, interactionWidth: 18,
});
const rejectEdge = (s: string, t: string): Edge => ({
  id: `r-${s}-${t}`, source: s, target: t, label: "reject", animated: true, data: { reject: true }, type: "smoothstep",
  style: { stroke: "#f85149", strokeWidth: 2, strokeDasharray: "5 4" },
  markerEnd: { type: MarkerType.ArrowClosed, color: "#f85149" }, labelStyle: { fill: "#f85149", fontSize: 10 }, interactionWidth: 18,
});

function layout(defNodes: PipelineNodeDef[]): Record<string, { x: number; y: number }> {
  const ids = new Set(defNodes.map((n) => n.id));
  const anyDeps = defNodes.some((n) => (n.depends_on || []).length > 0);
  const preds: Record<string, string[]> = {};
  defNodes.forEach((n, i) => { preds[n.id] = (anyDeps ? (n.depends_on || []) : (i > 0 ? [defNodes[i - 1].id] : [])).filter((p) => ids.has(p)); });
  const depth: Record<string, number> = {};
  const calc = (id: string, seen: Set<string>): number => {
    if (id in depth) return depth[id];
    if (seen.has(id)) return 0; seen.add(id);
    const ps = preds[id]; const v = ps.length ? Math.max(...ps.map((p) => calc(p, seen))) + 1 : 0; depth[id] = v; return v;
  };
  defNodes.forEach((n) => calc(n.id, new Set()));
  const byLayer: Record<number, string[]> = {};
  defNodes.forEach((n) => { (byLayer[depth[n.id]] ||= []).push(n.id); });
  const pos: Record<string, { x: number; y: number }> = {};
  Object.entries(byLayer).forEach(([d, lids]) => lids.forEach((id, j) => { pos[id] = { x: 60 + Number(d) * 340, y: 40 + j * 150 }; }));
  return pos;
}

export function PipelinesPage() {
  const [ids, setIds] = useState<string[]>([]);
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [pid, setPid] = useState("");
  const [pname, setPname] = useState("");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [rfi, setRfi] = useState<ReactFlowInstance | null>(null);
  const [dirty, setDirty] = useState(false);
  const [funcId, setFuncId] = useState("");
  const [funcs, setFuncs] = useState<FuncSpec[]>([]);
  const [backend, setBackend] = useState("mock");
  const [msg, setMsg] = useState("");
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const agent = useCallback((aid?: string) => agents.find((a) => a.id === aid), [agents]);
  const agentName = useCallback((aid?: string) => agent(aid)?.name || aid || "?", [agent]);
  const agentType = useCallback((aid?: string) => agent(aid)?.type || "producer", [agent]);
  const touch = () => setDirty(true);

  // effective model label shown on a node card
  const modelOf = useCallback((p: PipelineNodeDef) => p.model || agent(p.agent)?.model || "", [agent]);

  const removeNode = useCallback((id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
    setSelId((s) => (s === id ? null : s)); touch();
  }, [setNodes, setEdges]);

  const nodeData = useCallback((n: PipelineNodeDef, ntype: string): NodeData => {
    if (ntype === "gate") return { label: n.id, role: n.gate?.persona ? `gate · ${n.gate.persona}` : "gate", ntype, model: n.gate?.mode, props: n, onDelete: removeNode, desc: "Human / auto quality gate" };
    if (ntype === "auto_check") return { label: n.id, role: "auto-check", ntype, props: n, onDelete: removeNode, desc: n.check_cmd };
    const a = agent(n.agent);
    return { label: agentName(n.agent), role: a?.role || n.agent, ntype, desc: a?.description, model: modelOf(n), props: n, onDelete: removeNode };
  }, [agent, agentName, modelOf, removeNode]);

  const loadInto = useCallback(async (id: string) => {
    const def = await api.pipelineDef(id);
    const pos = layout(def.nodes);
    const anyDeps = def.nodes.some((n) => (n.depends_on || []).length > 0);
    const rfNodes: Node[] = def.nodes.map((n, i) => {
      const ntype = (n.type === "gate" || n.gate) ? "gate" : n.type === "auto_check" ? "auto_check" : agentType(n.agent);
      return { id: n.id, type: "moira", position: pos[n.id] || { x: 60, y: i * 150 }, width: 260, data: nodeData(n, ntype) };
    });
    const rfEdges: Edge[] = [];
    def.nodes.forEach((n, i) => {
      const deps = anyDeps ? (n.depends_on || []) : (i > 0 ? [def.nodes[i - 1].id] : []);
      deps.forEach((s) => rfEdges.push(depEdge(s, n.id)));
      if (n.on_reject_goto) rfEdges.push(rejectEdge(n.id, n.on_reject_goto));
    });
    setNodes(rfNodes); setEdges(rfEdges); setPid(id); setPname(def.name); setSelId(null); setDirty(false);
  }, [agentType, nodeData, setNodes, setEdges]);

  const loadList = useCallback(async () => {
    const r = await api.pipelines(); setIds(r.pipelines.map((p) => p.id));
    const want = new URLSearchParams(window.location.search).get("edit") || r.pipelines[0]?.id;
    if (want) await loadInto(want);
  }, [loadInto]);

  useEffect(() => {
    api.agents().then((d) => setAgents(d.agents)).catch(() => {});
    api.funcs().then((d) => { setFuncs(d.funcs); setFuncId((f) => (f && d.funcs.some((x) => x.id === f)) ? f : (d.funcs[0]?.id || f)); }).catch(() => {});
    loadList().catch(() => {});
    /* eslint-disable-next-line */
  }, []);
  useEffect(() => {
    if (!rfi || !nodes.length) return;
    const ts = [220, 600].map((d) => setTimeout(() => rfi.fitView({ padding: 0.2, duration: 200 }), d));
    return () => ts.forEach(clearTimeout);
    /* eslint-disable-next-line */
  }, [pid, rfi]);

  const sel = nodes.find((n) => n.id === selId) || null;
  const selProps = (sel?.data as NodeData)?.props || null;
  const selType = sel ? (sel.data as NodeData).ntype : "";
  const isGate = selType === "gate", isCheck = selType === "auto_check";
  const depsIn = useMemo(() => edges.filter((e) => e.target === selId && !(e.data as { reject?: boolean })?.reject).map((e) => e.source), [edges, selId]);
  const feedsOut = useMemo(() => edges.filter((e) => e.source === selId && !(e.data as { reject?: boolean })?.reject).map((e) => e.target), [edges, selId]);

  const reaches = (start: string, target: string): boolean => {
    const adj: Record<string, string[]> = {};
    edges.filter((e) => !(e.data as { reject?: boolean })?.reject).forEach((e) => { (adj[e.target] ||= []).push(e.source); });
    const st = [start]; const seen = new Set<string>();
    while (st.length) { const c = st.pop()!; for (const p of adj[c] || []) { if (p === target) return true; if (!seen.has(p)) { seen.add(p); st.push(p); } } }
    return false;
  };
  const onConnect = (c: Connection) => {
    if (!c.source || !c.target || c.source === c.target) return;
    if (edges.some((e) => e.source === c.source && e.target === c.target)) return;
    if (reaches(c.source, c.target)) { setMsg("That connection would create a cycle."); return; }
    setEdges((es) => addEdge(depEdge(c.source!, c.target!), es)); touch();
  };
  const onEdgeClick = (_: unknown, edge: Edge) => { setEdges((es) => es.filter((e) => e.id !== edge.id)); touch(); };

  const patch = (id: string, p: Partial<PipelineNodeDef>) => {
    setNodes((ns) => ns.map((n) => {
      if (n.id !== id) return n;
      const props = { ...(n.data as NodeData).props!, ...p };
      const ntype = (n.data as NodeData).ntype;
      return { ...n, data: nodeData(props, ntype) };
    })); touch();
  };
  const setReject = (srcId: string, target: string) => {
    setEdges((es) => {
      const kept = es.filter((e) => !((e.data as { reject?: boolean })?.reject && e.source === srcId));
      return target ? [...kept, rejectEdge(srcId, target)] : kept;
    }); touch();
  };
  const rejectTargetOf = (srcId: string) => edges.find((e) => (e.data as { reject?: boolean })?.reject && e.source === srcId)?.target || "";

  const uid = (base: string) => { let i = 0, id = base; const has = (x: string) => nodes.some((n) => n.id === x); while (has(id)) id = `${base}-${++i}`; return id; };
  const nextPos = () => ({ x: nodes.length ? Math.max(...nodes.map((n) => n.position.x)) + 340 : 80, y: 60 });
  const addRF = (ntype: string, props: PipelineNodeDef, pos?: { x: number; y: number }) => {
    setNodes((ns) => [...ns, { id: props.id, type: "moira", position: pos || nextPos(), width: 260, data: nodeData(props, ntype) }]);
    setSelId(props.id); touch();
  };
  const addAgent = (a: AgentDef, pos?: { x: number; y: number }) => addRF(a.type, { id: uid(a.id), agent: a.id, max_retries: 2 }, pos);
  const addCheck = (pos?: { x: number; y: number }) => { const id = uid("check"); addRF("auto_check", { id, type: "auto_check", check_cmd: "pytest -q" }, pos); };
  const addGate = (pos?: { x: number; y: number }) => { const id = uid("gate"); addRF("gate", { id, type: "gate", gate: { mode: "auto", persona: "lead-dev" } }, pos); };

  // drag & drop from palette
  const onDragStart = (e: React.DragEvent, payload: object) => { e.dataTransfer.setData("application/moira", JSON.stringify(payload)); e.dataTransfer.effectAllowed = "move"; };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const raw = e.dataTransfer.getData("application/moira"); if (!raw || !rfi) return;
    const p = JSON.parse(raw) as { kind: string; id?: string };
    const pos = rfi.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    if (p.kind === "gate") addGate(pos);
    else if (p.kind === "check") addCheck(pos);
    else { const a = agents.find((x) => x.id === p.id); if (a) addAgent(a, pos); }
  };

  const toDef = (): PipelineDefRaw => ({
    id: pid || pname.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "pipeline",
    name: pname || "Pipeline",
    nodes: nodes.map((n) => {
      const p = { ...(n.data as NodeData).props! };
      p.depends_on = edges.filter((e) => e.target === n.id && !(e.data as { reject?: boolean })?.reject).map((e) => e.source);
      const rj = edges.find((e) => e.source === n.id && (e.data as { reject?: boolean })?.reject);
      p.on_reject_goto = rj ? rj.target : null;
      if (!p.model) delete p.model;
      if (!p.backend) delete p.backend;
      return p;
    }),
  });

  const newPipeline = () => { setNodes([]); setEdges([]); setPid(""); setPname("New Pipeline"); setSelId(null); setDirty(true); };
  const save = async () => { const def = toDef(); await api.savePipeline(def); setPid(def.id); const r = await api.pipelines(); setIds(r.pipelines.map((p) => p.id)); setDirty(false); setMsg("Saved to repo."); };
  const del = async () => { if (!pid || !window.confirm(`Delete pipeline "${pid}"?`)) return; await api.deletePipeline(pid); const r = await api.pipelines(); setIds(r.pipelines.map((p) => p.id)); const f = r.pipelines[0]?.id; if (f) loadInto(f); else newPipeline(); };
  const run = async () => { if (dirty || !pid) await save(); const res = await api.start({ func_id: funcId, pipeline_id: toDef().id, backend }); setMsg(`Started run ${String(res.run_id).replace("run-", "")} (${res.status}). See Runs.`); };

  const displayEdges = useMemo(() => edges.map((e) => (selId && (e.source === selId || e.target === selId)) ? { ...e, style: { ...e.style, strokeWidth: 3 } } : e), [edges, selId]);

  // palette grouped by category + search
  const groups = useMemo(() => {
    const q = search.toLowerCase();
    const g: Record<string, AgentDef[]> = {};
    agents.filter((a) => !q || (a.name + a.role + a.category).toLowerCase().includes(q))
      .forEach((a) => { (g[a.category || "other"] ||= []).push(a); });
    return g;
  }, [agents, search]);

  return (
    <div className="builder-page">
      <div className="builder-bar glass">
        <Select style={{ width: 168 }} value={pid} onChange={(e) => loadInto(e.target.value)} title="pipeline">
          {ids.map((id) => <option key={id} value={id}>{id}</option>)}
          {pid === "" && <option value="">(new — unsaved)</option>}
        </Select>
        <Input style={{ width: 180 }} value={pname} onChange={(e) => { setPname(e.target.value); touch(); }} placeholder="Pipeline name" />
        <Button variant="ghost" onClick={newPipeline}>+ New</Button>
        <Button variant="ghost" onClick={del} disabled={!pid}>Delete</Button>
        <span className="grow1" />
        <span className="bar-lbl">run vs</span>
        <Select style={{ width: 210 }} value={funcId} onChange={(e) => setFuncId(e.target.value)} title="run the pipeline against this func-spec">
          {funcs.length === 0 && <option value={funcId}>{funcId || "(no func-specs in repo)"}</option>}
          {funcs.map((f) => <option key={f.id} value={f.id} title={f.title}>{f.id}</option>)}
        </Select>
        <Select style={{ width: 140 }} value={backend} onChange={(e) => setBackend(e.target.value)} title="backend">
          <option value="mock">mock</option><option value="claude_code">claude_code</option><option value="litellm">litellm</option>
        </Select>
        <Button variant="ghost" onClick={run}>▶ Run</Button>
        <Button variant="primary" onClick={save} disabled={!dirty && !!pid}>{dirty ? "Save *" : "Save"}</Button>
      </div>
      {msg && <div className="builder-msg" onClick={() => setMsg("")}>{msg}</div>}

      <div className="builder3">
        {/* palette */}
        <aside className="palette2">
          <div className="pal-title">Palette</div>
          <input className="pal-search" placeholder="Search agents…" value={search} onChange={(e) => setSearch(e.target.value)} />
          <div className="pal-group">
            <div className="pal-ghead" onClick={() => setCollapsed((c) => ({ ...c, _logic: !c._logic }))}>
              <span>{collapsed._logic ? "▸" : "▾"}</span> Logic
            </div>
            {!collapsed._logic && <>
              <div className="pal-card" draggable onDragStart={(e) => onDragStart(e, { kind: "gate" })} onClick={() => addGate()}>
                <span className="pc-chip" style={{ background: TYPE_COLOR.gate }}>◆</span>
                <div><div className="pc-name">Gate</div><div className="pc-sub">human / auto quality gate</div></div>
              </div>
              <div className="pal-card" draggable onDragStart={(e) => onDragStart(e, { kind: "check" })} onClick={() => addCheck()}>
                <span className="pc-chip" style={{ background: TYPE_COLOR.auto_check }}>⚙</span>
                <div><div className="pc-name">Auto Check</div><div className="pc-sub">run a real command (pytest/lint)</div></div>
              </div>
            </>}
          </div>
          {Object.entries(groups).map(([cat, list]) => (
            <div className="pal-group" key={cat}>
              <div className="pal-ghead" onClick={() => setCollapsed((c) => ({ ...c, [cat]: !c[cat] }))}>
                <span>{collapsed[cat] ? "▸" : "▾"}</span> {cat} <span className="pal-count">{list.length}</span>
              </div>
              {!collapsed[cat] && list.map((a) => (
                <div className="pal-card" key={a.id} draggable
                     onDragStart={(e) => onDragStart(e, { kind: "agent", id: a.id })} onClick={() => addAgent(a)}>
                  <span className="pc-chip" style={{ background: TYPE_COLOR[a.type] }}>{ICON[a.type] || "●"}</span>
                  <div><div className="pc-name">{a.name}</div><div className="pc-sub">{a.description || a.role}</div></div>
                </div>
              ))}
            </div>
          ))}
        </aside>

        {/* canvas */}
        <div className="canvas-wrap" onDrop={onDrop} onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}>
          <div className="flow-canvas">
            <ReactFlow nodes={nodes} edges={displayEdges} nodeTypes={nodeTypes}
              onInit={setRfi} nodesDraggable elementsSelectable
              onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
              onConnect={onConnect} onEdgeClick={onEdgeClick}
              onNodeClick={(_, n) => setSelId(n.id)} onPaneClick={() => setSelId(null)}
              deleteKeyCode={["Backspace", "Delete"]} proOptions={{ hideAttribution: true }}
              defaultEdgeOptions={{ type: "smoothstep" }} minZoom={0.3}>
              <Background color="var(--border)" gap={24} size={1.5} />
              <Controls showInteractive={false} fitViewOptions={{ padding: 0.2 }} />
              <MiniMap pannable zoomable className="bld-minimap"
                nodeColor={(n) => TYPE_COLOR[(n.data as NodeData).ntype] ?? "#777"}
                maskColor="color-mix(in srgb, var(--bg) 70%, transparent)" />
            </ReactFlow>
          </div>
        </div>

        {/* node settings */}
        <aside className="inspector2">
          {!sel || !selProps ? (
            <div className="insp-empty">
              <div className="insp-empty-ic">◆</div>
              <div>Select a node to configure it,<br />or drag one from the palette.</div>
            </div>
          ) : (
            <>
              <div className="insp-head">
                <span className="rfn-chip" style={{ background: TYPE_COLOR[selType] }}>{ICON[selType]}</span>
                <div className="insp-head-t"><div className="insp-title">{sel.id}</div>
                  <div className="insp-type" style={{ color: TYPE_COLOR[selType] }}>{selType.replace("_", "-")}</div></div>
                <button className="icon-btn" title="delete node" onClick={() => removeNode(sel.id)}>✕</button>
              </div>

              {!isGate && !isCheck && (
                <>
                  <div className="cfg-sec">
                    <div className="cfg-label">Agent</div>
                    <select className="cfg-input" value={selProps.agent || ""} onChange={(e) => patch(sel.id, { agent: e.target.value })}>
                      {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </select>
                  </div>
                  <div className="cfg-sec">
                    <div className="cfg-label">Model settings</div>
                    <div className="cfg-sub">Requested model <span className="muted">(per-node override)</span></div>
                    <select className="cfg-input" value={selProps.model || ""} onChange={(e) => patch(sel.id, { model: e.target.value })}>
                      {MODELS.map((m) => <option key={m} value={m}>{m || `(agent default${agent(selProps.agent)?.model ? `: ${agent(selProps.agent)!.model}` : ""})`}</option>)}
                    </select>
                    <div className="cfg-sub" style={{ marginTop: 8 }}>Backend</div>
                    <div className="seg">
                      {BACKENDS.map((b) => (
                        <button key={b} className={"seg-btn" + ((selProps.backend || "") === b ? " on" : "")}
                          onClick={() => patch(sel.id, { backend: b })}>{b || "default"}</button>
                      ))}
                    </div>
                  </div>
                  <div className="cfg-sec">
                    <div className="cfg-label">Max retries before gate</div>
                    <div className="stepper">
                      <button onClick={() => patch(sel.id, { max_retries: Math.max(0, (selProps.max_retries ?? 2) - 1) })}>−</button>
                      <span>{selProps.max_retries ?? 2}</span>
                      <button onClick={() => patch(sel.id, { max_retries: (selProps.max_retries ?? 2) + 1 })}>+</button>
                    </div>
                  </div>
                </>
              )}

              {isCheck && (
                <div className="cfg-sec">
                  <div className="cfg-label">Check command</div>
                  <input className="cfg-input mono" value={selProps.check_cmd || ""} placeholder="pytest -q"
                    onChange={(e) => patch(sel.id, { check_cmd: e.target.value })} />
                </div>
              )}

              {isGate && (
                <>
                  <div className="cfg-sec">
                    <div className="cfg-label">Gate mode</div>
                    <div className="seg">
                      {["auto", "hybrid", "human", "off"].map((m) => (
                        <button key={m} className={"seg-btn" + ((selProps.gate?.mode || "auto") === m ? " on" : "")}
                          onClick={() => patch(sel.id, { gate: { ...selProps.gate, mode: m } })}>{m}</button>
                      ))}
                    </div>
                  </div>
                  <div className="cfg-sec">
                    <div className="cfg-label">Persona</div>
                    <input className="cfg-input" value={selProps.gate?.persona || ""} placeholder="lead-dev / client / ciso"
                      onChange={(e) => patch(sel.id, { gate: { ...selProps.gate, mode: selProps.gate?.mode || "auto", persona: e.target.value } })} />
                  </div>
                  {selProps.gate?.mode === "hybrid" && (
                    <div className="cfg-sec">
                      <div className="cfg-label">Confidence routing</div>
                      <div className="cfg-sub">auto-approve ≥ <b>{(selProps.gate?.high_cutoff ?? 0.85).toFixed(2)}</b></div>
                      <input type="range" min="0.5" max="1" step="0.01" value={selProps.gate?.high_cutoff ?? 0.85}
                        onChange={(e) => patch(sel.id, { gate: { ...selProps.gate!, high_cutoff: parseFloat(e.target.value) } })} />
                      <div className="cfg-sub">auto-reject ≤ <b>{(selProps.gate?.low_cutoff ?? 0.5).toFixed(2)}</b></div>
                      <input type="range" min="0" max="0.8" step="0.01" value={selProps.gate?.low_cutoff ?? 0.5}
                        onChange={(e) => patch(sel.id, { gate: { ...selProps.gate!, low_cutoff: parseFloat(e.target.value) } })} />
                    </div>
                  )}
                </>
              )}

              <div className="cfg-sec">
                <div className="cfg-label">Reject → goto</div>
                <select className="cfg-input" value={rejectTargetOf(sel.id)} onChange={(e) => setReject(sel.id, e.target.value)}>
                  <option value="">(none)</option>
                  {nodes.filter((x) => x.id !== sel.id).map((x) => <option key={x.id} value={x.id}>{x.id}</option>)}
                </select>
              </div>

              <div className="cfg-sec io">
                <div><div className="cfg-label">Dependencies (in)</div>
                  {depsIn.length ? depsIn.map((d) => <span key={d} className="io-chip in">{d}</span>) : <span className="muted small">none</span>}</div>
                <div><div className="cfg-label">Feeds (out)</div>
                  {feedsOut.length ? feedsOut.map((d) => <span key={d} className="io-chip out">{d}</span>) : <span className="muted small">none</span>}</div>
              </div>
              <div className="cfg-hint">Wire dependencies by dragging a node's right port → another's left port.</div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
