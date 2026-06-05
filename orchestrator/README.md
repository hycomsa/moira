# Moira Orchestrator (v0.1 spike)

Headless orchestration core — Moira's governed-orchestration IP layer. Proves the
v0.1 thesis (DEC-MOIRA-001 kill-test #3): **can Moira drive one FUNC spec
end-to-end through a governed pipeline with configurable gates and a per-step
audit record?** Yes — verified end-to-end.

## What this is (and isn't)

- **Is:** the engine (pipeline, gates, retries, audit, event log, state) + a CLI cockpit + pluggable backends.
- **Isn't:** the coding model. Per **ADR-004**, execution is *delegated* to pluggable backends. The DEV/coding node runs through `ClaudeCodeBackend` (the `claude` CLI under your own login — no per-seat API cost). The spike's correctness is proven with `MockBackend` (deterministic, no API).

## Architecture

```
moira_core/
  models.py      dataclasses: Node, Pipeline, GateConfig, AuditRecord, Event …
  store.py       SQLite: runs, append-only event log, audit records, cost
  engine.py      the state machine: drive nodes, gates, retry-N-then-gate, pause/resume
  gates.py       auto / hybrid (confidence) / human / off  + routing simulation
  repo_reader.py AI SDLC repo reader (FUNC/INT/REQ/ADR) + git-native lineage
  backends/
    base.py        AgentBackend protocol + registry
    mock.py        deterministic, role-aware (for tests + offline demo)
    claude_code.py claude CLI delegation (ADR-004; guarded, needs the CLI)
  pipelines.py   default SDLC vertical slice
moira_cli.py     headless cockpit: run / inbox / approve / reject / show / audit / runs
tests/           16 unittest cases (engine, gates, retries, rework, lineage)
```

## Run it

```bash
cd orchestrator

# tests
python3 -m unittest discover -s tests -v

# one-shot demo (fresh DB, happy path + human gate + reject/rework)
./demo.sh

# manual
python3 moira_cli.py run FUNC-MOIRA-audit-record --repo ../../ai-sdlc            # happy path
python3 moira_cli.py run FUNC-MOIRA-audit-record --repo ../../ai-sdlc --impl-gate human
python3 moira_cli.py inbox
python3 moira_cli.py approve <run-id> --by lead-dev --confirm "what you verified"
python3 moira_cli.py reject  <run-id> --by lead-dev --feedback "what to fix"
python3 moira_cli.py show  <run-id>     # activity log (cockpit, text form)
python3 moira_cli.py audit <run-id>     # per-step audit records (the defensible core)
```

## Gate modes (configurable per node)

| mode | behavior |
|------|----------|
| `auto` | verdict decides; HIGH/CRITICAL findings escalate |
| `hybrid` | confidence ≥ high→approve, < low→reject, between→human (Inbox) |
| `human` | named persona must approve |
| `off` | always approve |

## What's proven vs deferred

**Proven (this spike):** governed multi-stage pipeline · configurable gates (auto/hybrid/human/off) · confidence-driven routing · human pause/resume via Inbox · reject→rework with feedback · retry-N-then-gate · per-step audit record (input/output/tools/decisions/approvals/cost/time/owner) · git-native lineage · cost aggregation · append-only event log · faithful pipeline persistence/resume.

**Deferred:** LangGraph engine (ADR-002 — spike uses a dependency-free state machine); real `ClaudeCodeBackend` exercise (wired, needs CLI + login); Tauri cockpit (renders this same data); LiteLLM multi-backend; enforced RBAC / SSO identity / signed event log (operating-model.md — designed, not built).

## Known v0.1 limitations

- Engine is a linear state machine with single `on_reject_goto` rework targets (not arbitrary DAG). LangGraph in v0.2.
- `ClaudeCodeBackend` parsing is best-effort against `claude --output-format json`.
- Single-user; RBAC/identity modeled in the audit `owner` field but not enforced.
