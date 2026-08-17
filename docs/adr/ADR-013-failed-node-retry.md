# ADR-013: "Retry" as the third decision on an escalated failed node

- **Status**: Accepted (implemented)
- **Date**: 2026-08-17
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-011 (informed retry — the automatic attempts that precede
  this escalation), ADR-010 (loop cap), ADR-008 (RBAC), `m-c-research/` QW5

## Context

When a node exhausts its backend retries the run escalates to a human
(`retry-N-then-gate`). But the decision surface offered only approve/reject —
both with wrong semantics for a **failed** node:

- **Approve** marked the node DONE with **no output**: downstream nodes ran on
  an empty upstream, silently. On the mobile Inbox especially, a reviewer
  tapping "Approve" had no way to know they were approving a void.
- **Reject** follows `on_reject_goto` — which producers rarely have — so in
  practice it **ended the run** as REJECTED, destroying the queued work.
- The cockpit even claimed "Reject & rework re-runs it", which was false for
  any node without a rework edge — documentation drift the research warns
  about (architecture truth is part of the product).

The natural human intent at this checkpoint — *"it looks transient / I know
what was wrong, run it again (and here's a hint)"* — had no button.

## Decision

`GateDecision.decision` gains a third value, **`retry`**, valid only when the
run is waiting on an escalated **non-gate** node:

1. **Semantics**: the failed node resets to `pending` and re-drives with a
   **fresh attempt budget** (`max_retries` again, with ADR-011's informed
   retry + backoff inside). An optional human note rides the existing
   `feedback` channel, so the next attempt sees it as
   `=== REVIEWER FEEDBACK ===` — human guidance, distinct from the mechanical
   `attempt_errors` of ADR-011.
2. **Gates are excluded** (engine raises `ValueError`, API returns 409): a
   gate re-**evaluates** verifier results, which are what they are; "retry"
   re-**executes** work. Blurring that would make a gate decision mean two
   different things.
3. **Audited like any decision**: the retry lands in the audit record's
   `approvals` (`decision="retry", by=<principal>`) plus a `node.retry.human`
   event. Because ADR-010's loop cap counts only `by="system"` rejects, human
   retries are — correctly — unlimited.
4. **Approve stays allowed but becomes explicit**: a human may legitimately
   accept the gap (the node may be non-essential), so approve still marks the
   failed node DONE — but the engine now emits `node.accept_failed`
   ("accepted WITHOUT output — downstream proceeds without its result"), so
   the acceptance of a void is visible in the event trail, never silent.
   Approving a real gate emits nothing new.
5. **Transport**: `POST /api/runs/{id}/retry` `{feedback?}` — same idempotency
   guard as approve/reject (409 unless `waiting_gate`), same RBAC action
   (`approve_gate`: it is the third decision at the same checkpoint), approver
   identity from the authenticated principal. `/api/inbox` and
   `/api/mobile/inbox` items now carry `kind: gate | failed_node`, derived
   from the waiting event (`gate.wait` vs `node.escalate`), so both cockpit
   and mobile show the ↻ Retry button exactly where it applies.

## Consequences

### Positive

- The checkpoint's three decisions now match the three human intents:
  *run it again* (retry), *accept the gap* (approve, explicit), *stop or
  rework per pipeline* (reject) — and the UI copy now tells the truth.
- Queued work survives transient failures without abusing approve/reject.
- Full audit: who retried, with what guidance, and every acceptance of a
  missing output.

### Negative / trade-offs

- A human can retry indefinitely — intentional (mirrors ADR-010's rule that
  human decisions are governance working as intended), but a stuck node can
  be retried forever by hand; the cost budget (research ST4) is the eventual
  global guardrail.
- `kind` inference relies on the waiting event kind; a run paused by both
  (impossible today — one waiting node at a time) would prefer the latest
  event. Revisit if parallel waiting states ever exist.

## Alternatives Considered

- **Forbid approve on a failed node** — rejected: removes legitimate human
  agency (non-essential steps, accepted residual gaps); explicitness beats
  prohibition.
- **Reuse reject for "run it again"** — rejected: reject's contract is the
  rework edge / terminal rejection; overloading it would make `on_reject_goto`
  semantics depend on why the run stopped.
- **Auto-retry with backoff at the runner level instead** — rejected: the
  engine already did its automatic attempts (ADR-011); this checkpoint exists
  precisely because automation ran out — the next attempt should be a human
  decision, on the audit record.
- **A separate RBAC action for retry** — rejected: it is the same checkpoint
  decided by the same persona; a new action would fragment the role matrix
  for no boundary gain.

## References

- `orchestrator/moira_core/engine.py` — `resume()` retry branch +
  `node.accept_failed` event
- `orchestrator/moira_api.py` — `/api/runs/{id}/retry`, inbox `kind`
- `orchestrator/moira_core/authz.py` — route mapping
- `cockpit/src/pages/InboxPage.tsx`, `cockpit/src/api.ts`,
  `orchestrator/mobile.html` — the ↻ Retry button on failed-node cards
- `orchestrator/tests/test_failed_node_retry.py` — 8 tests (engine semantics,
  feedback delivery, gate exclusion, explicit accept-failed, HTTP + RBAC)
- `m-c-research/23-rekomendacje-state-of-the-art.md` — QW5
