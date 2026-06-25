# ADR-007: Governance packs as enforceable controls

**Date:** 2026-06-26
**Status:** Accepted (MVP slice)
**Deciders:** Tomasz Skonieczny

**Relates to:** ADR-005 (persistence / sealed audit), must-fix #5

## Context

Moira's pitch leans on "governance packs" (KNF/RODO/SOX). Before this ADR, governance was a
regulation corpus (`.ai/standards/compliance/*.md`) + an LLM opinion + generic confidence gates.
That gives *visibility*, not *enforcement*: Moira could ask an LLM to judge compliance, but could
not prove a configured policy was actually applied. Regulated enterprises need repeatable,
reproducible controls: which policy version applied, which checks were mandatory, which evidence
was required, which findings block release, who may override, and why.

## Decision

A **governance pack** is a versioned, executable policy bundle (JSON), **project-owned and
repo-only**: packs live solely in the AI SDLC repo at `.ai/standards/compliance/packs/*.json`.
Moira ships **no built-in packs** — the repo is the single source of truth (git-native thesis).

Principle: **deterministic checks are the control; LLM checks are a labeled qualitative second
opinion** that never blocks on its own.

A pack declares: `id`, `version`, `domain`, `jurisdiction`, `applies_when`, `required_evidence`,
`checks.deterministic[]` (built-in check or shell command + `severity_on_fail`), `checks.llm[]`
(`references` to regulation docs), `gate` (mode/persona/blocks_on), `allowed_models`, `override`
(allowed_personas/requires_reason), `retention`.

### Execution

- `validate_pack` is the schema gate (rejects unknown built-ins, bad severities/gate modes, etc.).
- `compile_pack` turns a pack into pipeline nodes the existing engine + durable runner already run:
  deterministic → `AUTO_CHECK` nodes (blocking Findings); LLM → `PRODUCER` nodes (qualitative);
  one governance `GATE`.
- `attach_pack` materialises the host pipeline's implicit order, then hangs governance off its tail
  so policy runs **after** the work.
- `POST /api/runs` accepts `governance_packs: [id,...]`; `GET /api/governance/packs[/{id}]` list/get.
- A new zero-dependency deterministic check, `log_hygiene`, flags sensitive data in log statements
  (CRITICAL) and raw `print`/`console.log` in application code (MEDIUM).
- Shell checks whose tool is absent are recorded **not applicable** (not a failure).

### Evidence (composes with ADR-005 / #4)

The applied pack's `{id, version, sha256(content)}` and per-check node ids are stamped into the
**sealed** audit at attach time, so policy coverage is reconstructed purely from the mirrored audit
(`summarize_governance`) and rendered as a coverage table (required / passed / failed / waived / N/A)
in the run report. Fully reproducible and git-native.

## Scope (MVP) and deferrals

| In | Out (deferred) |
|---|---|
| repo-only packs (3 samples: `gdpr-basic`, `wcag2.2`, `logs-advanced`) | auto-applicability by tags (manual selection only) |
| schema + validate + compile + attach | enforced override **authority** + persona authz → **ADR-006/#2 (identity/RBAC)** |
| `log_hygiene` deterministic check + N/A path | hard enforcement of data-handling / retention / `allowed_models` (recorded, not enforced) |
| applied-pack stamped in sealed audit; report coverage table | custom `blocks_on` below HIGH (engine blocks on HIGH/CRITICAL; recorded only) |
| override **recorded** (persona + reason); `requires_reason` enforced | cryptographic signing of policy result → ADR-005 future |

## Consequences

- Governance becomes a mechanism, not curated prose: a misconfigured/violated policy blocks or
  escalates a real gate, with sealed evidence.
- Override *authority* is honestly partial until identity/RBAC lands — the reason and claimed persona
  are recorded now; enforcement of *who* may override is deferred.
- Packs are project-tailorable in git; the same sealed audit that proves the run also pins the exact
  policy version that applied.
