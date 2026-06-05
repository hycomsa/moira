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
| `MOIRA_PRIMARY` | `sqlite` \| `postgres` | `sqlite` |
| `MOIRA_DB` | SQLite file path | `.moira/moira.sqlite` |
| `MOIRA_PG_DSN` | Postgres DSN (when primary = postgres) | — |
| `MOIRA_GIT_EXPORT` | `1` to enable the git audit mirror | `0` |
| `MOIRA_GIT_REPO` | git target (fallback; normally the run's workspace `repo_path`) | workspace repo |

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
- Tamper-evidence (hash-chaining audit rows) and a background commit worker are
  noted as future work in `ADR-005` — not implemented yet.
- Backup/retention: for Postgres use standard `pg_dump`; for the git mirror, the
  history *is* the backup (push the AI SDLC repo to your remote).
```
