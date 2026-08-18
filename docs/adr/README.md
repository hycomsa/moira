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
| [008](ADR-008-api-identity-rbac.md) | API identity (JWT, local/oidc) + default-deny RBAC (5 roles) | Accepted (backend MVP) |
| [009](ADR-009-gate-rework-feedback.md) | System-generated rework feedback on gate reject (closed quality loop, part 1) | Accepted (implemented) |
| [010](ADR-010-bounded-rework-loop.md) | Bounded rework loop — `max_loop` caps system rejects, audit-derived counter (closed quality loop, part 2) | Accepted (implemented) |
| [011](ADR-011-informed-retry.md) | Informed backend retry — previous errors in the retry prompt + linear backoff (closed quality loop, part 3) | Accepted (implemented) |
| [012](ADR-012-backend-probes.md) | Backend install/login probes + fail-fast launch gating (asymmetric-TTL cache, unknown never blocks) | Accepted (implemented) |
| [013](ADR-013-failed-node-retry.md) | "Retry" as the third decision on an escalated failed node; approve-the-gap becomes explicit | Accepted (implemented) |
| [014](ADR-014-check-output-to-rework.md) | Closed test-fix loop — failing check output feeds the rework prompt (opt-in per gate, audit-derived) | Accepted (implemented) |
| [015](ADR-015-fail-loud-model-identity.md) | Fail-loud model identity — litellm has no silent default model; validation at save/launch | Accepted (implemented) |
| [016](ADR-016-process-termination.md) | Escalating, verified process termination — group SIGTERM→SIGKILL, reader unblock, visible verdict | Accepted (implemented) |
| [017](ADR-017-cost-budgets.md) | Server-enforced cost budgets (run + workspace-month) — settings store, pause-not-kill, governed continue | Accepted (implemented) |
| [018](ADR-018-prompt-and-metering-hygiene.md) | Prompt & metering hygiene — [UNTRUSTED DATA] framing, fail-loud skills + references delivery, cache-aware tokens | Accepted (implemented) |
| [019](ADR-019-auth-on-by-default.md) | Auth on by default, end-to-end — process default `local`, Tauri token handshake, CORS narrowing, one-command onboarding | **Proposed (TODO)** |
| [020](ADR-020-worktree-per-run.md) | Run isolation via git worktrees — `code_path` only, fail-closed, branch as deliverable; unblocks multi-run and ×N variants | **Proposed (design settled)** |

> `ADR-007-agentic-engineering-workflow` was left in the `ai-sdlc` framework repo —
> it describes the engineering workflow, not the Moira product, so its ownership is
> intentionally triaged separately.
