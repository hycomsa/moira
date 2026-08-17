# ADR-010: Bounded rework loop — `max_loop` caps system rejects

- **Status**: Accepted (implemented)
- **Date**: 2026-08-17
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-009 (rework feedback — lands with this change by design),
  ADR-006 (durable runner), `m-c-research/` QW2 (Moira vs Cezar research)

## Context

The engine had exactly one unbounded loop. A gate configured as `auto` with
`escalate_on_blocking=False` (or a `hybrid` gate auto-denying on low
confidence) that keeps seeing a blocking finding will reject → reset the
`on_reject_goto` subtree → re-drive → reject… forever. Nothing else stops it:

- the **durable runner does not bound it** — the lease heartbeat renews every
  ~2 s for as long as the drive lives (`runner.py`), so the job never expires;
  the loop runs while the worker process runs;
- `Node.max_retries` bounds a different thing (transient *backend failures*
  within one node execution), not gate-driven rework;
- per-node `timeout` bounds one execution, not the number of executions.

ADR-009 made this loop *informed* (rejects now carry findings feedback), which
sharpened the risk: a feedback-driven loop is a **cost loop** — each iteration
burns real tokens. The comparative research (`m-c-research/`, files 10/11/15)
called the pairing out explicitly: "feedback without a cap = a cost loop, a cap
without feedback = a blind loop" — QW1 and QW2 are two halves of one change.
Reference point: Cezar bounds every automatic continuation it has (retry cap 2,
nudge cap 40, resume cap 12); Moira bounds none of its rework.

## Decision

`GateConfig` gains **`max_loop: int = 3`** — the maximum number of rejects a
gate may issue **with `by="system"`**. When a system reject *would* be issued
and the budget is spent, `evaluate_gate` returns **escalate** instead, with
`confirmed = "rework budget exhausted: N/N automatic rejects — human decision
required"`. The run pauses (`waiting_gate`, Inbox) like any human gate.

Four sub-decisions, each deliberate:

### 1. The counter is DERIVED from the audit trail, not stored

`Engine._run_gate` counts prior audit records of this gate node whose
`approvals[]` contain `decision="reject", by="system"`, and passes the count to
`evaluate_gate(cfg, results, system_rejects=n)`.

Alternatives considered:

- **In-memory counter in the drive loop** — rejected: the loop can span
  `resume()` calls, sidecar restarts and durable-runner worker handoffs
  (ADR-006 explicitly supports a run continuing on another worker); an
  in-memory counter resets exactly when supervision is weakest.
- **Extend the persisted run-state dict** — rejected: `run_state` is a flat
  `node_id → status` map consumed by the cockpit and recovery paths; changing
  its shape is a backward-compatibility break for a cosmetic gain.
- **New store column/table** — rejected: schema churn in SQLite *and* Postgres
  for a value the sealed audit already contains.
- **Derive from audit (chosen)** — every system reject is already sealed into
  the hash-chained audit by `_run_gate` itself; deriving the count makes the
  audit the single source of truth (consistent with the product thesis), is
  replay-safe, and survives every process boundary for free. Cost: one
  `audit_records(run_id)` scan per gate evaluation — O(steps of the run),
  negligible next to an LLM node, and gate evaluations are rare events.

### 2. Only SYSTEM rejects consume the budget

A human clicking "Reject & rework" in the Inbox is not a runaway loop — it is
the governance model working as intended, and it stays **unlimited**, including
after exhaustion (a human may keep sending work back for as long as they
choose; verified by test). The cap targets precisely the decisions no human
sees: `by="system"` rejects from auto/hybrid gates.

### 3. Exhaustion escalates — never approves, never fails

- **Auto-approve on exhaustion** — rejected outright: it would convert "the
  work keeps failing its checks" into a silent pass; indefensible for a
  governance product.
