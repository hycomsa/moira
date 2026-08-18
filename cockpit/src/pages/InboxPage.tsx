import { useEffect, useMemo, useRef, useState } from "react";
import { api, getWorkspace, approver, type Artifact, type AuditRow, type InboxItem, type RunDetail } from "../api";
import { ago } from "../time";
import { FilesDiff, hasDiff } from "../components/FilesDiff";
import { Metrics } from "../components/Metrics";
import { Button } from "../components/ui/Button";
import { ArtifactModal } from "../components/ArtifactModal";

const COLOR: Record<string, string> = {
  succeeded: "#3fb950", failed: "#f85149", waiting_gate: "#d29922",
  running: "#58a6ff", rejected: "#f85149", pending: "#8b949e",
};
const Dot = ({ s }: { s: string }) => <span className="dot" style={{ background: COLOR[s] ?? "#8b949e" }} />;

const out = (r?: AuditRow) => (r?.output ?? {}) as Record<string, unknown>;
const checkFailed = (r?: AuditRow) => out(r).passed === false || r?.status === "failed" || r?.status === "rejected";
function summarize(r?: AuditRow): string {
  if (!r) return "(no record yet)";
  const o = out(r);
  if (typeof o.passed === "boolean") return o.passed ? "check passed" : "check FAILED";
  if (typeof o.summary === "string") return o.summary;
  if (r.decisions?.length) return r.decisions[0];
  return r.status;
}

