# Moira Architecture Must-Fixes

This note expands the five architecture must-fixes from the repository review.
It is intentionally high-level: the goal is to align the solution with Moira's
product thesis before adding more surface area.

> **Status (2026-06-26):** all five must-fixes are implemented to MVP — see the
> per-section "Status update" notes and ADR-002/004/005/006/007/008. Remaining
> work is tracked as follow-ups (see `MANUAL_VERIFICATION.md` and the ADRs).
> **Path note:** `path:line` citations below are relative to the **workspace root**
> (the directory containing `moira-app/` and `ai-sdlc/`), and predate later edits —
> treat them as historical pointers, not exact line numbers.

## Context

Moira's stated intent is to be an AI-native SDLC cockpit: a governed
orchestration layer above pluggable agent backends, with human gates,
traceability, and audit across the lifecycle from intent to delivery.

Evidence:

- `moira-app/README.md:10` calls Moira an "AI-native SDLC cockpit" and a
  "governed orchestration layer".
- `moira-app/README.md:13-16` says Moira drives agents across the lifecycle
  and adds governance, traceability, and cockpit behavior above backends.
- `moira-app/README.md:46-48` says the AI SDLC framework content and target
  application code live in separate repositories that Moira reads and writes
  as a workspace.
- `LEAN_CANVAS.md:12-16` frames the enterprise problem as missing guardrails,
  compliance, audit trail, visibility, and control over AI agents.
- `LEAN_CANVAS.md:175-178` positions domain governance and self-hosted/on-prem
  deployment as the unfair advantage.

Architectural judgment: the current implementation is a credible v0.1 spike,
but not yet an enterprise governance architecture. The fixes below are the
minimum architectural changes needed before treating Moira as a regulated
pilot platform rather than a trusted-local prototype.

## 1. Define The Real v0.2 Architecture Contract

### Current State

There is a drift between accepted architecture records, product docs, and code.

Facts:

- ADR-002 accepts Python plus LangGraph as the orchestration engine:
  `ai-sdlc/.ai/context/adrs/ADR-002-orchestration-langgraph.md:10-11`.
- The implemented engine says it is a dependency-free DAG engine and
  "supersedes LangGraph for now":
  `moira-app/orchestrator/moira_core/engine.py:1-17`.
- The README now describes an "own DAG engine" as a key decision:
  `moira-app/README.md:80-82`.
- `PERSISTENCE.md` says hash-chaining is future work:
  `moira-app/orchestrator/PERSISTENCE.md:112-113`.
- SQLite and Postgres stores already seal audit records:
  `moira-app/orchestrator/moira_core/store.py:169-178` and
  `moira-app/orchestrator/moira_core/pg_store.py:146-152`.

Judgment: for a governance product, architecture truth is part of the product.
If the ADRs, README, and code disagree, reviewers cannot know which guarantees
are real and which are aspirational.

### Why It Matters

Moira's value proposition depends on trust. Enterprise users will ask simple
questions:

- What is the orchestration engine?
- Where is run state durable?
- What happens after restart?
- Which audit trail is canonical?
- Which parts are proven, deferred, or experimental?

If these answers differ across docs and code, the design looks uncontrolled.
That is especially damaging because Moira is supposed to control AI delivery.

### Target Architecture

Create a single architecture contract for v0.2 that separates three modes:

1. `desktop-local`
   - Single user.
   - Local SQLite allowed.
   - Localhost API.
   - Best for demos and individual development.

2. `team-self-hosted`
   - Shared Postgres primary store.
   - Authenticated HTTP API.
   - Durable runner queue.
   - RBAC-backed gate decisions.
   - Git mirror optional but not the primary read source.

3. `enterprise-regulated`
   - Same as team mode, plus signed audit/report artifacts, retention policy,
     approved model routing, policy packs, and deployment/security hardening.

ADR-006 now decides that the custom engine is the v0.2 run driver and that
durability belongs to the runner/job layer:

