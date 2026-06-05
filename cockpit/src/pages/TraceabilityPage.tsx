import { useEffect, useMemo, useState } from "react";
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position, MarkerType,
  type Node, type Edge, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, type Artifact, type TraceFunc } from "../api";
import { ArtifactModal } from "../components/ArtifactModal";
import { Metrics } from "../components/Metrics";

const COLOR: Record<string, string> = {
  succeeded: "#3fb950", failed: "#f85149", waiting_gate: "#d29922",
  running: "#58a6ff", rejected: "#f85149", pending: "#8b949e",
};
const KIND_COLOR: Record<string, string> = { INT: "#a371f7", REQ: "#d29922", FUNC: "#58a6ff", RUN: "#3fb950" };
const Dot = ({ s }: { s: string }) => <span className="dot" style={{ background: COLOR[s] ?? "#8b949e" }} />;
const kindOf = (id: string) => id.split("-", 1)[0].toUpperCase();

// ---- graph node ----
function TraceNode({ data }: NodeProps) {
  const d = data as { kind: string; label: string; sub?: string; status?: string };
  const c = d.kind === "RUN" ? COLOR[d.status || "pending"] : KIND_COLOR[d.kind] ?? "#777";
  return (
    <div className={"tg-node tg-" + d.kind.toLowerCase()} style={{ ["--tc" as string]: c }}>
      <Handle type="target" position={Position.Left} className="tg-h" />
      <div className="tg-kind" style={{ color: c }}>{d.kind === "RUN" ? "run" : d.kind}</div>
      <div className="tg-label">{d.label}</div>
      {d.sub && <div className="tg-sub">{d.sub}</div>}
      <Handle type="source" position={Position.Right} className="tg-h" />
    </div>
  );
}
const nodeTypes = { trace: TraceNode };

export function TraceabilityPage() {
  const [funcs, setFuncs] = useState<TraceFunc[]>([]);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [view, setView] = useState<"list" | "graph">("list");

  useEffect(() => { api.traceability().then((d) => setFuncs(d.funcs)).catch(() => { /* */ }); }, []);
  const open = async (id: string) => { try { setArtifact(await api.artifact(id)); } catch { /* */ } };

  // build graph: INT/REQ → FUNC → RUN, columns by kind
  const { gNodes, gEdges } = useMemo(() => {
    const cols: Record<string, number> = { INT: 0, REQ: 1, FUNC: 2, RUN: 3 };
    const seen = new Set<string>();
    const nodes: Node[] = [];
    const edges: Edge[] = [];
    const rowOf: Record<number, number> = { 0: 0, 1: 0, 2: 0, 3: 0 };
    const place = (id: string, kind: string, label: string, sub?: string, status?: string) => {
      if (seen.has(id)) return; seen.add(id);
      const col = cols[kind] ?? 2;
      nodes.push({ id, type: "trace", position: { x: col * 270, y: rowOf[col] * 92 }, width: 220,
        data: { kind, label, sub, status } });
      rowOf[col] += 1;
    };
    funcs.forEach((f) => {
      const ups = f.lineage.filter((l) => l !== f.id && (kindOf(l) === "INT" || kindOf(l) === "REQ" || kindOf(l) === "ADR"));
      ups.forEach((u) => place(u, kindOf(u), u));
      place(f.id, "FUNC", f.id, f.title);
      ups.forEach((u) => edges.push({ id: `e-${u}-${f.id}`, source: u, target: f.id, type: "smoothstep",
        style: { stroke: "var(--muted)", strokeWidth: 1.5 }, markerEnd: { type: MarkerType.ArrowClosed } }));
      f.runs.forEach((r) => {
        place(r.run_id, "RUN", r.run_id.replace("run-", "").slice(0, 8), r.model, r.status);
        edges.push({ id: `e-${f.id}-${r.run_id}`, source: f.id, target: r.run_id, type: "smoothstep",
          style: { stroke: COLOR[r.status] ?? "var(--muted)", strokeWidth: 1.5 }, markerEnd: { type: MarkerType.ArrowClosed } });
      });
    });
    return { gNodes: nodes, gEdges: edges };
  }, [funcs]);

  return (
    <div className={view === "graph" ? "trace-graph-page" : "page"}>
      <div className="trace-head-bar">
        <h2 style={{ margin: 0 }}>Traceability <span className="muted">· spec → requirement → intent → runs</span></h2>
        <span className="grow1" />
        <div className="seg" style={{ width: 180 }}>
          <button className={"seg-btn" + (view === "list" ? " on" : "")} onClick={() => setView("list")}>List</button>
          <button className={"seg-btn" + (view === "graph" ? " on" : "")} onClick={() => setView("graph")}>Graph</button>
        </div>
      </div>

      {view === "graph" ? (
        <div className="trace-canvas">
          <ReactFlow nodes={gNodes} edges={gEdges} nodeTypes={nodeTypes} fitView
            nodesConnectable={false} elementsSelectable
            onNodeClick={(_, n) => { const k = (n.data as { kind: string }).kind; if (k !== "RUN") open(n.id); }}
            proOptions={{ hideAttribution: true }} minZoom={0.2}>
            <Background color="var(--border)" gap={24} size={1.5} />
            <Controls showInteractive={false} />
            <MiniMap pannable className="bld-minimap"
              nodeColor={(n) => { const d = n.data as { kind: string; status?: string }; return d.kind === "RUN" ? (COLOR[d.status || ""] ?? "#777") : (KIND_COLOR[d.kind] ?? "#777"); }} />
          </ReactFlow>
        </div>
      ) : (
        <>
          {funcs.length === 0 && <div className="panel"><div className="empty">No func-specs in this workspace's repo.</div></div>}
          {funcs.map((f) => {
            const upstream = f.lineage.filter((l) => l !== f.id && !l.startsWith("FUNC"));
            return (
              <div className="panel trace-card" key={f.id}>
                <div className="trace-grid">
                  <div className="trace-col">
                    <div className="trace-label">Upstream</div>
                    {upstream.length === 0 && <span className="muted small">—</span>}
                    {upstream.map((l) => <button key={l} className="chip chip-btn block-chip" onClick={() => open(l)}>{l}</button>)}
                  </div>
                  <div className="trace-arrow">→</div>
                  <div className="trace-col">
                    <div className="trace-label">Func-spec</div>
                    <button className="chip chip-btn block-chip func" onClick={() => open(f.id)}>{f.id}</button>
                    <div className="trace-title">{f.title}</div>
                  </div>
                  <div className="trace-arrow">→</div>
                  <div className="trace-col">
                    <div className="trace-label">Runs ({f.runs.length})</div>
                    {f.runs.length === 0 && <span className="muted small">no runs yet</span>}
                    {f.runs.map((r) => (
                      <div className="trace-run" key={r.run_id}>
                        <Dot s={r.status} /><span className="rid">{r.run_id.replace("run-", "").slice(0, 8)}</span>
                        <Metrics m={r} compact />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </>
      )}
      {artifact && <ArtifactModal artifact={artifact} onClose={() => setArtifact(null)} onOpen={open} />}
    </div>
  );
}
