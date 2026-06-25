# Architecture Decision Records — Moira

Canonical home for Moira's product architecture decisions. These were previously
authored under an `ai-sdlc` working copy whose git index still tracked an unrelated
project's ADRs; they now live with the code they govern (ADR-005/006 P5 hygiene fix).

| ADR | Decision | Status |
|-----|----------|--------|
| [000](ADR-000-template.md) | ADR template | — |
| [001](ADR-001-desktop-shell-tauri.md) | Desktop shell on Tauri | Accepted |
| [002](ADR-002-orchestration-langgraph.md) | Orchestration engine | Superseded for v0.2 runtime by ADR-006 (custom DAG engine; LangGraph deferred) |
| [003](ADR-003-model-routing-litellm.md) | Model routing via LiteLLM | Accepted |
| [004](ADR-004-dev-execution-delegated.md) | Dev execution is delegated to agent backends | Accepted (amended by ADR-006) |
| [005](ADR-005-pluggable-persistence.md) | Pluggable persistence (SQLite/Postgres + git sink) | Accepted (amended by ADR-006) |
| [006](ADR-006-durable-runner-execution.md) | Durable runner execution model (one model, two hosting modes) | Accepted |
| [007](ADR-007-governance-packs.md) | Governance packs as enforceable controls (repo-only, deterministic-first) | Accepted (MVP) |

> `ADR-007-agentic-engineering-workflow` was left in the `ai-sdlc` framework repo —
> it describes the engineering workflow, not the Moira product, so its ownership is
> intentionally triaged separately.
