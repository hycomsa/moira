# ADR-004: DEV execution is delegated, not re-implemented

**Date:** 2026-06-04  
**Status:** Accepted  
**Deciders:** Tomasz Skonieczny  
**Supersedes framing in:** ADR-002, ADR-003 (clarifies the execution vs orchestration split)
**Amended by:** ADR-006 — durable runner execution model

## Context

The red-team review (DEC-MOIRA-001) surfaced a load-bearing risk: the "Code Generator" node cannot be built by wiring LangGraph nodes alone. Real coding needs a frontier agent harness — and the team's existing working asset (`ai-sdlc`) already runs inside Claude Code and delegates execution to upstream `obra/superpowers`.

Owner clarification (2026-06-04): the goal is **frontier models for coding/reasoning/analysis** (Claude, OpenAI Codex), with the **ability to also use local models to avoid vendor lock-in** — NOT running coding on local Ollama. The multi-model pillar means *model-agnostic / no lock-in*, not *everything local*.

## Decision

Moira separates two layers explicitly:

```
ORCHESTRATION LAYER (Moira owns this)
  Pipeline graph · quality gates · state/persistence · retry · 
  traceability · cost tracking · cockpit UI
        │  delegates each node's work to ↓
EXECUTION LAYER (pluggable, Moira does NOT re-implement)
  Agent backends — each node picks one:
    • Claude Code CLI        (frontier, first backend — ai-sdlc already runs here)
    • OpenAI Codex CLI       (frontier, alt backend — no lock-in)
    • Direct API via LiteLLM (frontier or local, for non-coding nodes)
```

- The **coding/DEV node delegates to a frontier agent backend** (Claude Code CLI first). Moira does not build a from-scratch coding harness.
- **Reasoning/analysis/BA nodes** call models directly via LiteLLM — frontier by default, model-agnostic.
- **Local models (Ollama)** are an opt-in option for nodes where they suffice (classification, routing, summarization) and an anti-lock-in hedge — never the default for SDLC-grade coding in regulated contexts.
- A distinct **"external runtime" node type** represents delegated execution, so the cockpit shows "this step ran in Claude Code CLI" vs "this step called Claude API directly."

## Execution mechanism: self-hosted runner under your own CLI login (from Cezar)

Cezar (open-source, `cezar-feature-analysis.md`) proves the concrete mechanism that satisfies both delegation AND the owner's "no per-seat license fees" requirement:

```
Self-hosted runner picks up claude-cli / codex-cli jobs
  on YOUR infra, under YOUR existing CLI subscription login.
Managed/cloud path handles raw API jobs only.
```

- Coding runs through the developer's/org's **existing Claude Code / Codex subscription** on a self-hosted runner — not metered per-seat API billing through Moira.
- This cleanly separates **control plane** (Moira cockpit) from **execution** (runner on your infra). Orthogonal to the Tauri-vs-web cockpit decision (ADR-001) — the runner model applies either way.
- Reusable reference implementation exists in `/home/tse/hycom/sdlc/cezar` (runner protocol, token registration).

## ADR-006 clarification: runner durability

The "self-hosted runner" in this ADR is not a daemon thread launched by the HTTP request handler. For v0.2, runner execution is defined by ADR-006:

- the API/control plane creates runs and enqueues durable jobs,
- embedded local runners and external self-hosted runners use the same job/lease protocol,
- runner credentials execute authorized jobs but do not approve gates,
- long-running Claude Code / Codex CLI execution happens in the runner plane,
- crash recovery is based on persisted job leases and run state, not in-memory thread lifetime.

This preserves the core ADR-004 decision: Moira delegates actual coding/reasoning to best-of-breed harnesses. ADR-006 defines how that delegated execution becomes durable and operable.

## Consequences

- Moira's value is the **governed orchestration + traceability layer ABOVE best-of-breed harnesses** — not a competing agent harness. This is the defensible posture the red-team confirmed survives.
- The "self-hosted = local coding" implication is **retired** — it was never the owner's claim and is false for the DEV node. Self-hosted = data/control stays on-prem; the *backend* can still call a frontier API (or a self-hosted-LLM gateway, like GitLab Duo does).
- `obra/superpowers` (if used as a backend) must be **pinned + vendored with an SBOM and a documented exit path** (DORA third-party requirement).
- The custom Python DAG engine is the accepted v0.2 coordinator; LangGraph is deferred/superseded by ADR-006 unless a future ADR reintroduces it. The coordinator is not the coder; coding still belongs to delegated backends.

## Open
- Which backend is primary for v0.1 DEV node: Claude Code CLI (lowest friction, ai-sdlc compatible) — yes for v0.1.
- v0.1 instrumentation must measure unattended completion rate per backend (red-team kill-test #3).
- Which external runner packaging is first: standalone Python process, system service, or container — open for implementation.
