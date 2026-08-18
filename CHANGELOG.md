# Changelog

All notable changes to Moira are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/). Architecture decisions live in
[`docs/adr/`](docs/adr/README.md) — entries below reference them.

> This file starts on 2026-08-18. History before the `[0.1.0]` baseline is
> reconstructable from `git log` and the ADR index.

## [Unreleased]

The closed, bounded quality loop + operational hardening (research-driven
batch, ADR-009…016).

### Added
- **Findings feedback on system rejects** — an auto/hybrid gate that rejects
  now serializes the blocking findings into the rework prompt (deterministic
  checks ranked above LLM findings; INFO skipped; capped digest). (ADR-009)
- **Bounded rework loop** — `GateConfig.max_loop` (default 3, `0` = never
  auto-reject) caps *system* rejects per gate; exhaustion escalates to a human,
  never auto-approves. The counter is derived from the audit trail, so it
  survives resume/restart/worker handoff. Editable in the pipeline editor.
  (ADR-010)
- **Informed backend retry** — a retry prompt carries the previous attempts'
  errors (`=== PREVIOUS ATTEMPT FAILED ===`, last 3, truncated) and retries
  are paced by a linear backoff (`MOIRA_RETRY_BACKOFF`, default 2 s). The
  audit records actual `attempts` and the errors. (ADR-011)
- **Backend install/login probes** — `claude --version` + `claude auth status`
  behind an asymmetric-TTL cache (healthy 300 s / broken 30 s), reported per
  backend in `/api/health` and a live **Settings → Backends** panel; launches
  (`/api/runs`, `/api/discovery`, `/api/eval`) fail fast with **503 +
  copy-paste fix command** when a backend is definitely unusable. Unknown
  state never blocks. (ADR-012)
- **`retry` — a third decision on an escalated failed node**
  (`POST /api/runs/{id}/retry`): re-runs the failed step with a fresh attempt
  budget; the reviewer's note becomes guidance. Inbox items (web + mobile `/m`)
  carry `kind: gate | failed_node` and show the ↻ Retry button on failed-node
  cards. Approving a failed node is now explicit (`node.accept_failed` event) —
  never a silent skip. (ADR-013)
- **Closed test-fix loop (opt-in per gate)** — `rework_check_output` delivers
  the raw failing `AUTO_CHECK` output tail (cap 20 kB) into the rework prompt
  as `=== FAILING CHECK OUTPUT ===`; evidence is derived from the audit trail
  and works across resume/restart. Checks persist `output.check_output` into
  the sealed audit. (ADR-014)
- **Security & governance boundaries page** —
  [`docs/SECURITY_BOUNDARIES.md`](docs/SECURITY_BOUNDARIES.md): what is
  guaranteed (with mechanisms) and what is explicitly not.
- **Real-CLI dogfood of the closed loop** — reproducible harness
  (`orchestrator/verify_real_testfix_loop.py`) + measured report
  ([`docs/verification/2026-08-18-closed-loop-dogfood.md`](docs/verification/2026-08-18-closed-loop-dogfood.md)):
  unattended convergence in 3 evidence-driven iterations, sealed audit chain
  verified, $1.50 / 131 s on the forced-rework scenario.

### Changed
- **LiteLLM has no silent default model** — a node without an explicit model
  fails loudly before any provider call, and `validate_pipeline` rejects the
  configuration at save/launch. Pipelines that relied on the silent
  `gpt-4o-mini` fallback must now set a model. (ADR-015, **breaking** for such
  configs)
- **Process termination is escalating and verified** — the claude CLI is
  spawned in its own process group; timeout/cancel send group SIGTERM, escalate
  to SIGKILL only if ignored, verify the exit, and unblock the stream reader if
  a detached survivor holds the pipe. The timeout error names what happened
  (`SIGTERM` vs `SIGTERM→SIGKILL`, exit verified). (ADR-016)
- Shell auto-checks stamp `check: shell` in their output (uniform deterministic
  marker across all built-in checks).
- Audit record `input` now includes `attempts`, and — when present —
  `attempt_errors` and `check_output`, so the sealed trail fully describes what
  the model was shown.

### Fixed
- An auto gate with `escalate_on_blocking=false` could loop reject→rework
  **forever** (the durable runner's lease heartbeat kept it alive); the loop is
  now bounded. (ADR-010)
- A grandchild process inheriting the CLI's stdout pipe could hang the stream
  reader forever after the child died. (ADR-016)
- Killing a timed-out CLI left its spawned children running. (ADR-016)
- The Inbox claimed "Reject & rework re-runs it" for failed steps with no
  rework edge (reject actually ended the run); the copy now tells the truth and
  ↻ Retry exists. (ADR-013)
- Duplicate gate decisions racing an already-running run, and other pre-batch
  fixes, are covered by the June history (see git log).

### Docs
- Documentation reconciled with the implemented system: real module map and
  test counts (33 files / 305 tests), 16 ADRs indexed, the phantom "Codex CLI"
  execution-layer claim marked as *planned*, stale "Deferred: LiteLLM" removed.

## [0.1.0] — 2026-06-29 (baseline)

The state of the repository when this changelog was introduced; not a formal
release. Highlights, in ADR order: Tauri desktop shell (ADR-001); dependency-free
DAG engine with parallel nodes, gates (auto/hybrid/human/off), reject→rework and
retry-then-gate (ADR-002 superseded); LiteLLM routing incl. local `ollama/*`
(ADR-003); delegated execution via the `claude` CLI with role classes and
Superpowers support (ADR-004); pluggable persistence — SQLite/Postgres primary +
sealed git mirror `.moira-runs/` with a verifiable hash chain (ADR-005); durable
runner with jobs/leases/heartbeat, embedded + external modes, mid-drive
cancellation (ADR-006); governance packs compiled into pipelines,
deterministic-first (ADR-007); JWT identity (local/OIDC) + default-deny RBAC
with 5 roles (ADR-008). React cockpit (Runs/Inbox/Pipelines/Agents/Discovery/
Traceability with the provenance orbit), discovery skill chains, deterministic
FUNC completeness from the git-native task backlog, LLM-as-judge evals, run
reports, mobile gate inbox `/m`.
