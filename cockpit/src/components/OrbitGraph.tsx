// Provenance "orbit": a glowing center node ringed by its source artifacts on
// concentric dotted rings. Used for artifact provenance, run pre-flight context,
// and post-factum run context. Pure SVG/CSS, no deps.
//
// Clicking a node selects it and slides out a detail panel (type · id · what it is)
// with an optional "Open full details →" link (onOpen). Clicking it again, or the ×,
// closes the panel.

import { useState } from "react";

const KIND: Record<string, string> = {
  INT: "#a371f7", REQ: "#d29922", FUNC: "#58a6ff", ADR: "#39c5cf", DOC: "#22d3ee", RUN: "#3fb950",
};
const KIND_LABEL: Record<string, string> = {
  INT: "Intent spec", REQ: "Requirement", FUNC: "Func-spec", ADR: "Architecture decision", DOC: "Document", RUN: "Run",
};
const KIND_DESC: Record<string, string> = {
  INT: "An upstream intent this work derives from.",
  REQ: "A requirement that feeds this func-spec.",
  FUNC: "The functional spec at the centre of this analysis.",
  ADR: "An architecture decision constraining this work.",
  DOC: "A supporting document in the model's context.",
  RUN: "A prior run in this lineage.",
};
export const kindOf = (id: string) => (id.split("-", 1)[0] || "").toUpperCase();

export interface OrbitSource { id: string; label?: string; kind?: string }

export function OrbitGraph({ center, sources, onOpen, size = 340 }: {
  center: { label: string; kind?: string };
  sources: OrbitSource[];
  onOpen?: (id: string) => void;
  size?: number;
}) {
  const [sel, setSel] = useState<OrbitSource | null>(null);
  const cx = size / 2, cy = size / 2, R = size * 0.37, R2 = R * 0.6;
  const n = sources.length;
  const pos = (i: number) => {
    const a = (i / Math.max(1, n)) * 2 * Math.PI - Math.PI / 2;
    const r = n > 5 && i % 2 ? R2 : R;
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  };
  const selKind = sel ? (sel.kind || kindOf(sel.id)) : "";
  return (
    <div className="orbit-wrap">
      <div className="orbit" style={{ height: size }}>
        <svg className="orbit-rings" viewBox={`0 0 ${size} ${size}`} preserveAspectRatio="xMidYMid meet">
          <circle cx={cx} cy={cy} r={R} className="ring" />
          <circle cx={cx} cy={cy} r={R2} className="ring" />
          {sources.map((s, i) => { const p = pos(i); return (
            <line key={s.id} x1={cx} y1={cy} x2={p.x} y2={p.y} className="orbit-link"
                  stroke={KIND[s.kind || kindOf(s.id)] || "#888"} />); })}
        </svg>
        <div className="orbit-center" style={{ left: `${(cx / size) * 100}%`, top: `${(cy / size) * 100}%`, ["--oc" as string]: KIND[center.kind || kindOf(center.label)] || "var(--accent)" }}>
          <span className="oc-core">✦</span>
          <div className="oc-label">{center.label}</div>
        </div>
        {sources.map((s, i) => {
          const p = pos(i); const c = KIND[s.kind || kindOf(s.id)] || "#888";
          return (
            <button key={s.id} className={"orbit-node" + (sel?.id === s.id ? " sel" : "")} title={s.id}
                    style={{ left: `${(p.x / size) * 100}%`, top: `${(p.y / size) * 100}%`, ["--oc" as string]: c }}
                    onClick={() => setSel((cur) => (cur?.id === s.id ? null : s))}>
              <span className="on-dot" />{s.label || s.id}
            </button>
          );
        })}
        {n === 0 && <div className="orbit-empty">no upstream sources</div>}
      </div>

      {sel && (
        <div className="orbit-detail" style={{ ["--oc" as string]: KIND[selKind] || "#888" }}>
          <div className="od-head">
            <span className="od-kind">{KIND_LABEL[selKind] || "Context source"}</span>
            <button className="od-x" onClick={() => setSel(null)} aria-label="Close">×</button>
          </div>
          <div className="od-id">{sel.label || sel.id}</div>
          <div className="od-desc">{KIND_DESC[selKind] || "A source in the model's context."}</div>
          {onOpen && <button className="od-open" onClick={() => onOpen(sel.id)}>Open full details →</button>}
        </div>
      )}
    </div>
  );
}