function DecisionCard({ it, codePath, onDecided, onOpenRun }: {
  it: InboxItem; codePath?: string; onDecided: () => void; onOpenRun?: (id: string) => void;
}) {
  const [det, setDet] = useState<RunDetail | null | undefined>(undefined);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const inFlight = useRef(false);
  const [artifact, setArtifact] = useState<Artifact | null>(null);

  useEffect(() => { api.run(it.run_id).then(setDet).catch(() => setDet(null)); }, [it.run_id]);
  const viewArtifact = async (id: string) => { try { setArtifact(await api.artifact(id)); } catch { /* */ } };

  const audit = det?.audit ?? [];
  const checks = (it.consumes ?? []).map((id) => audit.find((a) => a.node_id === id)).filter(Boolean) as AuditRow[];
  const failing = checks.filter(checkFailed);
  const produced = audit.filter((a) => hasDiff(a.output));
  const authored = [...new Set(produced.map((p) => (p.output as Record<string, unknown>).artifact as string).filter(Boolean))];
  const fileCount = produced.reduce((n, p) => n + ((out(p).files as unknown[])?.length || 0), 0);
  const isClient = it.audience === "client";
  // a node that FAILED after retries escalates here with no artifact — surface why
  const events = det?.events ?? [];
  const escalated = events.some((e) => e.kind === "node.escalate");
  const failEvents = events.filter((e) => e.kind === "node.escalate" || e.kind === "retry");

  const metrics = useMemo(() => {
    if (!det) return null;
    const dur = audit.reduce((a, x) => a + (x.duration || 0), 0);
    const c: Record<string, number> = {};
    const NOISE = new Set(["", "(default)", "mock", "claude_code", "litellm"]);
    audit.forEach((a) => { const i = a.input as { model?: string; backend?: string }; const be = i?.backend; if (!be) return; const l = i?.model && !NOISE.has(i.model) ? i.model : be; if (l) c[l] = (c[l] || 0) + 1; });
    const model = Object.entries(c).sort((x, y) => y[1] - x[1])[0]?.[0];
    return { usd: det.cost.usd, tokens: det.cost.tokens_in + det.cost.tokens_out, duration: dur, model };
  }, [det, audit]);

  const decide = async (kind: "approve" | "reject" | "retry") => {
    if (inFlight.current) return;   // synchronous guard: a gate can only be decided once
    inFlight.current = true;
    setBusy(true); setMsg("");
    try {
      if (kind === "approve") await api.approve(it.run_id, approver(), note || "Reviewed and accepted");
      else if (kind === "retry") await api.retry(it.run_id, approver(), note);
      else await api.reject(it.run_id, approver(), note || "Sent back for rework");
      setMsg(kind === "approve" ? "✓ Approved — continuing the run…"
        : kind === "retry" ? "↻ Retrying the failed step…"
        : "↩ Sent back for rework — re-running the step…");
      onDecided();
    } catch (e) {
      setMsg("⚠ Action failed: " + String((e as Error)?.message || e));
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  };

  return (
    <div className={"panel glass decision-card" + (isClient ? " client" : "")}>
      <div className="dc-head">
        <span className={"aud-tag " + (isClient ? "client" : "tech")}>{isClient ? "client gate" : "quality gate"}</span>
        {it.persona && <span className="persona-tag">{it.persona}</span>}
        {!!it.since && (
          <span className={"inbox-age" + (Date.now() / 1000 - it.since > 7 * 86400 ? " stale" : "")}
                title={new Date(it.since * 1000).toLocaleString()}>
            ⏳ waiting {ago(it.since)}
          </span>
        )}
        {it.gate_review?.coverage && (() => {
          const c = it.gate_review!.coverage!;
          const ok = c.ac.total > 0 && c.ac.in_tasks >= c.ac.total;
          return <span className={"trace-badge " + (ok ? "ok" : "warn")} title={`tasks ${c.tasks.done}/${c.tasks.total} done · AC covered by tasks`}>
            {ok ? "✓" : "⚠"} AC {c.ac.in_tasks}/{c.ac.total}</span>;
        })()}
        {it.gate_review?.conformance && (
          <span className={"trace-badge " + (it.gate_review.conformance.overall >= 0.8 ? "ok" : it.gate_review.conformance.overall >= 0.5 ? "warn" : "legacy")}
                title="last LLM conformance (spec ↔ code)">⚖ {Math.round(it.gate_review.conformance.overall * 100)}%</span>
        )}
        <div className="dc-titles">
          <div className="dc-title">{it.message || "Decision required"}</div>
          <div className="dc-sub">{det?.pipeline.name || it.run_id.replace("run-", "")} · <code>{it.run_id.replace("run-", "").slice(0, 10)}</code></div>
        </div>
        {metrics && <Metrics m={metrics} compact />}
        {onOpenRun && <button className="link dc-open" onClick={() => onOpenRun(it.run_id)} title="open the full run (execution plan + activity)">Open run →</button>}
      </div>

      {/* verdict banner */}
      {det !== undefined && (
        failing.length > 0
          ? <div className="dc-banner warn">⚠ {failing.length} check{failing.length > 1 ? "s" : ""} failing — review before approving</div>
          : checks.length > 0
            ? <div className="dc-banner ok">✓ all {checks.length} checks green</div>
            : null
      )}

      {/* client/business artifact */}
      {it.review && Object.keys(it.review).length > 0 && (
        <div className="review">
          <div className="review-label">{isClient ? "For your approval (business view)" : "Review artifact"}</div>
          {Object.entries(it.review).map(([node, art]) => (
            <div className="artifact" key={node}>
              {typeof art?.summary === "string" && <div className="art-summary">{art.summary}</div>}
              {Array.isArray(art?.requirements) && (
                <ul className="art-reqs">{(art.requirements as unknown[]).map((r, i) => <li key={i}>{String(r)}</li>)}</ul>
              )}
            </div>
          ))}
        </div>
      )}

      {/* checks feeding this gate */}
      {checks.length > 0 && (
        <div className="gate-checks">
          <div className="review-label">Checks feeding this gate</div>
          {checks.map((c) => (
            <div className={"check-row" + (checkFailed(c) ? " fail" : "")} key={c.node_id}>
              <Dot s={checkFailed(c) ? "failed" : c.status} /><span className="cname">{c.node_name}</span>
              <span className="csum">{summarize(c)}</span>
            </div>
          ))}
        </div>
      )}

      {/* artifacts authored by a discovery skill — preview right here */}
      {authored.length > 0 && (
        <div className="gate-authored">
          <div className="review-label">Authored in the repo — review before approving</div>
          {authored.map((id) => (
            <button key={id} className="chip chip-btn" onClick={() => viewArtifact(id)}>📄 {id}</button>
          ))}
        </div>
      )}

      {/* proposed changes — collapsed */}
      {produced.length > 0 && (
        <details className="dc-changes">
          <summary>Proposed changes · {fileCount} file{fileCount !== 1 ? "s" : ""}</summary>
          {produced.map((p) => (
            <div className="audit" key={p.step_id}>
              <div className="arow"><b>{p.node_name}</b></div>
              <FilesDiff output={p.output} open codePath={codePath} />
            </div>
          ))}
        </details>
      )}
      {/* failed-node escalation: no artifact to review — show why it stopped */}
      {escalated && failEvents.length > 0 && (
        <div className="gate-checks dc-escal">
          <div className="review-label">⚠ Escalated after a failed step — what happened</div>
          {failEvents.map((e, i) => (
            <div className={"check-row" + (e.kind === "node.escalate" ? " fail" : "")} key={i}>
              <span className="kind">{e.kind}</span><span className="csum">{e.message}</span>
            </div>
          ))}
          <div className="muted small" style={{ marginTop: 6 }}>
            The step failed (no output produced). <b>↻ Retry</b> re-runs it — your note becomes
            guidance for the next attempt. <b>Approve</b> accepts the failure and continues
            <i> without</i> this step's output. <b>Reject</b> follows the pipeline's rework
            edge (or ends the run if there is none). Open the run for the full execution plan.
          </div>
        </div>
      )}
      {det === undefined && <div className="muted small">loading evidence…</div>}

      {/* decide */}
      <div className="review-label" style={{ marginTop: 14 }}>Decision note <span className="muted">(recorded in the audit)</span></div>
      <textarea className="gate-note" placeholder={isClient ? "What you approve / what to change…" : "What did you verify? / feedback for rework…"}
                value={note} onChange={(e) => setNote(e.target.value)} />
      {msg && <div className={"dc-banner " + (msg.startsWith("⚠") ? "warn" : "ok")} style={{ marginTop: 10 }}>{msg}</div>}
      {it.kind === "budget" && (
        <div className="muted small" style={{ marginTop: 8 }}>
          💸 <b>Budget pause</b> — the run stopped before spending more. Raise the budget
          (Overview → Spend), then <b>↻ Retry</b> to continue; <b>Approve</b> skips this
          step; <b>Reject</b> stops or reworks per the pipeline (ADR-017).
        </div>
      )}
      <div className="dc-actions">
        {(it.kind === "failed_node" || it.kind === "budget") && (
          <Button variant="primary" disabled={busy} onClick={() => decide("retry")}>{busy ? "…" : "↻ Retry step"}</Button>
        )}
        <Button variant="success" disabled={busy} onClick={() => decide("approve")}>{busy ? "…" : "✓ Approve"}</Button>
        <Button variant="danger" disabled={busy} onClick={() => decide("reject")}>{busy ? "…" : "↩ Reject & rework"}</Button>
        <Button variant="ghost" disabled={busy} title="Terminally cancel this run (audited) — the decision disappears from the Inbox"
          onClick={async () => {
            if (!window.confirm("Cancel this run? Queued work in it is discarded (audited).")) return;
            setBusy(true);
            try { await api.cancel(it.run_id, "cancelled from the Inbox"); setMsg("✕ Run cancelled."); onDecided(); }
            catch (e) { setMsg("⚠ Cancel failed: " + String((e as Error)?.message || e)); }
            finally { setBusy(false); }
          }}>✕ Cancel run</Button>
      </div>
      {artifact && <ArtifactModal artifact={artifact} onClose={() => setArtifact(null)} onOpen={viewArtifact} />}
    </div>
  );
}

const STALE_DAYS = 7;

export function InboxPage({ inbox, onDecided, onOpenRun }: { inbox: InboxItem[]; onDecided: () => void; onOpenRun?: (id: string) => void }) {
  const [codePath, setCodePath] = useState<string | undefined>();
  const [bulkBusy, setBulkBusy] = useState(false);
  useEffect(() => {
    api.workspaces().then((d) => setCodePath(d.workspaces.find((w) => w.id === getWorkspace())?.code_path || undefined)).catch(() => { /* */ });
  }, []);

  // oldest decisions first — they are the ones rotting
  const sorted = [...inbox].sort((a, b) => (a.since || 0) - (b.since || 0));
  const staleCut = Date.now() / 1000 - STALE_DAYS * 86400;
  const stale = sorted.filter((i) => (i.since || 0) < staleCut);

  const cancelStale = async () => {
    if (!window.confirm(
      `Cancel ${stale.length} run(s) waiting longer than ${STALE_DAYS} days?\n` +
      "Each is cancelled terminally (audited); queued work in them is discarded.")) return;
    setBulkBusy(true);
    for (const it of stale) {
      try { await api.cancel(it.run_id, `stale gate decision (> ${STALE_DAYS}d) — bulk cancel from Inbox`); }
      catch { /* keep going — one failure must not stop the sweep */ }
    }
    setBulkBusy(false);
    onDecided();
  };

  return (
    <div className="page">
      <div className="inbox-head">
        <h2 style={{ margin: 0 }}>Pending decisions</h2>
        {inbox.length > 0 && <span className="pill-count">{inbox.length}</span>}
        <span className="muted" style={{ fontSize: 13 }}>human quality gates awaiting your call · oldest first</span>
        {stale.length > 0 && (
          <Button variant="ghost" size="sm" disabled={bulkBusy} onClick={cancelStale}
            title={`Terminally cancel every run whose decision has been waiting > ${STALE_DAYS} days`}>
            {bulkBusy ? "cancelling…" : `✕ Cancel stale (${stale.length} > ${STALE_DAYS}d)`}
          </Button>
        )}
      </div>
      {inbox.length === 0 && (
        <div className="panel glass inbox-empty">
          <div className="ie-ic">🛡</div>
          <div><b>You're all caught up.</b><br /><span className="muted">No gates waiting — agents are running autonomously.</span></div>
        </div>
      )}
      {sorted.map((it) => <DecisionCard key={it.run_id} it={it} codePath={codePath} onDecided={onDecided} onOpenRun={onOpenRun} />)}
    </div>
  );
}