- The custom engine stays for v0.2.
- ADR-002 is superseded for runtime purposes.
- If LangGraph returns later, it needs a new ADR explaining which missing
  properties it provides: durable checkpoints, interrupts, replay, graph
  branching, or observability.

### Architecture Changes

- Keep ADR-002 marked as superseded by ADR-006 for the implemented runtime.
- Update ADR-005/PERSISTENCE.md to reflect actual hash-chain implementation.
- Add a `docs/architecture/runtime-modes.md` or equivalent document that defines
  local, team, and enterprise modes.
- Add an "Architecture Guarantees" table:
  - durable state
  - gate authorization
  - audit tamper-evidence
  - execution isolation
  - model routing
  - recovery semantics
  - deployment scope
- Add a "Not Guaranteed Yet" table for anything still prototype-grade.

### Acceptance Criteria

- A new engineer can read one architecture entry point and correctly describe
  the real runtime.
- Every accepted ADR is either implemented, explicitly deferred, or superseded.
- Product docs do not claim enterprise guarantees that are only available in
  `desktop-local` or not available at all.
- The release notes for the next pilot state which runtime mode is supported.

### Effort

Small.

This is mostly documentation and decision cleanup, but it must be done with
architectural discipline. Avoid "docs-only" language that hides unresolved
decisions.

### Risk Retired

Retires the risk that teams build features against the wrong mental model,
especially around orchestration durability, audit evidence, and deployment
boundaries.

## 2. Harden Identity And API Boundaries

### Current State

The sidecar is treated like a trusted local API, but it already exposes actions
that matter.

Facts:

- The API binds to `127.0.0.1`:
  `moira-app/orchestrator/moira_api.py:1139`.
- The API sends wildcard CORS:
  `moira-app/orchestrator/moira_api.py:498-500`.
- The API can read workspace files:
  `moira-app/orchestrator/moira_api.py:797-804`.
- The API can clone Git repositories:
  `moira-app/orchestrator/moira_api.py:851-865`.
- The API can create and delete agents and pipelines:
  `moira-app/orchestrator/moira_api.py:529-543`,
  `moira-app/orchestrator/moira_api.py:874-884`.
- The API can start runs:
  `moira-app/orchestrator/moira_api.py:885-908`.
- The API can approve or reject gates based on request body fields:
  `moira-app/orchestrator/moira_api.py:1044-1074`.
- The user guide advertises mobile access on the desktop IP:
  `moira-app/USER_GUIDE.md:177-180`, which implies a network-accessible mode.
- RBAC and identity are explicitly not enforced:
  `moira-app/orchestrator/README.md:65-71`.

Judgment: the trust boundary is currently suitable only for trusted-local
development. It is not suitable for team use, mobile use, or regulated pilots.

### Why It Matters

Approving a gate is a business or engineering control. If any local web page can
POST an approval because CORS is open and there is no authentication, the gate is
not a control. It is a UI event.

The same applies to file browsing, Git clone, pipeline edits, and run execution.
These are sensitive operations because they can expose project data or trigger
agent processes that modify code and documents.

### Target Architecture

Introduce an explicit API security model with two profiles.

#### Local Desktop Profile

- Bind to `127.0.0.1`.
- Generate a random session token on sidecar start.
- Inject token into the Tauri/web frontend launch context.
- Require `Authorization: Bearer <token>` or an equivalent local session header
  for all `/api/*` routes except health checks that expose no sensitive data.
- Restrict CORS to the known frontend origin.
- Do not allow LAN/mobile access unless explicitly enabled.

#### Team Profile

- Bind to configured interface only when explicitly enabled.
- Require real authentication, for example SSO/OIDC or a reverse-proxy identity
  header with a signed internal trust contract.
- Map authenticated users to personas and roles.
- Enforce authorization:
  - who can create workspaces
  - who can read code files
  - who can edit pipelines
  - who can start runs
  - who can approve each gate persona
  - who can run compliance checks
- Store gate approver from authenticated identity, not from request body.

### Architecture Changes

