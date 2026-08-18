/** Shared time formatting (run timestamps come from the server as epoch seconds). */

/** Compact relative age: 42s / 7m / 3h / 2d. */
export const ago = (ts: number) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return `${s | 0}s`;
  if (s < 3600) return `${(s / 60) | 0}m`;
  if (s < 86400) return `${(s / 3600) | 0}h`;
  return `${(s / 86400) | 0}d`;
};

/** Full local date-time, e.g. "18 Aug 2026, 12:43" (browser locale). */
export const fmtWhen = (ts?: number) => {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
};

/** Compact duration between two epoch-second stamps: 12s / 3m 05s / 1h 12m. */
export const fmtSpan = (from?: number, to?: number) => {
  if (!from || !to || to < from) return "";
  const s = Math.round(to - from);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${(s / 60) | 0}m ${String(s % 60).padStart(2, "0")}s`;
  return `${(s / 3600) | 0}h ${String(((s % 3600) / 60) | 0).padStart(2, "0")}m`;
};

export const TERMINAL_STATUSES = new Set(["succeeded", "failed", "rejected", "cancelled"]);
