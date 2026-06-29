# FUNC-MOIRA-audit-record: Per-step Audit Record

> **Source:** [INT-MOIRA-cockpit](intent-cockpit.md)
> **Requirements:** none formalized yet (the `REQ-MOIRA-AUDIT-*` set was never authored)
> **ADR:** [ADR-004](../adr/ADR-004-dev-execution-delegated.md)
> **Implemented by:** `orchestrator/moira_core/models.py` (`AuditRecord`)

## TL;DR
Every executed pipeline step produces an immutable audit record capturing
input, output, tools, decisions, approvals, cost, time and owner — plus
git-native artifact lineage. This is Moira's defensible core (decision provenance,
not model-reasoning tracking).

## Use cases

### UC-AUDIT-01 — Capture a step audit record
When an agent node finishes, the engine writes an audit record with exactly:
input · output · tools · decisions · approvals · cost · time · owner · lineage.

**AC-AUDIT-01-01:** the record is append-only (never mutated after write).
**AC-AUDIT-01-02:** `owner` is always populated (accountable actor).
**AC-AUDIT-01-03:** cost (tokens + USD) and duration are recorded.

### UC-AUDIT-02 — Capture a gate approval
When a human approves/rejects a gate, the decision records WHAT was confirmed
(not just "approved") and by whom.

**AC-AUDIT-02-01:** approval includes `confirmed` text and `by` persona.
**AC-AUDIT-02-02:** reject delivers feedback to the producer it returns to.

### UC-AUDIT-03 — Trace lineage
Each record links to the spec artifacts it derives from (FUNC → REQ → INT, ADRs).

**AC-AUDIT-03-01:** lineage lists every referenced artifact ID found in the spec.
