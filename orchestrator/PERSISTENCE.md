# Persistence — where run / audit data lives

Moira's run state, append-only event log, and audit records (the defensible
core: input · output · tools · decisions · approvals · cost · time · owner) are
written through a **pluggable persistence layer**. You choose the destination(s)
by config — no code change:

| Destination | What it's for | Reads? |
|---|---|---|
| **SQLite** (default) | single-dev / desktop, zero setup | yes (primary) |
| **PostgreSQL** | central, team-shared, queryable, retention | yes (primary) |
| **Git** (`.moira-runs/`) | git-native audit history next to the specs | no (write-only mirror) |

**Architecture** (see `ADR-005`): exactly one **primary** `RunStore`
(SQLite *or* Postgres) answers all reads and is the source of truth; zero-or-more
**export sinks** (git) receive writes as a mirror. A `CompositeStore` fans writes
out — a sink failure degrades the mirror, never the run. The engine/API/CLI are
unchanged: they talk to the `RunStore` protocol and never learn which backend or
sink is active.

## Configuration (env vars)

| Var | Meaning | Default |
|---|---|---|
| **Persistence** | | |
| `MOIRA_PRIMARY` | `sqlite` \| `postgres` | `sqlite` |
| `MOIRA_DB` | SQLite file path | `.moira/moira.sqlite` |
| `MOIRA_PG_DSN` | Postgres DSN (when primary = postgres; needs `psycopg[binary]`) | — |
| `MOIRA_GIT_EXPORT` | `1` to enable the git audit mirror | `0` |
| `MOIRA_GIT_REPO` | git target (fallback; normally the run's workspace `repo_path`) | workspace repo |
| **Durable runner (ADR-006)** | | |
| `MOIRA_RUNNER_MODE` | `embedded` \| `external` \| `off` (embedded runner host) | `embedded` |
| `MOIRA_RUNNER_LEASE_SECONDS` | job lease timeout | `300` |
| `MOIRA_RUNNER_POLL_SECONDS` | runner idle poll interval | `0.5` |
| `MOIRA_JOB_MAX_ATTEMPTS` | durable job max retry attempts | `3` |
| `MOIRA_RETRY_BACKOFF` | base seconds of the linear backoff between BACKEND retry attempts within a node (`sleep(min(30, base × failures))`; the retry prompt carries the previous errors — ADR-011) | `2` |
| **Auth / RBAC (ADR-008)** | | |
| `MOIRA_AUTH_MODE` | `off` \| `local` \| `oidc` (off ⇒ enforcement disabled) | `off` |
| `MOIRA_AUTH_SECRET` | HS256 secret for local self-issued tokens | random/process |
| `MOIRA_OIDC_JWKS_URL` | OIDC JWKS endpoint (required when mode=oidc; needs `PyJWT[crypto]`) | — |
| `MOIRA_OIDC_ISSUER` / `MOIRA_OIDC_AUDIENCE` | OIDC issuer / audience claims to verify | — |
| `MOIRA_OIDC_GROUP_ROLES` | JSON map `{group: role}` → 5 roles | `{}` |
| **Agent backend budgets** | | |
| `MOIRA_CLAUDE_TIMEOUT` / `_MAX_TURNS` | default agent timeout (s) / turn budget | `600` / `12` |
| `MOIRA_CLAUDE_HEAVY_TIMEOUT` / `_HEAVY_MAX_TURNS` | coding-agent budget | `1800` / `40` |
| `MOIRA_CLAUDE_SKILL_TIMEOUT` / `_SKILL_MAX_TURNS` | discovery-skill budget | `300` / `20` |
| `MOIRA_DEBUG` | `1` records the exact claude cmd/prompt into the audit | `0` |
| `MOIRA_LOG` | sidecar logfile path | app-data |

Git commits in the mirror happen **synchronously** on run-status transitions (a
background commit worker is future work).

The four scenarios:

```sh
# 1) Default — local SQLite, nothing to set
python3 moira_api.py

# 2) Git-native audit alongside SQLite
MOIRA_GIT_EXPORT=1 python3 moira_api.py

# 3) Central Postgres
MOIRA_PRIMARY=postgres \
MOIRA_PG_DSN=postgresql://moira:moira@localhost:5432/moira \
  python3 moira_api.py

# 4) Both — Postgres as the queryable store + git as the human-readable history
MOIRA_PRIMARY=postgres \
MOIRA_PG_DSN=postgresql://moira:moira@localhost:5432/moira \
MOIRA_GIT_EXPORT=1 \
  python3 moira_api.py
```

## PostgreSQL — local dev setup

```sh
cd orchestrator

# 1. Start Postgres (named volume keeps data across restarts).
#    If host port 5432 is already taken, override it:
#      MOIRA_PG_PORT=25460 docker compose up -d db   (then use :25460 in the DSN)
docker compose up -d db
#   …or without compose:
#   docker run -d --name moira-postgres -p 5432:5432 \
#     -e POSTGRES_USER=moira -e POSTGRES_PASSWORD=moira -e POSTGRES_DB=moira postgres:16

# 2. Install the driver (optional dependency — only the Postgres path needs it).
#    Use a venv; system pip is often blocked (PEP 668).
python3 -m venv .venv && . .venv/bin/activate
pip install "psycopg[binary]"

# 3. Point Moira at it and run
export MOIRA_PRIMARY=postgres
export MOIRA_PG_DSN=postgresql://moira:moira@localhost:5432/moira
python3 moira_api.py --repo /path/to/ai-sdlc-repo

# 4. Verify rows land
docker exec -it moira-postgres psql -U moira -d moira -c \
  "select run_id, status, pipeline_id from runs order by created_at desc limit 5;"
docker exec -it moira-postgres psql -U moira -d moira -c \
  "select node_id, status, owner from audit order by seq desc limit 10;"
```

The schema (4 tables: `workspaces`, `runs`, `events`, `audit`) is created
automatically on first connect. `events.seq` and `audit.seq` use DB-side
`IDENTITY`, so ordering is globally monotonic across processes/connections.

## Git mirror — layout & commit behavior

When `MOIRA_GIT_EXPORT=1`, each run is mirrored into the workspace's AI SDLC repo:

```
<repo>/.moira-runs/<run-id>/
  run.yaml        # run_id, pipeline_id, owner, status, workspace_id (overwritten)
  state.yaml      # {node_id: status} — the resume map; diffs show transitions
  pipeline.json   # the pipeline snapshot (written once)
  events.jsonl    # append-only, one JSON line per event
  audit/<step_id>.json  # one file per audit record (overwrite-by-step)
```

- **Commit on status transitions** (~8–12 commits/run): run created, each state
  change, and the terminal status. Events/audit between transitions are written
  immediately and swept into the next transition commit.
- **Your work is never touched:** commits are scoped to `.moira-runs/<run-id>`
  (`git add -- <pathspec>`, `git commit --only`). No `git add -A`, no branch
  switching. A repo with no `.git` is `git init`-ed on first use.
- Inspect a run's history: `git log --oneline -- .moira-runs/<run-id>`.

## Notes / honesty

- `psycopg` is the **only** new dependency, and it's optional — loaded lazily
  only when `MOIRA_PRIMARY=postgres`. The SQLite and git paths remain
  stdlib-only, consistent with the zero-dep core.
- **Tamper-evidence (implemented):** every audit record is sealed into a per-run
  hash chain (`prev_hash` + `hash`, see `integrity.py`). The primary store and the
  git mirror (`.moira-runs/<run>/audit/*.json`) carry the **same sealed record**,
  so the reviewable git artifact is itself verifiable — `verify_chain` (ordered,
  primary) and `verify_export` (order reconstructed from links, git mirror) detect
  any silent edit, drop, or reorder. Exposed at `GET /api/runs/{id}/verify`; the
  run report shows chain status, length, and head hash. This is **tamper-evident,
  not signed** — it proves the log wasn't silently altered, not *who* wrote it.
  Cryptographic signing of the chain head / terminal report (signer identity, key
  id, rotation) is future enterprise work. A background commit worker is also
  still future work.
- Backup/retention: for Postgres use standard `pg_dump`; for the git mirror, the
  history *is* the backup (push the AI SDLC repo to your remote).
```
