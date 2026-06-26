# ADR-006: Durable runner execution model

**Date:** 2026-06-25  
**Status:** Accepted  
**Deciders:** Tomasz Skonieczny

**Supersedes runtime framing in:** ADR-002  
**Amends:** ADR-004, ADR-005

## Context

Moira's v0.1 sidecar launches long-running work from HTTP handlers by creating a run and then starting a daemon background thread. That was sufficient for a local spike, but it is not a durable execution model:

- a sidecar restart kills active daemon threads,
- active `running` runs are currently recovered by marking them failed,
- long-running Claude/Codex execution can outlive the HTTP request and the desktop app session,
- team/self-hosted use needs multiple runners, backpressure, cancellation, and crash recovery,
- gate approval must enqueue a continuation safely instead of directly resuming work in the request path.

The engine itself already has useful primitives: persisted run state, `drive_existing`, gate waiting state, and `resume`. The missing layer is a durable execution protocol around the engine.

We also want to keep a low-friction desktop experience. Therefore the product needs two hosting modes, but not two execution semantics.

## Decision

Moira will use **one durable runner execution model** with two deployment modes:

1. **Embedded local runner**
   - Runs inside the desktop/local sidecar process.
   - Uses the same persisted jobs and lease protocol as every other runner.
   - Optimized for single-user desktop, demos, and development.
   - May use SQLite as the primary store.

2. **External runner**
   - Runs as a separate process or service.
   - Claims jobs from the shared primary store.
   - Intended for team/self-hosted/enterprise deployments.
   - Uses PostgreSQL as the primary store for robust multi-runner coordination.

The HTTP API is the **control plane**. It creates runs, writes/reads state, enqueues jobs, accepts gate decisions, and exposes status. It does not directly own long-running execution.

The runner is the **execution plane**. It leases jobs, heartbeats while executing, drives the existing engine, records events/audit/state, handles cancellation, and releases or retries work.

## Non-Decision

This ADR does not replace the custom Python DAG engine with LangGraph. The current custom engine remains the run driver for v0.2. LangGraph can be reconsidered later only if it clearly reduces complexity and comes with a migration ADR.

This ADR also does not create two product runtimes. "Daemon/local" is not a separate execution path. It is only an embedded way to host the same durable runner protocol.

## Target Architecture

```
Cockpit / mobile / CLI
        │
        ▼
HTTP API / control plane
  - auth and authorization
  - create run
  - enqueue job
  - approve / reject / cancel
  - read status, events, audit
        │
        ▼
Primary store
  - workspaces
  - runs
  - run state
  - events
  - audit
  - jobs
  - workers
  - cancellations
        │
        ▼
Runner host
  embedded local runner OR external runner process
        │
        ▼
Engine.drive_existing / Engine.resume
        │
        ▼
Agent backends
  - mock
  - Claude Code CLI
  - LiteLLM
  - future Codex CLI
```

## Job Lifecycle

Jobs move through explicit states:

```
queued -> leased -> running -> succeeded
                         ├-> failed
                         ├-> cancelled
                         └-> waiting_gate
```

Definitions:

- `queued`: job exists and is available for a runner.
- `leased`: a runner has atomically claimed the job for a bounded time.
- `running`: the runner has started executing the job.
- `waiting_gate`: the run reached a human gate and needs a decision.
- `succeeded`: the job completed and no immediate continuation is needed.
- `failed`: the job exhausted attempts or hit a non-recoverable error.
- `cancelled`: user/system cancellation was honored.

The runner must update the lease heartbeat while executing (implemented: a heartbeat thread renews the lease every `lease_seconds / 3` for the duration of the drive, so a long run is never stealable). If the runner dies, the heartbeat stops, the lease expires, and a later runner can recover or retry the job according to policy.

## Job Kinds

Initial job kinds:

