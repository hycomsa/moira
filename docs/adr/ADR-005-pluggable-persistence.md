# ADR-005: Pluggable run/audit persistence (primary store + export sinks)

**Date:** 2026-06-04  
**Status:** Accepted  
**Deciders:** Tomasz Skonieczny
**Amended by:** ADR-006 — durable runner execution model

## Context

Run state, the append-only event log, and audit records (the defensible core:
input · output · tools · decisions · approvals · cost · time · owner) were
persisted by a single concrete class, `Store`, backed by one local SQLite file
(`.moira/moira.sqlite`). That is correct for a single-dev desktop run, but it
contradicts the product thesis that **the audit trail is the defensible core**:
there was no central store, no git-native history, no team sharing, and deleting
one file lost everything.

Owner direction: run data should be able to live in **git** (git-native audit in
the AI SDLC repo, the single source of truth), a **central PostgreSQL**, or
**both** — selected by configuration, not by code changes. PostgreSQL is wanted
now (local Docker available), not merely as a future seam. Git commits should be
fast and happen **at phase/status transitions**.

ADR-006 extends the persistence responsibility: the primary store is also the
control-plane source of truth for durable execution jobs, worker heartbeats,
leases, and cancellation requests. Git remains an audit/export sink, not a job
queue or resume source.

## Decision

Introduce a persistence seam with a deliberate **read/write asymmetry**:

```
            reads  ─────────────►  PRIMARY  (one of)
                                   ├─ SQLite  (default, zero-dep)
                                   └─ PostgreSQL (central, team, queryable)

  writes ──► CompositeStore ──► primary (source of truth)
                          └────► EXPORT SINKS (write-only, 0..n)
                                   └─ Git mirror (.moira-runs/, commit on transition)
```

- **`RunStore`** (a `typing.Protocol`) is the full read+write contract the
  engine/API/CLI/runner call. `Store` (SQLite) and `PostgresRunStore` both satisfy it.
- **`ExportSink`** is a *write-only observer* — it never serves reads. Its
  commit/flush policy is its own; the engine never learns git (or anything)
  exists. `GitExportSink` is the first sink.
- **`CompositeStore`** is itself a `RunStore`: reads delegate to the primary
  only; each write goes to the primary first, then fans out to sinks inside a
  try/except. **A sink failure degrades the mirror, never the run.**
- **`make_run_store(...)`** builds the configuration from env
  (`MOIRA_PRIMARY`, `MOIRA_PG_DSN`, `MOIRA_GIT_EXPORT`, `MOIRA_GIT_REPO`),
  returning a bare primary when no sinks are configured (zero overhead).

Reads have exactly one home because git is an excellent *audit mirror* but a poor
*query/resume source*: the engine reads `get_run_state` / `audit_records` during
a run (and on resume after a gate), which a relational store answers with one
indexed query and git cannot do cheaply or race-free.

For durable execution, reads and atomic writes also have exactly one home: the
primary store. Job claiming, lease expiry, worker heartbeat, and cancellation
must never depend on the Git mirror.

### Durable execution specifics (ADR-006)

The primary store adds three control-plane datasets:

- `jobs`: queued/leased/running/completed work items for `drive_run`,
  `resume_run`, and later eval/report work.
- `workers`: embedded or external runner status, capabilities, heartbeat, and
  active job.
- `cancellations`: persisted user/system cancellation requests.

SQLite is acceptable for one embedded local runner. PostgreSQL is the expected
primary for multi-runner/team execution because job claiming must be atomic
across processes.

### Git mirror specifics
- Layout under `<repo>/.moira-runs/<run-id>/`: `run.yaml`, `state.yaml`,
  `pipeline.json`, append-only `events.jsonl`, `audit/<step_id>.json`.
- **Commit on transitions** (run created, each state change, terminal status) →
  ~8–12 meaningful commits/run; intermediate event/audit writes are swept into
  the next transition commit (self-healing if a write was skipped).
- **Never touches the user's work**: `git add -- .moira-runs/<run-id>` +
  `git commit --only`; no `add -A`, no branch switching. In-process per-repo lock
  serializes commits; git's `index.lock` is the cross-process backstop.

### PostgreSQL specifics
- Same four tables; dialect-only differences (`%s`, `ON CONFLICT DO UPDATE`,
  DB-side `IDENTITY` on `events.seq` **and** `audit.seq` for globally monotonic
  ordering — which also fixes the per-instance `_seq` reset SQLite carried).
- `psycopg` (v3) is imported lazily, only on the Postgres path.

## Consequences

- The cockpit/CLI/engine/runner depend on the `RunStore` protocol,
  so swapping SQLite↔Postgres or adding the git mirror remains configuration,
  but durable runner semantics require both primary stores to implement the job
  and worker operations from ADR-006.
  Existing tests and `verify_*` scripts that construct `Store(path)` keep working
  unchanged — the abstraction is a superset, not a replacement.
- `psycopg` is the only new dependency and it is **optional** — the SQLite and
  git paths stay stdlib-only, preserving the zero-dep core (consistent with the
  posture in ADR-004).
- The audit trail can now be **git-native and reviewable in the same repo as the
  specs**, and/or **centralized in Postgres** for team queries and retention —
  directly strengthening the "audit is the defensible core" position.

## Open / future
- **Tamper-evidence:** hash-chain audit rows (each record links the previous
  record's hash) so the log cannot be silently altered. Lightweight; deferred.
- **Background commit worker:** ship synchronous scoped commits first; add a
  per-repo background queue only if measured commit latency under the synchronous
  HTTP handler hurts.
- **`.moira/persistence.json`:** env-first now; add a file-based config overlay
  when a richer setup is needed.
- The legacy SQLite per-instance `_seq` is left as-is (fixed only in Postgres) to
  avoid churn; revisit if SQLite is ever used as a shared multi-writer store.
- **Job claiming implementation:** SQLite can start with transaction-guarded
  single embedded runner semantics; Postgres must use atomic claim semantics
  suitable for multiple external runners.