- Add an `AuthContext` created at the HTTP boundary.
- Replace free-form `by` values on gate approval with authenticated principal.
- Add route-level authorization policy.
- Split health endpoint into:
  - public readiness: no secrets, no paths
  - authenticated diagnostics: repo paths, logs, config
- Make mobile inbox require the same auth model.
- Add explicit configuration:
  - `MOIRA_BIND_ADDR`
  - `MOIRA_AUTH_MODE=local-token|oidc|trusted-proxy`
  - `MOIRA_ALLOWED_ORIGINS`
  - `MOIRA_MOBILE_ENABLED`
- Add audit fields for:
  - authenticated subject
  - display name
  - persona at decision time
  - auth source

### Acceptance Criteria

- A browser origin not on the allowlist cannot call mutating APIs.
- Gate approval fails unless the authenticated user is authorized for the gate
  persona.
- Request body cannot spoof the approver.
- Mobile access is impossible unless explicitly enabled and authenticated.
- File browsing and log access are authenticated and scoped.
- Audit records identify who approved a gate in a way that is enforceable, not
  merely user-entered text.

### Effort

Medium.

The first local-token version is small. The team/enterprise identity integration
is medium because it touches every mutating route and the gate model.

### Risk Retired

Retires the risk that Moira's "human quality gate" is bypassable or forgeable.
Also retires a major data-exposure risk from file browsing and diagnostics.

## 3. Replace Daemon Threads With Durable Runner Execution

> **Status update (2026-06-26): largely DONE in the v0.2 slice.** Durable jobs/leases,
> embedded + external runners, lease heartbeat during execution (closes the long-run
> double-execution gap), lost-lease detection, orphan recovery via lease release, and an
> external-mode-requires-Postgres guard are all implemented and tested. **Mid-drive
> cancellation + ClaudeCodeBackend subprocess kill are now also done (B1).** ADRs moved to
> `moira-app/docs/adr/`. **Deferred:** durable `eval`/`report` jobs, live Postgres
> multi-runner soak, LiteLLM in-flight cancel — see ADR-006 "Cancellation". The path references below
> point at the original `ai-sdlc/.ai/context/adrs/...` location; ADRs 000–006 now live in
> `moira-app/docs/adr/`.

### Current State

Runs are launched in background daemon threads inside the HTTP sidecar.

Facts:

- `background()` starts a daemon thread:
  `moira-app/orchestrator/moira_api.py:74-95`.
- Startup recovery marks any `running` run as failed:
  `moira-app/orchestrator/moira_api.py:97-114`.
- The engine can persist run state and resume from it:
  `moira-app/orchestrator/moira_core/engine.py:72-82`,
  `moira-app/orchestrator/moira_core/engine.py:84-124`.
- ADR-004 describes a self-hosted runner model where the control plane is
  separate from execution:
  `ai-sdlc/.ai/context/adrs/ADR-004-dev-execution-delegated.md:35-47`.
- ADR-006 now defines the durable runner execution model:
  `ai-sdlc/.ai/context/adrs/ADR-006-durable-runner-execution.md`.

Judgment: the engine has useful resume primitives, but the process model is not
durable. Long-running coding or discovery work is tied to a web server thread.

### Why It Matters

Real SDLC agent work is slow and failure-prone:

- a coding node can run for tens of minutes
- Claude/Codex CLI can hang or rate-limit
- the desktop app can close
- the sidecar can restart during update
- multiple users may start work concurrently
- a team may need to cancel, retry, or inspect a stuck run

Daemon threads provide none of the operational control expected for this.
Marking active runs as failed after restart is honest, but not enough for a
product that claims governed execution.

### Target Architecture

Introduce durable jobs and runners, as formalized in ADR-006.

There should be one execution model and two hosting modes:

- **Embedded local runner**: runs inside the desktop/local sidecar, but still
  claims persisted jobs and heartbeats leases.
- **External runner**: runs as a separate process/service against the shared
  primary store.