- `drive_run`: continue a newly created or previously queued run from persisted state.
- `resume_run`: continue a run after a gate decision.
- `rerun`: create or drive a fresh run from a previous pipeline snapshot.
- `eval`: reserved. **In v0.2, `eval` is intentionally NOT a durable job** — see "Eval is a synchronous control-plane exception" below.
- `report`: render/export final reports if this becomes expensive or needs retries.

The first implementation may start with `drive_run` and `resume_run`; other kinds can be added once the contract is proven.

## Persistence Model

ADR-005 remains the primary persistence seam, but the `RunStore` contract must be extended with durable execution tables.

### `jobs`

Required fields:

- `job_id`
- `run_id`
- `workspace_id`
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

### `workers`

Required fields:

- `worker_id`
- `mode` (`embedded` or `external`)
- `host`
- `pid`
- `version`
- `capabilities`
- `status` (`running`, `draining`, `stopped`)
- `active_job_id`
- `heartbeat_at`
- `last_error`

### `cancellations`

Required fields:

- `run_id`
- `requested_by`
- `reason`
- `requested_at`
- `honored_at`

## Store Semantics

The durable runner requires atomic operations, not only CRUD.

Required store operations:

- `enqueue_job(job)`
- `claim_next_job(worker_id, capabilities, lease_seconds)`
- `heartbeat_job(job_id, worker_id, lease_seconds)`
- `mark_job_running(job_id, worker_id)`
- `complete_job(job_id, worker_id, status, error=None)`
- `release_expired_leases(now)`
- `request_cancellation(run_id, by, reason)`
- `cancellation_requested(run_id)`
- `upsert_worker(worker)`
- `heartbeat_worker(worker_id, active_job_id=None)`

SQLite implementation can use a single local writer and transactions. PostgreSQL implementation should use row-level locking or atomic update patterns so multiple external runners cannot claim the same job.

## API Semantics

`POST /api/runs`:

1. validates and snapshots the pipeline,
2. creates the run and initial run state,
3. enqueues a `drive_run` job,
4. returns `run_id` immediately.

Gate approve/reject:

1. validates the authenticated approver,
2. persists the gate decision intent,
3. enqueues a `resume_run` job,
4. returns immediately.

Cancel:

1. records a cancellation request,
2. the runner honors it at the **job boundary** — before it starts executing a claimed `drive_run`/`resume_run` job (see "Cancellation" for the v0.2 scope and what is deferred),
3. run and job become `cancelled` with audit/event evidence.

The API must not call `Engine.drive_existing()` or `Engine.resume()` directly in product runtime. Direct calls remain acceptable in unit tests, explicit synchronous CLI/debug commands, and the deliberately-synchronous `eval` path documented below.

### Eval is a synchronous control-plane exception (v0.2)

`POST /api/eval` deliberately runs the eval pipeline synchronously on the request thread and returns the scorecard inline. This is **not** a silent second execution path — it is a documented, scoped exception:

- the caller (cockpit) needs the scorecard in the HTTP response, not via polling; routing eval through the async runner would regress that contract,
- eval pipelines are short and bounded (a single eval/judge node), so head-of-line blocking on the request thread is acceptable,
- eval does not mutate a code repo or a long-lived run that must survive restart, so it does not need lease/heartbeat durability.

If eval ever grows long-running or repo-mutating, it must become a durable `eval` job with polling (see Open Questions). Until then, the synchronous path is the intended design and is exempt from the "API must not drive the engine" rule.

## Runner Semantics

A runner loop:

1. registers or heartbeats worker status,
2. claims the next eligible job,
3. marks the job running,
4. opens its own store connection,
5. reconstructs the run context from persisted workspace/run/pipeline state,
6. calls the appropriate engine method,
7. writes audit/events/state through normal engine/store paths,
8. completes the job,
9. enqueues a continuation only if the run remains runnable and not waiting at a gate.

