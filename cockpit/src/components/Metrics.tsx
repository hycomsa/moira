// Compact, consistent presentation of run rollup metrics:
// leading model · duration · cost · tokens. Shared by Overview, Runs, Traceability.

export const fmtTokens = (n?: number) =>
  n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;

export const fmtDur = (s?: number) => {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60), ss = Math.round(s % 60);
  if (s < 3600) return `${m}m ${ss.toString().padStart(2, "0")}s`;
  const h = Math.floor(s / 3600);
  return `${h}h ${Math.floor((s % 3600) / 60)}m`;
};

export const fmtUsd = (n?: number) => (n == null ? "—" : `$${n}`);

export interface Metricsish { usd?: number; tokens?: number; duration?: number; model?: string }

export function Metrics({ m, compact = false }: { m: Metricsish; compact?: boolean }) {
  return (
    <span className={"metrics" + (compact ? " compact" : "")}>
      {m.model && m.model !== "—" && <span className="m-model" title="leading model">◆ {m.model}</span>}
      <span className="m-item" title="total step time">⏱ {fmtDur(m.duration)}</span>
      <span className="m-item" title="cost">{fmtUsd(m.usd)}</span>
      <span className="m-item" title="tokens">⛁ {fmtTokens(m.tokens)}</span>
    </span>
  );
}