Do not keep the current daemon-thread path as a parallel product runtime. It is
acceptable only as an implementation detail of the embedded runner loop after it
uses jobs, leases, and recovery.

#### Control Plane

The HTTP API should only create run/job records and return quickly. It should not
own long-running execution.

Responsibilities:

- create run
- enqueue next executable node or run-driving job
- expose run status
- expose logs/events
- accept gate decisions
- authorize user actions

#### Runner Plane

Runners should lease jobs from the primary store.

Responsibilities:

- claim job with lease timeout
- execute node or continue run
- heartbeat while active
- write live events and audit records
- release, retry, or fail job
- support cancellation
- recover abandoned leases

The runner can still be a local process in desktop mode, but it should use the
same durable protocol as team mode.

### Architecture Changes

Add persistence tables or equivalent records:

- `jobs`
  - `job_id`
  - `run_id`
  - `workspace_id`
  - `node_id`
  - `kind`
  - `status`
  - `payload`
  - `attempt`
  - `max_attempts`
  - `lease_owner`
  - `lease_until`
  - `created_at`
  - `updated_at`
  - `started_at`
  - `finished_at`
  - `last_error`
- `workers`
  - `worker_id`
  - `mode` (`embedded` or `external`)
  - `host`
  - `pid`
  - `version`
  - `capabilities`
  - `status`
  - `active_job_id`
  - `heartbeat_at`
  - `last_error`
- `cancellations`
  - `run_id`
  - `requested_by`
  - `reason`
  - `requested_at`
  - `honored_at`

Add runner behavior:

- lease with compare-and-set semantics
- heartbeat extension
- abandoned lease recovery
- max attempts per job
- idempotent node completion
- one active executor per run/node
- explicit cancellation path

Refactor the API:

- `POST /api/runs` creates a run and enqueues the first job.
- Gate approval enqueues continuation work.
- The sidecar may start an embedded local runner in desktop mode, but execution
  no longer depends on request thread lifetime.
- Product routes should not call `Engine.drive_existing()` or `Engine.resume()`
  directly; direct calls remain acceptable in tests and explicit synchronous
  CLI/debug commands.

### Acceptance Criteria

- Killing the HTTP sidecar does not lose the authoritative run state.
- A runner crash leaves a lease that can expire and be retried.
- A desktop restart can resume or mark only the active leased job, not blindly
  fail the entire run.
- Two runners cannot execute the same node simultaneously.
- Users can cancel a run and see that cancellation in audit/events.
- Backpressure is visible: queued, leased, running, waiting, failed.

### Effort

Large.

This changes the runtime architecture. The existing engine can remain, but the
ownership of execution must move from HTTP request process to a durable runner
protocol.

### Risk Retired

Retires the risk that real agent work is lost or corrupted by sidecar restart,
desktop close, double execution, or stuck subprocesses.

## 4. Unify Audit Evidence

> **Status update (2026-06-26): DONE.** `save_audit` now returns the sealed body
> (option 2); `CompositeStore` forwards it to sinks; `GitExportSink` writes the
> sealed record, so every `.moira-runs/<run>/audit/*.json` carries `prev_hash` +
> `hash`. Added `integrity.verify_export` (reconstructs chain order from links) so
> the git mirror verifies standalone and agrees with the primary's head; a silent
> edit/drop/reorder of a git audit file is detected. The `/api/runs/{id}/verify`
> endpoint and the report's chain block (status/length/head) already existed.
> Docs (`PERSISTENCE.md`) now state tamper-evident-only; signing is future
> enterprise work. Tests: git-sink sealed-mirror + tamper, verify_export units,
> live Postgres re-verified. Path/line refs below predate the change.

### Current State

The primary stores seal audit records, but the Git mirror writes unsealed records
from the original object.

Facts:

- SQLite seals records before storing:
  `moira-app/orchestrator/moira_core/store.py:169-178`.
- Postgres seals records before storing:
  `moira-app/orchestrator/moira_core/pg_store.py:146-152`.