The runner must be idempotent at the job boundary. If a job is retried after a crash, persisted run state decides what work remains. It must not assume in-memory context survived.

## Embedded Local Runner

The embedded runner is allowed for desktop/local mode, but it must still use jobs and leases.

It may run as:

- a thread inside the sidecar process for v0.2 implementation speed, or
- a child process if process isolation becomes necessary.

Operational hygiene for embedded mode:

- worker id includes host and pid,
- heartbeat is visible in `/api/health` or a runner status endpoint,
- stale worker/lease cleanup runs on startup,
- local mode may use SQLite, but only one embedded runner should be active per profile.

This keeps desktop friction low while preventing a second non-durable execution path.

## External Runner

External runner mode is required for team/self-hosted deployments.

It must:

- connect to the same primary store as the API,
- advertise capabilities such as `claude_code`, `litellm`, `codex_cli`, `shell_checks`,
- claim only jobs it can execute,
- heartbeat regularly,
- support draining shutdown,
- expose logs/status for operations,
- avoid trusting local desktop session state.

PostgreSQL is the expected primary store for this mode. SQLite is not a team runner coordination backend.

## Recovery

Startup and periodic recovery must not blindly mark all `running` runs as failed.

New recovery rules:

- expired job leases return to `queued` if attempts remain,
- jobs with exhausted attempts become `failed`,
- runs with no active job and no waiting gate are reconciled by persisted run state,
- abandoned workers become `stopped` or `stale`,
- cancellation requests are honored before new node execution.

The current v0.1 orphan recovery behavior is acceptable only until ADR-006 is implemented.

## Cancellation

Cancellation is a first-class control-plane action.

**v2 (implemented) — job-boundary cancellation:**

- the cancellation request is persisted (`cancellations` table),
- the runner checks it once **before it begins executing a claimed job** (`drive_run`/`resume_run`); a not-yet-started run cancels cleanly, and a run waiting at a gate cancels instead of resuming,
- the runner marks run + job `cancelled` and honors the request, with event + audit evidence.

**Mid-drive cancellation + subprocess kill (implemented, B1):**

A run inside a long `Engine.drive_existing` call is now interrupted:

1. `Engine._drive` polls a `should_cancel()` callback between node batches (and after each batch persists), returning `CANCELLED` instead of scheduling more work;
2. the runner's heartbeat thread also watches `cancellation_requested(run_id)` (ticking ≤2s) and calls `BackendRegistry.cancel_active()`, which invokes each backend's optional `cancel()` — `ClaudeCodeBackend.cancel()` kills its in-flight subprocess(es), unblocking the long node so the engine stops promptly.

`LiteLLMBackend` has no subprocess to kill (an in-flight HTTP call) — cancel for it remains best-effort/future; the engine's between-batch `should_cancel` still bounds it. Completed nodes are persisted before cancelling, so the audit stays accurate.

## Retry semantics

Two distinct retry layers, deliberately kept separate:

- **Node-level retry (inside the engine):** a backend that fails transiently is retried `node.max_retries` times within a single job, then the node escalates to a human gate. Unchanged by this ADR.
- **Job-level recovery (the durable layer):** a job whose lease expires (runner crashed or stalled) is returned to `queued` by `release_expired_leases` if `attempt < max_attempts`, else marked `failed`. A job that raises a caught application error during the drive is marked `failed` **terminally** — deterministic application failures are not auto-retried (that would just replay the same failure and double-write audit). Only lease loss, i.e. a missing/crashed runner, triggers a job-level retry.

## Observability

Runner status should be visible without opening raw logs.

Minimum UI/API data:

- queued/running/leased/failed job counts,
- worker list,
- active job per worker,
- last heartbeat,
- last error,
- stale lease count,
- cancellation count.

This is separate from agent reasoning/live output. Runner observability answers "is the execution system healthy?"

## Security Boundary

Durable runner execution depends on API identity hardening.

