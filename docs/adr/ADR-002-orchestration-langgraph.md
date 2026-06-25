# ADR-002: Orchestration Engine — LangGraph + Python

**Date:** 2026-06-04  
**Status:** Superseded for v0.2 runtime by ADR-006  
**Deciders:** Tomasz Skonieczny

**Superseded by:** ADR-006 — Durable runner execution model

## Context
Moira needs a stateful, interruptible pipeline orchestration engine that supports human-in-the-loop quality gates natively.

## Decision
Original decision: **Python + LangGraph** as the orchestration engine, running as a Tauri sidecar process.

This is no longer the implemented v0.1/v0.2 runtime contract. The shipped Moira orchestrator uses a dependency-free Python DAG engine that implements:

- explicit DAG dependencies via `depends_on`,
- parallel ready-node execution,
- gate pause/resume,
- reject-to-rework,
- retry-then-escalate,
- audit/event persistence through the `RunStore` seam.

LangGraph remains a possible future implementation detail if it clearly reduces complexity, but it is not the runtime engine today and must not be described as an active product guarantee.

The v0.2 architecture keeps the custom engine as the in-process run driver and moves durability to the execution layer: persisted jobs, leases, worker heartbeat, and embedded/external runners. That decision is captured in ADR-006.

## Rationale
- Human-in-the-loop via checkpoints/interrupts is a **core LangGraph feature** — quality gates map directly to this primitive
- Stateful graph execution with persistence = pause/resume/retry at any node
- Python is the native language of the AI/ML ecosystem (LiteLLM, Langfuse, OTel all Python-native)
- LangGraph is more mature than LangGraph.js for complex conditional workflows
- Tauri + Python sidecar is a proven pattern for desktop AI apps

## Architecture
```
Original target:

Tauri (Rust) ←IPC/SSE→ Python sidecar (LangGraph + LiteLLM)
React UI                      ↕
                         Agent backends
                    (Claude / OpenAI / Ollama)
```

Current v0.2 target is defined by ADR-006:

```
Cockpit / API control plane
        │
        ▼
Durable job queue + run state (SQLite local / Postgres team)
        │
        ▼
Runner host (embedded local or external worker)
        │
        ▼
Custom Python DAG engine -> pluggable agent backends
```

## Alternatives Considered
- **TypeScript + LangGraph.js:** Single language stack but LangGraph.js less mature, worse AI ecosystem
- **Google ADK:** Interesting A2A support but Gemini-first, GCP-oriented; human-in-the-loop less native
- **Custom orchestration:** Maximum control, maximum build cost — not worth it given LangGraph maturity

## Consequences

- Documentation must distinguish **pipeline orchestration semantics** from the **execution durability model**.
- The current custom engine is accepted as the v0.2 run driver; replacing it with LangGraph would require a new ADR and a migration plan.
- Durable execution is solved below the engine through jobs and runners, not by relying on request-thread daemon execution.
- The `langgraph` package remains optional only; no runtime code may assume it is installed unless a future ADR changes this decision.