- `CompositeStore.save_audit()` writes to primary, then fans out the original
  `AuditRecord` to sinks:
  `moira-app/orchestrator/moira_core/persistence.py:148-150`.
- `GitExportSink.on_audit()` writes `rec.to_dict()`:
  `moira-app/orchestrator/moira_core/git_sink.py:126-136`.
- `integrity.py` says the git mirror carries the evidence:
  `moira-app/orchestrator/moira_core/integrity.py:6-7`.
- Reports are rendered from the primary audit:
  `moira-app/orchestrator/moira_core/persistence.py:176-180`.

Judgment: there are two audit artifacts with different evidence strength. The
primary database audit is sealed; the Git audit mirror is human-readable but not
the sealed canonical record.

### Why It Matters

The product promise says "git-native, tamper-evident audit." If auditors inspect
the Git repository and find records without `prev_hash` and `hash`, the claim
does not hold for the artifact they are reviewing.

Even if the database is correct, the reviewable evidence in the repo is weaker.
That is a product-level problem, not a storage implementation detail.

### Target Architecture

Define one canonical audit record shape.

Every exported audit record should include:

- immutable run id
- step id
- node id
- input summary
- output summary or artifact reference
- tools used
- decisions
- approvals
- cost
- timing
- owner/authenticated subject
- lineage
- `prev_hash`
- `hash`
- schema version

The primary store and Git mirror should carry the same sealed record content.
Reports should include the chain head and verification result.

For enterprise mode, add signing:

- sign terminal run report or chain head
- record signer identity and key id
- document key rotation and verification

### Architecture Changes

Change the persistence contract so sinks receive the sealed record, not the raw
`AuditRecord`.

Possible approaches:

1. Move sealing into `CompositeStore.save_audit()`.
   - Composite seals once.
   - Primary stores sealed record.
   - Sinks receive sealed record.
   - Requires adjusting store interfaces.

2. Add `save_audit()` return value.
   - Primary store seals and returns sealed body.
   - Composite forwards sealed body to sinks.
   - Keeps sealing in primary but makes exported evidence consistent.

3. Add a read-after-write export.
   - Composite saves primary.
   - Composite fetches the just-saved sealed record.
   - Composite passes sealed record to sinks.
   - Simpler but less clean.

Preferred: option 1 if doing a larger persistence cleanup; option 2 if minimizing
blast radius.

Also update:

- Git sink to write sealed records.
- Integrity tests to verify the Git-exported audit files, not only primary store.
- Report rendering to show chain status and chain head.
- PERSISTENCE.md to remove stale "future work" text.

### Acceptance Criteria

- Every `.moira-runs/<run>/audit/*.json` file includes `prev_hash` and `hash`.
- `/api/runs/{id}/verify` and Git-export verification agree.
- A manual edit to a Git audit record is detected by verification.
- Generated run report includes chain status, length, and head hash.
- Docs clearly state whether hash-chain is tamper-evident only or also signed.

### Effort

Medium.

The code change is moderate, but the evidence model must be explicit. Avoid
patching only the Git sink without deciding where canonical sealing belongs.

### Risk Retired

Retires the risk that the reviewable audit artifact does not satisfy the
tamper-evident audit promise.

## 5. Turn Governance Packs Into Enforceable Controls

### Current State

Moira has strong language around governance packs and compliance, but the
implementation is mostly document-driven plus LLM evaluation.

Facts:

- The lean canvas lists governance packs such as KNF, RODO, and SOX:
  `LEAN_CANVAS.md:75-80`.
- The repo reader supports compliance documents under
  `.ai/standards/compliance`:
  `moira-app/orchestrator/moira_core/repo_reader.py:140-184`.
- The API can run a compliance eval against selected regulation references:
  `moira-app/orchestrator/moira_api.py:927-992`.
- Deterministic checks currently include AC coverage and test execution:
  `moira-app/orchestrator/moira_core/engine.py:260-266`.
