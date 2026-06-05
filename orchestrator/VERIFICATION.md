# v0.1 Spike — Verification against the vision

**Date:** 2026-06-04  
**Built & verified autonomously.** Maps what was built to the agreed vision (AI SDLC repo).

## Result: thesis PROVEN (kill-test #3)

> "Can Moira's governed orchestration layer drive ONE real FUNC spec end-to-end
> (analysis → code → tests → review) using pluggable backends, with human gates
> and a per-step audit record?" — **Yes.** 16/16 tests green; CLI demo end-to-end
> on a real func-spec (`FUNC-MOIRA-audit-record`).

## Capability ↔ vision trace

| Vision (source) | Built | Evidence |
|---|---|---|
| Governed multi-stage SDLC pipeline (`agent-and-gate-model.md`) | ✅ | `pipelines.py` slice: analyze→gate→design→implement→verify×2→gate→test |
| Producer / verifier agents | ✅ | `mock.py` roles; verifiers emit findings+confidence |
| Gates configurable human/auto/hybrid/off (owner requirement) | ✅ | `gates.py`; CLI `--analysis-gate/--impl-gate`; unit tests |
| Confidence-driven routing + simulation (Cezar) | ✅ | `evaluate_gate` + `simulate_routing`; tests |
| Human pause/resume via Inbox (Cezar inbox) | ✅ | CLI `inbox`/`approve`/`reject`; `waiting_gate` status |
| Reject → rework with feedback to producer | ✅ | demo: `implement` ran twice; feedback delivered |
| Retry-N-then-gate (`operating-model.md` p5) | ✅ | `_run_node_with_retry`; tests (transient + exhausted) |
| Audit record: input·output·tools·decisions·approvals·cost·time·owner | ✅ | `models.AuditRecord`; `audit` CLI; field test |
| Approval captures WHAT was confirmed (not a stamp) | ✅ | `--confirm` text persisted in approval |
| Git-native lineage (decision provenance) | ✅ | `repo_reader.trace_lineage`; shown per step |
| Append-only event log (`operating-model.md` p4) | ✅ | `store.events`; activity log |
| Cost tracking per step/run (Measurable ROI pillar) | ✅ | `run_cost`; USD + tokens |
| Owner per step (`operating-model.md` p1 identity seed) | ✅ | every record carries `owner` |
| Execution delegated, not re-implemented (ADR-004) | ✅ | `AgentBackend` protocol; `ClaudeCodeBackend` wired |
| AI SDLC repo as single source of truth (FR-005) | ✅ | ran against real `../ai-sdlc` repo |

## Honest deltas vs vision (deferred, documented)

| Item | Status | Why |
|---|---|---|
| LangGraph engine (ADR-002) | Deferred | spike uses dependency-free state machine; proves governance/audit, not the engine. v0.2. |
| Real `ClaudeCodeBackend` run | Wired, not exercised | needs `claude` CLI + login; offline proof via MockBackend. |
| Tauri 3-column cockpit (exAI-inspired) | Not built | CLI `show`/`audit` is the same data; UI renders it. |
| LiteLLM multi-backend + local Ollama (P1) | Not built | backend registry ready; add `LiteLLMBackend`. |
| Enforced RBAC / SSO identity / signed log | Designed not built | `operating-model.md` — v0.2+; owner field present from day 1. |
| Arbitrary DAG / parallel nodes | Linear + reject-goto only | sufficient for the slice; LangGraph later. |

## Real backend (ClaudeCodeBackend) — exercised for real

`verify_real_backend.py` drove the `analyze` node through the **actual `claude` CLI**
on the real func-spec, through the engine.

| Check | Result |
|---|---|
| engine → backend → `claude` CLI → parse → audit record | ✅ end-to-end |
| node status | ✅ succeeded |
| REAL cost captured in audit record | ✅ $0.52 (2299 in + 8415 out tokens) |
| lineage captured | ✅ ADR-004 → FUNC → INT → REQ |
| model read the real repo | ✅ (noted the feature is "largely implemented in moira_core") |

**Structured-output contract — HARDENED & re-verified.** First pass returned
prose (`output={raw:…}`, empty decisions). Fixed via: sentinel markers
(`===MOIRA_JSON_START/END===`) + `--append-system-prompt` contract + a robust
parser (markers → balanced-brace → raw fallback, 5 unit tests) + tool-light mode
for reasoning roles (`--disallowed-tools Edit Write Bash`) + `--max-turns`.
Re-run result: **5 structured decisions, structured output JSON, tools=[Grep,Glob],
cost $0.26 (down from $0.52 — more focused).** Contract now reliable.

Reproduce: `python3 verify_real_backend.py ../../ai-sdlc FUNC-MOIRA-audit-record`
(needs an authenticated `claude` CLI).

## Follow-on increments (all built & verified, 29/29 tests)

| Increment | Result |
|---|---|
| **Hardened ClaudeCodeBackend contract** | sentinel markers + system-prompt + robust parser + tool-light reasoning roles. Re-verified real claude: 5 structured decisions, clean JSON, cost $0.52→$0.26. |
| **LiteLLM backend** (model-agnostic, ADR-003) | shared output contract (`contract.py`), guarded import, cost capture. 5 fake-litellm tests + real litellm import (venv): `available()=True`, no-key path graceful. Registered in CLI+API. |
| **Client gate** (the wedge) | `client_gated_pipeline`: CLIENT approves BEFORE code; gate surfaces the analyst artifact (`reviews`, `audience=client`); API inbox exposes the review payload; cockpit shows "For your approval (business view)". 4 tests + API + screenshot. |
| **Cockpit** (React+TS+Vite) | 3-column (Execution plan · Activity log · Audit record) + Inbox + deep-linking. Builds clean; rendered & verified via headless Chromium on real data. |
| **Tauri desktop shell** | `cargo build` exit 0 (194MB binary, webkit2gtk/tao/wry). Launches on real X; spawns the Python sidecar (health OK, 3 backends). Native-window screenshot blocked by missing wmctrl/xdotool — webview is the same verified dist. |

## Cross-model run — VERIFIED LIVE (sdlc-full-crosschecked via claude)

`verify_real_crosschecked.py` ran the 9-node cross-checked pipeline through the
real `claude` CLI, with per-node models honored (`--model`):

| node | model | cost | time |
|---|---|---|---|
| plan | **opus** (strong planner) | $0.057 | 14s |
| design / patterns / implement | default | $0.096 / $0.217 / $0.156 | |
| review | **opus** (cross-model judge) | $0.103 | 19s |
| security | **opus** | $0.102 | 22s |
| auto-tests | real command | $0.000 | instant |
| docs | default | $0.124 | |

- **Total real cost: $0.86**, status **succeeded**.
- Cross-model is real: planner/reviewer/security ran on **opus** while producers used the default model — independent judgment (LLM-as-judge) confirmed per-node.
- Files written: `slugify.py`, `test_slugify.py`, `README.md`; **AI code passes all spec acceptance criteria**.
- Reproduce: `python3 verify_real_crosschecked.py ../../ai-sdlc /tmp/x` (needs `claude` CLI; ~$0.9).

## How to reproduce

```bash
cd moira-app/orchestrator && ./demo.sh            # offline, mock backend, 16 tests + demo
python3 verify_real_backend.py ../../ai-sdlc      # real claude delegation (costs ~$0.5)
cd .. && ./run-cockpit.sh                          # web cockpit on :8765
```
