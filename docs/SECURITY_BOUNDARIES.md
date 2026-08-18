# Security & governance boundaries — what Moira guarantees, and what it does not

> One honest page (2026-08-18). Moira sells governed orchestration; that claim is
> only worth something if its limits are stated as plainly as its guarantees.
> When this page and the code disagree, the code is the bug — fix one of them.

## What IS guaranteed (implemented and tested)

| Guarantee | Mechanism | Since |
|---|---|---|
| Every executed step leaves a sealed audit record (input, output, tools, decisions, approvals, cost, time, owner, lineage) | hash-chained records in the primary store; `GET /api/runs/{id}/verify` | ADR-005 |
| A silent edit/drop/reorder of an exported audit file is **detectable** | `integrity.verify_export` over the git mirror (`.moira-runs/`) | ADR-005 |
| Gate decisions are attributable to an authenticated identity when auth is on | approver = JWT principal; request-body `by` is ignored | ADR-008 |
| Separation of duties on gate personas | default-deny RBAC, 5 roles; a Developer cannot approve a compliance gate | ADR-008 |
| Automatic rework/retry loops are bounded and informed | `max_loop` cap (audit-derived counter), findings feedback, retry error context | ADR-009/010/011 |
| Accepting a failed step is explicit, never silent | `node.accept_failed` event; a distinct human **retry** decision exists | ADR-013 |
| A launch on a definitely unusable backend fails before any work or spend | install/login probes + 503 blockers with the fix command | ADR-012 |
| A run/workspace stops spending at its configured USD budget (pause, not kill; continue = raise the budget + retry, both audited) | server-enforced budgets checked before each node batch | ADR-017 |
| The model that runs is the model configured (litellm) | no silent default model; validation at save/launch | ADR-015 |
| A timed-out or cancelled CLI is actually terminated, children included | process-group SIGTERM→SIGKILL with verified exit | ADR-016 |
| Governance-pack checks marked deterministic really block; which pack applied is sealed into the audit | pack compiler + fingerprint (`applied_marker`) | ADR-007 |

## What is NOT guaranteed (read this before a regulated pilot)

| Not guaranteed | Reality today | Tracked as |
|---|---|---|
| **Signed audit** | The hash chain is tamper-**evident**, not tamper-**proof**: an attacker who can rewrite the whole chain (and the git mirror) consistently can forge history. No cryptographic signing, no key management. | future enterprise work (PERSISTENCE.md) |
| **Auth everywhere by default** | The API process default is `MOIRA_AUTH_MODE=off`. The web launcher (`run-cockpit.sh`) turns `local` auth on; the **Tauri desktop runs with auth off** (token doesn't reach the webview yet). CORS is `*`. | research ST3 |
| **Compliance verdicts** | LLM-based checks (evals, pack `llm` checks) are **qualitative, advisory evidence** — never deterministic proof of regulatory conformance. Deterministic checks block; LLM opinions inform a human. | ADR-007, by design |
| **Prompt-injection resistance** | Uncontrolled prompt content (upstream outputs, check output, attempt errors) is now framed as `[UNTRUSTED DATA]` with a SYSTEM rule (ADR-018) — a real mitigation, but **prompt-level only**: it reduces, not eliminates, injection risk. Agents still run with `acceptEdits` and no sandbox; spec/skill content is inherently instructions. | ADR-018; hard guarantees need sandboxing |
| **Execution sandboxing** | Delegated CLIs run as your OS user with your permissions — no container, no seccomp, no filesystem jail. A misbehaving agent can touch anything you can. | not planned for desktop-local |
| **Mid-node cost overshoot** | Budgets (ADR-017) are checked between node batches — a single in-flight node can exceed the limit by its own cost before the pause lands; there is no per-token metering inside a running backend call. | ADR-017, accepted |
| **Governance-pack override authority** | `override.allowed_personas` / `requires_reason` are enforced only indirectly via the gate persona; there is no dedicated, audited override endpoint. | orchestrator README |
| **Git mirror by default** | The sealed git mirror is **opt-in** (`MOIRA_GIT_EXPORT=1`, default off); without it the only evidence is the local/DB store. | PERSISTENCE.md |
| **Network exposure** | The API binds `127.0.0.1` only; the mobile inbox works on the same machine. There is no hardened multi-user deployment story yet (no TLS, no reverse-proxy guide). | research ST12 |
| **Probe freshness** | Backend readiness is cached (healthy 300 s / broken 30 s); a login can expire inside the window and surface as a run failure, not a launch block. | ADR-012, accepted |
| **OIDC in production** | The OIDC path exists and is unit-tested but has never been exercised against a live IdP. | MANUAL_VERIFICATION.md |

## Operating guidance

- **Desktop-local, single user** (today's supported mode): treat Moira as a
  trusted-local tool. The governance value is the *audit trail and the gates*,
  not perimeter security.
- **Team / regulated pilot**: do not deploy beyond loopback until ST3 (auth
  default-on end-to-end, CORS narrowed) and a reverse-proxy deployment story
  exist. Turn the git mirror on (`MOIRA_GIT_EXPORT=1`) so evidence is
  reviewable and verifiable outside the DB.
- **Contractual language**: per ADR-007's positioning, governance packs are
  *encoded standards the AI respects* and decision support — never a warranty
  of regulatory conformance. The accountable human gate owns the outcome.