- AC coverage and test execution produce findings that feed gates:
  `moira-app/orchestrator/moira_core/engine.py:300-323`,
  `moira-app/orchestrator/moira_core/engine.py:325-351`.
- Gate behavior is based on verifier findings and confidence:
  `moira-app/orchestrator/moira_core/gates.py:20-55`.

Judgment: the current model is good for MVP visibility, but not yet a
policy-grade governance system. It can ask an LLM to judge compliance; it cannot
yet prove that a configured governance policy was enforced.

### Why It Matters

Regulated enterprises need repeatable controls:

- which regulation or policy version applied
- which checks were mandatory
- which evidence was collected
- which findings block release
- who can override a finding
- why an override is allowed
- how the result can be reproduced later

LLM compliance review can be useful, but as a second opinion. It should not be
the sole enforcement mechanism for high-risk controls.

### Target Architecture

Define governance packs as versioned policy bundles, not only Markdown guidance.

A pack should have:

- `id`
- `version`
- `domain`
- `jurisdiction`
- `applicability triggers`
- `required evidence`
- `mandatory checks`
- `optional checks`
- `severity mapping`
- `gate policy`
- `allowed models/backends`
- `data handling constraints`
- `required approver personas`
- `override policy`
- `retention policy`

Example conceptual shape:

```yaml
id: rodo-basic
version: 0.1.0
applies_when:
  tags: [personal-data]
required_evidence:
  - data-flow-summary
  - retention-decision
  - dpa-check
checks:
  deterministic:
    - id: secrets-scan
      command: gitleaks detect --no-git
      severity_on_fail: critical
    - id: test-exec
      built_in: test_exec
      severity_on_fail: high
  llm:
    - id: gdpr-review
      references: [REG-GDPR]
      model_policy: approved-frontier
gate:
  mode: human
  persona: compliance
  blocks_on: [high, critical]
override:
  allowed_personas: [compliance-lead]
  requires_reason: true
```

### Architecture Changes

- Add a governance-pack schema and validator.
- Add pack selection to workspace or pipeline configuration.
- Compile pack policy into pipeline nodes:
  - deterministic checks
  - LLM evaluations
  - required human gates
  - blocked release conditions
- Store applied pack id/version in audit records and reports.
- Add a policy result object distinct from generic LLM scorecards.
- Make gate decisions consume policy results, not only generic verifier findings.
- Add override flow:
  - authorized approver
  - explicit reason
  - linked finding
  - audit record
- Add pack test fixtures for at least one sample compliance pack.

### Acceptance Criteria

- A pipeline run records which governance pack version was applied.
- Required checks from the pack are executed or explicitly marked not applicable.
- A high/critical mandatory finding blocks or escalates according to pack policy.
- Overrides are permissioned, reasoned, and auditable.
- LLM compliance review is labeled as qualitative evidence, not deterministic
  proof.
- Reports show policy coverage: required, passed, failed, waived, not applicable.

### Effort

Medium to large.

The first schema and compiler can be medium. A mature policy engine with rich
applicability logic and enterprise approvals is large.

### Risk Retired

Retires the risk that "governance pack" means curated prompts rather than
enforceable, repeatable controls.

## Recommended Sequencing

1. Fix the architecture contract first.
   - It defines what mode Moira is building toward and prevents more drift.

2. Harden API and identity before any team/mobile pilot.
   - Without this, gates are not real controls.

3. Add durable runner execution before real coding pilots.
   - Without this, long-running agent work is operationally fragile.

4. Unify audit evidence before claiming Git-native tamper-evidence.
   - This is central to the product thesis and relatively contained.

5. Build governance packs into policy controls before regulated pilots.
   - This turns the enterprise compliance story from intent into mechanism.

## Summary Verdict

Moira should continue as a refactor, not a rethink. The core idea is sound:
orchestrate best-of-breed agent backends and add governance, gates, traceability,
and audit. The current implementation proves the core loop, but the next phase
must move from trusted-local orchestration to enforceable, durable, authenticated
governance.
