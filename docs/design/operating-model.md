# Moira — Operating Model

**Date:** 2026-06-04  
**Status:** Active design  
**Purpose:** how Moira runs agents *safely in production* — the platform layer beneath the agent/gate model. Directly answers red-team accountability gaps (spoofable identity, missing audit trail).

These are the 7 pillars of running governed agents for real. Each: what it is, why it matters (acute in regulated/restricted environments), and v0.1 vs later.

## 1. Agent identity

- **What:** every agent run executes under a *verifiable* identity — not self-asserted `git config`. Binds: service identity (the agent) + the human owner who launched it. Every artifact/commit/audit entry carries both.
- **Why:** regulators need a provably accountable actor. "An AI did it" is not an answer; "agent X, launched by owner Y under role Z" is. Self-asserted git identity is spoofable → fails audit.
- **v0.1:** owner field captured per run + per-step (from the launching user). **Later:** SSO/OIDC-bound identity, signed commits, SPIFFE-style service identity.

## 2. RBAC

- **What:** roles gate who can configure pipelines, launch agents, approve which gates, and see what.
- **Roles:** Admin/Lead (configure + all gates), Developer (launch + dev gates + edit), Compliance Officer (compliance/legal gates), Client (read + client gates + comment), Viewer (read).
- **Why:** separation of duties is a hard control in regulated orgs (the person who builds ≠ the person who approves release). The client must see docs without touching config or code.
- **v0.1:** single-user (owner = admin), roles modeled but not enforced. **Later:** enforced RBAC, SSO group mapping, per-gate approver policy.

## 3. Secrets

- **What:** API keys (Anthropic/OpenAI), repo credentials, signing keys. Stored in OS keychain / vault — **never** in the AI SDLC repo, never in agent context, never in logs/audit records.
- **Why:** a leaked key in a git-committed config or an agent transcript is a breach. In restricted environments this is disqualifying.
- **v0.1:** `.env.local` (gitignored) + OS keychain; scrub secrets from logs. **Later:** vault integration (HashiCorp/cloud KMS), per-project scoping, rotation, self-hosted-LLM gateway so keys never reach the desktop.

## 4. Event log

- **What:** append-only, tamper-evident log of everything: agent start/stop, gate decisions, config changes, secret access, backend calls. This is the **source of the audit record** (input/output/tools/decisions/approvals/cost/time/owner).
- **Why:** the audit trail IS the product's defensible core (DEC-MOIRA-001). It must be trustworthy — immutable, ordered, complete.
- **v0.1:** append-only event log in SQLite, feeds the audit record per step. **Later:** hash-chained / signed entries (tamper-evidence), export to SIEM, retention policy.

## 5. Retries

- **What:** default error handling = retry N times (configurable) with varied approach, then escalate to a human gate. Must be **idempotent**: a retry must not duplicate side effects (double commit, double deploy).
- **Why:** agents fail (rate-limits, flaky tools, bad output). Uncontrolled retries corrupt state or cost money; no retries means every hiccup wakes a human.
- **v0.1:** retry-N-then-gate via LangGraph checkpointing (state in SQLite); side-effecting steps (commit) checkpointed so resume is safe. **Later:** per-node retry policy, rate-limit-aware backoff + provider fallback (LiteLLM), idempotency keys for deploys.

## 6. Evals

- **What:** how we *know* agents are good enough to trust. Per-agent / per-pipeline metrics: unattended completion rate, gate rejection rate, rework rate, regression suite on known specs.
- **Why:** you cannot set a gate to `mode: auto` without evidence the agent is reliable for that step. Evals are the gate on trust — and the input to the incident loop.
- **v0.1:** the spike measures unattended completion + agent-active-vs-gate-wait time over ≥10 runs (this IS the first eval). **Later:** standing eval suite per agent, regression specs, trust thresholds that govern which gates may be `auto`.

## 7. Incident loop

- **What:** when an agent does something wrong (bad code shipped, guardrail bypassed, gate errored): **detect → contain → investigate (via event log + audit record) → learn (update guardrails/standards/evals)**.
- **Why:** closes the loop — turns failures into hardened rules. This is "compound engineering": the system gets safer with use. The `ai-sdlc` framework already has `learnings/` + `/dev@capture-learnings` for this.
- **v0.1:** manual — failures captured as learnings. **Later:** structured incident record linked to the audit trail, auto-proposed guardrail/standard updates, eval regression added so the same failure can't recur silently.

## How the 7 compose

```
identity ─┐
RBAC ─────┼─→ WHO can do WHAT (authz + accountability)
secrets ──┘

event log ──→ audit record ──→ the defensible artifact (DEC-MOIRA-001)
                   ↑
retries ───────────┤ (every attempt logged)
evals ─────────────┤ (trust evidence → which gates may be auto)
                   ↓
incident loop ──→ updates guardrails/standards/evals ──→ system hardens
```

## v0.1 cut (operating model)

Minimal-but-not-painted-into-a-corner:
- **In:** owner per run, append-only event log → audit record, retry-N-then-gate, secrets in keychain/.env, the spike-as-first-eval.
- **Designed, not built:** enforced RBAC, SSO-bound identity, signed/hash-chained log, vault, standing eval suite, structured incident loop.
- **Principle:** don't build the enterprise controls in v0.1, but don't choose data models that block them later (e.g. event log is append-only from day 1; owner is always captured).