- **Fail the run** — rejected: failure destroys the accumulated work and the
  human's option to accept residual risk, redirect, or reject once more with
  better feedback. The persona sees the full context in the Inbox (the checks,
  the diff, the reject history in the audit) and decides.
- **Escalate (chosen)** — the same, already-tested pause path as every other
  human gate (`waiting_gate` → Inbox → RBAC-checked decision), so exhaustion
  introduces zero new run states or API surface.

### 4. Default 3, floor 0, no "unlimited" sentinel

- **3** initial+rework attempts mirror the engine's existing retry posture
  (`max_retries=2` → 3 attempts) and Cezar's field-tested small caps.
- **0** means "this gate never auto-rejects": the first would-be system reject
  escalates. Useful for high-stakes gates that want findings summarized (QW1)
  but every rework human-approved.
- **No unlimited sentinel** (-1/None) on purpose: an unbounded automatic cost
  loop is the defect this ADR removes; a team that truly wants a longer leash
  sets an explicit large number, which is visible in the pipeline YAML, the
  validator, and the audit — an auditable decision instead of an escape hatch.

## Consequences

### Positive

- The engine no longer has any unbounded automatic loop; every non-converging
  rework lands in front of a human with the reject history in the audit.
- Zero new persistence or API surface: the counter rides the audit trail, the
  pause rides the existing gate-escalation path.
- Configurable per gate in YAML (`gate: {max_loop: N}`), validated on save and
  launch (`validation.py`: non-negative integer), editable in the cockpit
  pipeline editor (auto/hybrid gates), and `GateConfig.from_dict` coerces
  hand-written string values (`"3"`) at parse time — a bad value fails loudly
  at save/launch, not mid-run.

### Negative / trade-offs

- **Behavior change for existing pipelines**: a previously-infinite loop now
  pauses after 3 automatic rejects. This is the intended fix, but operators of
  long unattended runs will see new `waiting_gate` pauses where runs formerly
  spun. (Mitigated by the exhaustion message naming the budget explicitly.)
- Old persisted gate configs (no `max_loop` key) silently adopt the default 3
  on their next deserialization — acceptable, since the alternative is keeping
  them unbounded.
- **Forward compatibility**: pipeline YAML written with `max_loop` crashes
  `GateConfig.from_dict` on pre-ADR-010 code (`cls(**d)` rejects unknown
  keys). One-directional and short-lived; noted for the future
  BACKWARD_COMPATIBILITY work (research QW12).
- The audit scan per gate evaluation grows linearly with run length; if runs
  ever reach thousands of steps, add a filtered store query then — not now.

## Alternatives Considered (whole-feature level)

- **Bound the loop via the job/lease layer** (max attempts per run-driving
  job) — rejected: the runner cannot distinguish "healthy long run" from
  "reject loop"; the gate is the only place that knows *why* work repeats, so
  the policy belongs in gate evaluation (and stays testable as a pure
  function).
- **Global run-level iteration budget** — deferred: a cost ceiling per run is
  the better global guardrail and is tracked separately as research ST4
  (server-enforced USD budget); a per-gate loop cap and a per-run cost budget
  compose rather than compete.

## References

- `orchestrator/moira_core/models.py` — `GateConfig.max_loop`
- `orchestrator/moira_core/gates.py` — `_loop_exhausted()`, `evaluate_gate(system_rejects=…)`
- `orchestrator/moira_core/engine.py` — audit-derived counter in `_run_gate`
- `orchestrator/moira_core/validation.py` — `max_loop` validation
- `cockpit/src/pages/PipelinesPage.tsx` — "Max auto-rework loops" gate field
- `orchestrator/tests/test_gate_loop_cap.py` — 7 unit + 2 integration tests
  (including the previously-infinite always-failing loop terminating at the cap)
- `m-c-research/23-rekomendacje-state-of-the-art.md` — QW2; follow-ups QW3
  (retry context), ST1 (check-output injection), ST4 (cost budget)
