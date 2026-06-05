// Provenance "orbit": a glowing center node ringed by its source artifacts on
// concentric dotted rings. Used for artifact provenance, run pre-flight context,
// and post-factum run context. Pure SVG/CSS, no deps.

const KIND: Record<string, string> = {
  INT: "#a371f7", REQ: "#d29922", FUNC: "#58a6ff", ADR: "#39c5cf", DOC: "#22d3ee", RUN: "#3fb950",
};
export const kindOf = (id: string) => (id.split("-", 1)[0] || "").toUpperCase();

export interface OrbitSource { id: string; label?: string; kind?: string }

export function OrbitGraph({ center, sources, onOpen, size = 340 }: {
  center: { label: string; kind?: string };
  sources: OrbitSource[];
  onOpen?: (id: string) => void;
  size?: number;
}) {
  const cx = size / 2, cy = size / 2, R = size * 0.37, R2 = R * 0.6;
  const n = sources.length;
  const pos = (i: number) => {
    const a = (i / Math.max(1, n)) * 2 * Math.PI - Math.PI / 2;
    const r = n > 5 && i % 2 ? R2 : R;
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  };
  return (
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
          <button key={s.id} className="orbit-node" title={s.id}
                  style={{ left: `${(p.x / size) * 100}%`, top: `${(p.y / size) * 100}%`, ["--oc" as string]: c }}
                  onClick={() => onOpen?.(s.id)} disabled={!onOpen}>
            <span className="on-dot" />{s.label || s.id}
          </button>
        );
      })}
      {n === 0 && <div className="orbit-empty">no upstream sources</div>}
    </div>
  );
}
