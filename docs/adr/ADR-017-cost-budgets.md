# ADR-017: Server-enforced cost budgets (per run, per workspace-month)

- **Status**: Accepted (implemented)
- **Date**: 2026-08-18
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-010 (iteration cap — bounds *loops*, this bounds *money*),
  ADR-013 (the `retry` decision this reuses), ADR-005 (audit costs as the
  spend source), `m-c-research/` ST4

## Context

Budgets existed only in the cockpit's localStorage — alert-only, per browser,
invisible to the engine. Nothing server-side could stop a run from spending;
loops were bounded by iteration count (ADR-010) but not by dollars. The
dogfood (docs/verification/2026-08-18) made it concrete: a 3-iteration loop on
a trivial task cost $1.50 — a real FUNC multiplies that. The research called
this a *shared* gap (Cezar lacks it too): whoever ships it first has a
differentiator, and it naturally belongs to a governance product.

## Decision

### 1. Budgets are governed configuration in a new generic `settings` store

A `settings(scope, key, value)` table (SQLite + Postgres, `CREATE IF NOT
EXISTS` — **no migration**) holds `workspace:<id>/budget_month_usd`,
`workspace:<id>/budget_run_usd` (default per-run cap applied at launch) and
`run:<run_id>/budget_usd`. A run's budget is a persisted setting — not a
context field — so it survives resume/restart independent of how the API
rebuilds context. Absent = no limit; empty string = cleared; `0` = pause
before any spend. The table is deliberately generic (future config lands here
instead of new columns).

### 2. The engine checks BEFORE spending, and pauses — never kills

`_budget_exceeded()` runs before each node batch in `_drive`: run spend =
`store.run_cost` (sealed audit costs — the same numbers the product reports);
workspace-month spend = sum of the workspace's runs' costs in the current
calendar month. When exceeded, the next pending step is parked exactly like an
escalation (`waiting_gate` + `budget.wait` event naming scope, spent and
limit) — queued work is preserved, downstream never runs on a silent gap, and
the pause lands in the Inbox (web + mobile) with a distinct `kind: budget`.

### 3. Continuing is an explicit, audited act — no override state

There is no "force" flag. The card says it plainly: **raise the budget**
(POST `/api/workspaces/{id}/budget`, RBAC action `configure`), then use the
existing ADR-013 **retry** decision — the check simply passes once the limit
is higher. Approve = skip the step (the explicit `node.accept_failed` gap
event, message generalized to "step accepted WITHOUT output"); reject =
stop/rework per pipeline. Both the budget change (API log) and the decision
(audit approvals) are attributable.

### 4. Surfaces

`GET /api/spend` returns the server budgets next to `month_usd`; the cockpit
Overview budget input now reads/writes the **server** value (the localStorage
copy is gone — one source of truth). Launch accepts `budget_usd`, defaulting
to the workspace's `budget_run_usd`.

## Consequences

- Positive: a runaway loop (or any run) is now bounded in dollars by
  configuration that an RBAC-protected action controls; SECURITY_BOUNDARIES
  moves "cost limits" from *not guaranteed* to *guaranteed*.
- Negative / limits: spend is counted at node-batch boundaries — a single
  in-flight node can still overshoot the limit by its own cost (bounding
  mid-node spend would require backend-level metering; out of scope).
  Workspace-month math is O(runs) per check — fine at current scale, add an
  aggregate query if workspaces grow to thousands of runs. Monthly boundary is
  the server's local calendar month.
- The engine's budget pause reuses the waiting-node machinery, so a
  budget-paused step is indistinguishable from a failed one in `run_state`
  (the `budget.wait` event is the discriminator — used by the Inbox `kind`).

## Alternatives Considered

- **Columns on `workspaces`/`runs`** — rejected: schema migration in two
  dialects for two numbers; the KV table is one `CREATE IF NOT EXISTS` and
  reusable.
- **Budget in the run context** — rejected: context is rebuilt by the API on
  resume; the budget would silently vanish exactly when a long run resumes.
- **Hard-fail the run on budget** — rejected: destroys queued work and the
  human's options; pausing keeps all three decisions open.
- **Auto-extend grace ("one more node")** — rejected: a limit that stretches
  itself is not a limit.
- **Client-side enforcement** — that was the status quo; a browser cannot be
  the enforcement point for a server-driven engine.

## References

- `orchestrator/moira_core/store.py`, `pg_store.py`, `persistence.py` — settings
- `orchestrator/moira_core/engine.py` — `_budget_exceeded`, `_pause_on_budget`
- `orchestrator/moira_api.py` — budget endpoint, launch default, spend rollup,
  inbox `kind: budget`
- `cockpit/src/pages/Overview.tsx`, `InboxPage.tsx`, `api.ts`, `mobile.html`
- `orchestrator/tests/test_cost_budget.py` — 7 tests (settings roundtrip, pause
  arithmetic, governed continue, cross-run monthly cap, HTTP + RBAC)
- `m-c-research/23-rekomendacje-state-of-the-art.md` — ST4
