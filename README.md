# Moira

[![CI](https://github.com/hycomsa/moira/actions/workflows/ci.yml/badge.svg)](https://github.com/hycomsa/moira/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

AI-native SDLC cockpit — governed orchestration layer **above** best-of-breed agent backends.

> **One repo.** This is the whole Moira product: `orchestrator/` (Python sidecar) +
> `cockpit/` (React/TS) + `src-tauri/` (desktop shell). The AI SDLC framework content
> (intents, requirements, specs, agents, skills) and any target application code live in
> **separate** repositories that Moira reads/writes as a *workspace* — they are not part of
> this repo.

Moira drives AI agents across the software development lifecycle (intent → requirements → design → code → QA → deploy) with human quality gates, git-native decision provenance, and model-agnostic execution. It does **not** re-implement an agent harness — it orchestrates pluggable frontier backends (Claude Code CLI, OpenAI Codex CLI, direct API) and adds the governance, traceability, and cockpit layer on top.

## Status: v0.1 (in development)

End-to-end on a real project (CSL Driver): shape specs via Discovery skills →
guided/visual pipeline runs → human gates → tamper-evident git-native audit →
report & traceability. **Built & verified (137 unit tests):**
- `orchestrator/` — dependency-free DAG engine + gates (auto/hybrid/human/off) +
  pluggable backends (mock/claude_code/litellm) + audit with **tamper-evident
  hash chain** + pluggable persistence (**SQLite / PostgreSQL / git mirror**) +
  HTTP API. Drives AI SDLC **skills** for discovery (single + chained). Deterministic
  **AUTO_CHECK** gates: `ac_coverage` (every acceptance criterion has a task) and
  `test_exec` (the project's test suite actually passes) — escalate on a gap.
- **Git-native task/epic backlog** (Zdzira-compatible — one markdown per ticket):
  `pm@decompose-func` turns a func-spec into an epic + tasks tagged by acceptance
  criterion; Moira measures **completeness** (Spec ↔ Tests ↔ Tasks ↔ Code) deterministically
  from the repo, alongside an optional **LLM conformance** scorecard. The same files
  open in [Zdzira PM](https://github.com/hycomsa) — one format, four tools.
- `cockpit/` — React + TS + Vite cockpit: Overview (mission control + **delivery-health
  dashboard** — per-FUNC decomposed/tested/built/conformance), Runs (+ run metrics, report,
  **traceability badge & panel**, context orbit), **decision-ready** Inbox (coverage +
  conformance on every gate card), a modern pipeline editor, Discovery, Files, Traceability
  (list + graph + provenance orbit), reusable UI primitives, profile menu. Plus a **mobile**
  gate inbox (`/m`).
- `src-tauri/` — Tauri v2 desktop shell (spawns the Python sidecar). Needs
  `cargo tauri` + webkit2gtk.

## Getting started

**New here? Read [`USER_GUIDE.md`](USER_GUIDE.md)** — how to run Moira, load/create an AI SDLC repo, create a workspace, define agents, build pipelines, and run them.

## Run the cockpit

```bash
# web cockpit (no Tauri needed) — builds frontend, serves it + API on one origin
./run-cockpit.sh                 # -> http://127.0.0.1:8765

# dev mode (hot reload): two terminals
python3 orchestrator/moira_api.py --repo ../ai-sdlc      # API on :8765
npm --prefix cockpit run dev                              # UI on :5173 (proxies /api)

# desktop shell (needs tauri-cli + webkit2gtk)
cargo tauri dev
```

## Architecture

```
Tauri Shell (Rust) + React UI   ← cockpit (web or desktop) + mobile gate inbox (/m)
        │ HTTP
Python orchestration sidecar    ← own DAG engine, gates, audit (hash-chain),
        │ delegates each node to    pluggable persistence (SQLite/Postgres/git)
Execution layer (pluggable)     ← Claude Code CLI · LiteLLM (frontier/local) · Codex CLI
```

Key decisions:
- **ADR-002** — own dependency-free DAG engine (LangGraph deferred)
- **ADR-003** — LiteLLM for model-agnostic routing (frontier-first, local as anti-lock-in)
- **ADR-004** — DEV execution is delegated, not re-implemented
- **ADR-005** — pluggable run/audit persistence (primary store + export sinks)

## Repository layout

```
orchestrator/   Python sidecar — DAG engine, gates, audit (hash chain), pluggable
                persistence (SQLite/Postgres/git), HTTP API, backends (mock/claude_code/litellm)
cockpit/        React + TypeScript + Vite frontend (+ mobile gate inbox)
src-tauri/      Tauri v2 desktop shell (spawns the sidecar)
docs/           Marketing landing pages (PL + EN)
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to run, test and build.

## Source of truth

Project context, intents, requirements, specs, ADRs, standards live in a **separate AI SDLC
repo** that you point a workspace at (e.g. `--repo /path/to/ai-sdlc`).

## Why build-own

Hycom owns the tooling: no per-seat license fees, full control, on-prem. GitLab Duo and exAI Cloud are reference designs, not vendors we pay.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). © 2026 Hycom S.A.
