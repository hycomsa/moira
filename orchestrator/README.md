# Moira Orchestrator

Headless orchestration core — Moira's governed-orchestration IP layer. Started
as the v0.1 spike (DEC-MOIRA-001 kill-test #3: *can Moira drive one FUNC spec
end-to-end through a governed pipeline with configurable gates and a per-step
audit record?* — yes, verified end-to-end) and has since grown the v0.2 slice:
durable runner, tamper-evident audit, RBAC, governance packs, and a closed,
bounded quality loop (ADR-006…016).

## What this is (and isn't)

- **Is:** the engine (DAG pipeline, gates, retries, audit, event log, state) + persistence (SQLite/Postgres + git mirror) + durable runner + HTTP API + a CLI cockpit + pluggable backends. Stdlib-only core (`dependencies = []`).
- **Isn't:** the coding model. Per **ADR-004**, execution is *delegated* to pluggable backends. The DEV/coding node runs through `ClaudeCodeBackend` (the `claude` CLI under your own login — no per-seat API cost). Offline correctness is proven with `MockBackend` (deterministic, no API).

## Architecture

```
moira_core/
  models.py      dataclasses: Node, Pipeline, GateConfig, AgentDefinition, AuditRecord, Event …
  engine.py      the DAG engine: parallel ready nodes, gates, informed retry-then-gate,
                 bounded rework loop, pause/resume, cancellation (ADR-006/009/010/011/014)
  gates.py       auto / hybrid (confidence) / human / off + findings feedback + loop cap
                 + routing simulation (ADR-009/010)
  pipelines.py   built-in SDLC pipelines (default + client-gated)
  validation.py  structural pipeline validation (save + launch)
  store.py       SQLite primary store: workspaces, runs, events, sealed audit, jobs/leases
  pg_store.py    PostgreSQL primary store (same contract; team mode)
  persistence.py RunStore protocol + CompositeStore + export sinks (ADR-005)
  git_sink.py    git mirror of runs into <repo>/.moira-runs/ (sealed records)
  integrity.py   per-run audit hash chain: seal / verify / verify_export
  runner.py      durable runner: job claim, lease heartbeat, recovery, cancel (ADR-006)
  authn.py       identity: off / local HS256 JWT / OIDC (ADR-008)
  authz.py       default-deny RBAC, 5 roles, gate-persona authority (ADR-008)
  governance.py  governance packs: validate, compile into pipeline nodes, fingerprint (ADR-007)
  evals.py       LLM-as-judge scorecards (quality / conformance / compliance)
  tasks.py       deterministic FUNC completeness from the git-native task backlog
  repo_reader.py AI SDLC repo: FUNC/INT/REQ/ADR, skills, agents, pipelines, lineage
  report.py      Markdown run report (audit-derived)
  gitdiff.py     side-effect-free per-step diff capture
  yamlio.py      restricted YAML (keeps the core dependency-free)
  backends/
    base.py            AgentBackend protocol + registry (+ cancel fan-out)
    contract.py        shared structured-output contract + prompt sections
                       (feedback / attempt errors / failing check output)
    mock.py            deterministic, role-aware (tests + offline demo)
    claude_code.py     claude CLI delegation: role classes, streaming, verified
                       process termination (ADR-004/016)
    litellm_backend.py model-agnostic routing incl. local ollama/* (ADR-003/015)
    probes.py          install/login probes + launch blockers (ADR-012)
moira_api.py     HTTP sidecar (stdlib): ~40 endpoints, auth, launch gating, mobile inbox
moira_cli.py     headless cockpit: run / inbox / approve / reject / show / audit / runs
moira_runner.py  standalone external runner process (team mode)
tests/           33 files, 305 unit/integration tests (8 skipped without a live Postgres DSN)
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

**Proven:** governed multi-stage pipeline · configurable gates (auto/hybrid/human/off) · confidence-driven routing · human pause/resume via Inbox · reject→rework with feedback (human-written; system rejects auto-serialize the blocking findings, deterministic checks first — ADR-009) · **bounded rework loop** (`max_loop` system rejects per gate, then forced human escalation; counter derived from the audit trail — ADR-010) · **closed test-fix loop** (opt-in per gate: the rework prompt gets the raw failing-check output, audit-derived — ADR-014) · **informed retry-N-then-gate** (the retry prompt carries the previous attempts' errors, paced by linear backoff — ADR-011; after exhaustion a human gets a third decision, **↻ retry** with optional guidance, and approving the failed node is explicit, never silent — ADR-013) · per-step audit record (input/output/tools/decisions/approvals/cost/time/owner) · git-native lineage · cost aggregation · append-only event log · faithful pipeline persistence/resume · **durable runner** (jobs/leases/workers, embedded+external, lease heartbeat, mid-drive cancellation — ADR-006) · **tamper-evident audit** sealed into the primary store *and* the git mirror (hash chain + `GET /api/runs/{id}/verify` — ADR-005) · **governance packs** (repo-owned policy bundles, deterministic checks + gate — ADR-007) · **enforced default-deny RBAC** (5 roles) with JWT identity, local HS256 or OIDC (ADR-008) · **backend install/login probes** (`claude --version` + `claude auth status` behind an asymmetric-TTL cache; `/api/health` reports per-backend readiness and launches fail fast with the fix command when a backend is definitely unusable — ADR-012) · **verified process termination** (group SIGTERM→SIGKILL with grace windows, reader unblock, escalation visible in the error — ADR-016).

**Deferred:** LangGraph engine (ADR-002 — runtime uses a dependency-free DAG engine); **cryptographic signing** of the audit (the hash chain is tamper-evident but not signed); **live OIDC** against a real IdP + Tauri-webview token (web-cockpit auth works today); LiteLLM in-flight cancel. See `../docs/SECURITY_BOUNDARIES.md` for the full guaranteed-vs-not list.

## Known limitations

- `LiteLLMBackend` has no in-flight cancel (no subprocess to signal); cancellation is honored between engine batches.
- RBAC override *authority* for governance packs (`override.requires_reason` / `allowed_personas`) is enforced via the gate persona; a dedicated override endpoint is future work.
- The HTTP API binds `127.0.0.1` only; LAN/mobile access needs an explicit bind address + OIDC (future), so the mobile companion is local-machine-only today.
