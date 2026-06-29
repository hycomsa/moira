# Design notes

Product design documents for Moira itself — intents, the audit-record spec, the
operating model, the agent/gate model, and the red-team verdict that shaped the
architecture. These were authored during discovery (originally in the AI SDLC
fixture repo) and moved here so Moira's own documentation lives with Moira.

They are **design provenance**, not the current API/runtime contract — for the
implemented architecture and decisions, see the ADRs in [`../adr/`](../adr/README.md).
Where a note overlaps an ADR, the ADR wins.

| Doc | What it is |
|-----|------------|
| [intent-cockpit.md](intent-cockpit.md) | `INT-MOIRA-cockpit` — the product intent (governed AI-SDLC cockpit) + market evidence |
| [intent-zdzira.md](intent-zdzira.md) | `INT-MOIRA-zdzira-integration` — v0.2+ intent: Zdzira as the ticketing/docs/client-collaboration module |
| [func-audit-record.md](func-audit-record.md) | `FUNC-MOIRA-audit-record` — per-step audit record (the defensible core); implemented by `AuditRecord` in the orchestrator |
| [operating-model.md](operating-model.md) | The 7 pillars of running governed agents safely in production (identity, RBAC, secrets, event log, retries, evals, incident loop) |
| [agent-and-gate-model.md](agent-and-gate-model.md) | The "Configure" layer: producer/verifier agents + configurable gates (auto/human/hybrid) |
| [tech-stack.md](tech-stack.md) | Early tech-stack standards (partly historical — LangGraph plan predates the custom DAG engine of ADR-006) |
| [red-team-verdict.md](red-team-verdict.md) | `DEC-MOIRA-001` — adversarial red-team verdict and the pivots it forced |
| [v0.1-scope-and-plan.md](v0.1-scope-and-plan.md) | Historical — the original v0.1 scope (superseded by the ADRs) |
| [references/agent-ecosystem.md](references/agent-ecosystem.md) | Survey of the coding-agent ecosystem and how Moira maps/borrows from it |
| [references/exai-ux-inspiration.md](references/exai-ux-inspiration.md) | exAI UX/IA inspiration mapped to Moira |

> Reconciliation backlog (overlap with shipped docs, intentionally not merged yet):
> operating-model ↔ [ADR-008](../adr/ADR-008-api-identity-rbac.md); agent-and-gate-model ↔ the
> engine (`orchestrator/moira_core/models.py`); func-audit-record ↔ `AuditRecord` + ADR-004/005;
> red-team-verdict ↔ [`../ARCHITECTURE_MUST_FIXES.md`](../ARCHITECTURE_MUST_FIXES.md).