Gate decisions must come from authenticated principals, not request body strings. External runners must not be able to approve gates; they execute already-authorized work. Runner credentials should allow job claim/update, event/audit writes, and heartbeat, but not user-level administrative actions.

This ADR assumes the API boundary will be hardened before team/enterprise mode is exposed.

## Consequences

Positive:

- Desktop and team modes share one execution semantics.
- Sidecar restart no longer automatically destroys long-running work.
- Backpressure, worker health, cancellation, and retry become explicit.
- External self-hosted runners become a natural extension of the architecture.
- The custom engine remains useful and does not need to solve worker durability itself.

Negative:

- Persistence surface grows.
- Tests must cover job lifecycle and recovery, not only engine behavior.
- API/runtime complexity increases.
- SQLite local mode must be carefully scoped to avoid pretending it is a multi-runner coordination backend.

## Implementation Plan

1. Extend `RunStore` with job/worker/cancellation methods.
2. Implement SQLite job store semantics first.
3. Add a runner module with an embedded runner loop.
4. Change `POST /api/runs` to enqueue `drive_run` instead of starting daemon background work.
5. Change gate approve/reject to enqueue `resume_run`.
6. Replace orphan recovery with lease/job recovery.
7. Add runner status endpoints.
8. Add Postgres job/worker/cancellation implementation.
9. Add external runner CLI/process entrypoint.
10. Add cancellation.
11. Add tests for lease expiry, duplicate claim prevention, gate resume, runner crash recovery, and embedded mode.

## Acceptance Criteria

- No product API route directly starts long-running engine work in a daemon thread.
- A run can be started, queued, claimed, executed, and completed by an embedded runner.
- A waiting gate can be approved and resumed through a queued `resume_run` job.
- Killing a runner leaves a lease that expires and can be retried.
- Two runners cannot claim the same job.
- Runner status is visible via API.
- Existing engine unit tests remain green.
- SQLite works for single embedded runner mode.
- Postgres works for multiple external runners before team mode is declared supported.

## Implementation status (2026-06-26)

Done in the v0.2 slice:

- jobs/workers/cancellations tables + atomic claim on both SQLite (`BEGIN IMMEDIATE`) and Postgres (`SELECT … FOR UPDATE SKIP LOCKED`),
- embedded runner hosted as a sidecar thread; external runner process entrypoint (`moira_runner.py`),
- `POST /api/runs`, discovery, gate approve/reject, and rerun enqueue jobs instead of daemon threads,
- orphan recovery replaced with lease release,
- **lease heartbeat during execution** (prevents the long-run double-execution that the missing heartbeat would have caused),
- **lost-lease detection**: `complete_job`/`mark_job_running` report rows affected; a worker that lost its lease aborts/logs instead of silently "succeeding",
- external mode refuses SQLite (requires Postgres).

Also done (B1): mid-drive cancellation (engine `should_cancel` + runner kill) and `ClaudeCodeBackend` subprocess kill.

Deferred: durable `eval`/`report` jobs, live Postgres multi-runner soak test, `LiteLLMBackend` in-flight cancel.

## Open Questions

- ~~Exact Postgres claim implementation~~ — **resolved:** `SELECT … FOR UPDATE SKIP LOCKED`.
- ~~Whether `eval` remains synchronous~~ — **resolved for v0.2:** eval stays a documented synchronous control-plane exception (above); revisit only if eval becomes long-running/repo-mutating.
- Whether the embedded runner starts automatically in every local sidecar or is controlled by `MOIRA_RUNNER_MODE` (currently: starts unless `MOIRA_RUNNER_MODE` is `off`/`external`).
- ~~Active Claude/Codex subprocess cancellation requires backend interface changes~~ — **resolved (B1):** `BackendRegistry.cancel_active()` + `ClaudeCodeBackend.cancel()` kill in-flight subprocesses; `LiteLLMBackend` HTTP-call cancel remains best-effort/future.
