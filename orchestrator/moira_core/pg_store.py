"""PostgreSQL implementation of `RunStore` — the central / team store.

Same four tables and identical method contracts as the SQLite `Store`, so it
drops in behind the `RunStore` protocol with no caller changes. Differences are
dialect-only:
- `?`            -> `%s`
- `INSERT OR REPLACE` -> `INSERT ... ON CONFLICT (pk) DO UPDATE`
- SQLite AUTOINCREMENT / per-instance `_seq` -> DB-side IDENTITY on events.seq
  AND audit.seq (RETURNING seq), so ordering is globally monotonic regardless of
  how many processes/connections write (fixes the per-instance `_seq` reset).
- JSON stored as TEXT via json.dumps/loads (exactly like SQLite) to keep the
  read path identical and avoid jsonb adaptation quirks.

`psycopg` (v3) is imported lazily here, so the core / sqlite / git paths stay
stdlib-only; only the Postgres path needs `pip install "psycopg[binary]"`.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from . import integrity
from .models import AuditRecord, Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    repo_path   TEXT NOT NULL,
    code_path   TEXT,
    created_at  DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    pipeline_id  TEXT NOT NULL,
    pipeline     TEXT NOT NULL,
    owner        TEXT NOT NULL,
    status       TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT '{}',
    created_at   DOUBLE PRECISION NOT NULL,
    updated_at   DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id   TEXT NOT NULL,
    kind     TEXT NOT NULL,
    node_id  TEXT,
    message  TEXT NOT NULL,
    ts       DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    step_id    TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    node_name  TEXT NOT NULL,
    owner      TEXT NOT NULL,
    status     TEXT NOT NULL,
    record     TEXT NOT NULL,
    seq        BIGINT GENERATED ALWAYS AS IDENTITY,
    ts         DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_run  ON audit(run_id, seq);

CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',
    attempt      INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner  TEXT,
    lease_until  DOUBLE PRECISION,
    created_at   DOUBLE PRECISION NOT NULL,
    updated_at   DOUBLE PRECISION NOT NULL,
    started_at   DOUBLE PRECISION,
    finished_at  DOUBLE PRECISION,
    last_error   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_run ON jobs(run_id, created_at);

CREATE TABLE IF NOT EXISTS workers (
    worker_id     TEXT PRIMARY KEY,
    mode          TEXT NOT NULL,
    host          TEXT,
    pid           INTEGER,
    version       TEXT,
    capabilities  TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL,
    active_job_id TEXT,
    heartbeat_at  DOUBLE PRECISION NOT NULL,
    last_error    TEXT
);

CREATE TABLE IF NOT EXISTS cancellations (
    run_id       TEXT PRIMARY KEY,
    requested_by TEXT NOT NULL,
    reason       TEXT,
    requested_at DOUBLE PRECISION NOT NULL,
    honored_at   DOUBLE PRECISION
);
"""


class PostgresRunStore:
    """`RunStore` backed by PostgreSQL via psycopg v3."""

    def __init__(self, dsn: str):
        import psycopg  # lazy — only this path needs the driver
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA)

    # ---- workspaces ------------------------------------------------------- #
    def create_workspace(self, ws_id: str, name: str, repo_path: str,
                         code_path: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO workspaces(id, name, repo_path, code_path, created_at)"
            " VALUES(%s,%s,%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,"
            " repo_path=EXCLUDED.repo_path, code_path=EXCLUDED.code_path",
            (ws_id, name, repo_path, code_path, time.time()),
        )

    def list_workspaces(self) -> list[dict[str, Any]]:
        return list(self.conn.execute(
            "SELECT id, name, repo_path, code_path, created_at FROM workspaces ORDER BY created_at"
        ).fetchall())

    def get_workspace(self, ws_id: str) -> Optional[dict[str, Any]]:
        return self.conn.execute("SELECT * FROM workspaces WHERE id=%s", (ws_id,)).fetchone()

    # ---- runs ------------------------------------------------------------- #
    def create_run(self, run_id: str, pipeline_id: str, pipeline_json: dict,
                   owner: str, status: str, workspace_id: str = "default") -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO runs(run_id, workspace_id, pipeline_id, pipeline, owner, status, created_at, updated_at)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, workspace_id, pipeline_id, json.dumps(pipeline_json), owner, status, now, now),
        )

    def update_run_status(self, run_id: str, status: str) -> None:
        self.conn.execute("UPDATE runs SET status=%s, updated_at=%s WHERE run_id=%s",
                          (status, time.time(), run_id))

    def save_run_state(self, run_id: str, state: dict[str, str]) -> None:
        self.conn.execute("UPDATE runs SET state=%s, updated_at=%s WHERE run_id=%s",
                          (json.dumps(state), time.time(), run_id))

    def get_run_state(self, run_id: str) -> dict[str, str]:
        row = self.conn.execute("SELECT state FROM runs WHERE run_id=%s", (run_id,)).fetchone()
        return json.loads(row["state"]) if row and row["state"] else {}

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        return self.conn.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone()

    def list_runs(self, workspace_id: Optional[str] = None) -> list[dict[str, Any]]:
        cols = ("SELECT run_id, workspace_id, pipeline_id, owner, status, created_at, updated_at"
                " FROM runs")
        if workspace_id:
            return list(self.conn.execute(
                cols + " WHERE workspace_id=%s ORDER BY created_at DESC", (workspace_id,)).fetchall())
        return list(self.conn.execute(cols + " ORDER BY created_at DESC").fetchall())

    # ---- events (append-only) -------------------------------------------- #
    def append_event(self, ev: Event) -> int:
        row = self.conn.execute(
            "INSERT INTO events(run_id, kind, node_id, message, ts) VALUES(%s,%s,%s,%s,%s)"
            " RETURNING seq",
            (ev.run_id, ev.kind, ev.node_id, ev.message, ev.ts),
        ).fetchone()
        return int(row["seq"])

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.conn.execute(
            "SELECT seq, kind, node_id, message, ts FROM events WHERE run_id=%s ORDER BY seq",
            (run_id,)).fetchall())

    # ---- audit ------------------------------------------------------------ #
    def save_audit(self, rec: AuditRecord) -> None:
        prev = self.conn.execute(
            "SELECT record FROM audit WHERE run_id=%s ORDER BY seq DESC LIMIT 1", (rec.run_id,)
        ).fetchone()
        prev_hash = json.loads(prev["record"]).get("hash", "") if prev else integrity.GENESIS
        body = integrity.seal(rec.to_dict(), prev_hash)
        self.conn.execute(
            "INSERT INTO audit(step_id, run_id, node_id, node_name, owner, status, record, ts)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (step_id) DO UPDATE SET status=EXCLUDED.status,"
            " record=EXCLUDED.record, node_name=EXCLUDED.node_name, ts=EXCLUDED.ts",
            (rec.step_id, rec.run_id, rec.node_id, rec.node_name, rec.owner,
             rec.status, json.dumps(body), time.time()),
        )

    def audit_records(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT record FROM audit WHERE run_id=%s ORDER BY seq", (run_id,)).fetchall()
        return [json.loads(r["record"]) for r in rows]

    def run_cost(self, run_id: str) -> dict[str, Any]:
        tokens_in = tokens_out = 0
        usd = 0.0
        for rec in self.audit_records(run_id):
            c = rec.get("cost") or {}
            tokens_in += c.get("tokens_in", 0)
            tokens_out += c.get("tokens_out", 0)
            usd += c.get("usd", 0.0)
        return {"tokens_in": tokens_in, "tokens_out": tokens_out, "usd": round(usd, 4)}

    # ---- durable execution jobs (ADR-006) ---------------------------------- #
    @staticmethod
    def _decode_job(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        return d

    @staticmethod
    def _decode_worker(row: dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        try:
            d["capabilities"] = json.loads(d.get("capabilities") or "[]")
        except json.JSONDecodeError:
            d["capabilities"] = []
        return d

    def enqueue_job(self, job: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        self.conn.execute(
            "INSERT INTO jobs(job_id, run_id, workspace_id, kind, status, payload,"
            " attempt, max_attempts, created_at, updated_at)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (job["job_id"], job["run_id"], job.get("workspace_id", "default"),
             job["kind"], job.get("status", "queued"), json.dumps(job.get("payload") or {}),
             int(job.get("attempt", 0)), int(job.get("max_attempts", 3)), now, now),
        )
        return self.get_job(job["job_id"]) or job

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id=%s", (job_id,)).fetchone()
        return self._decode_job(row)

    def jobs(self, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE run_id=%s ORDER BY created_at", (run_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
        return [self._decode_job(r) for r in rows if r is not None]  # type: ignore[arg-type]

    def claim_next_job(self, worker_id: str, capabilities: list[str] | None = None,
                       lease_seconds: int = 300) -> Optional[dict[str, Any]]:
        now = time.time()
        row = self.conn.execute(
            "UPDATE jobs SET status='leased', lease_owner=%s, lease_until=%s,"
            " attempt=attempt+1, updated_at=%s"
            " WHERE job_id = ("
            "   SELECT job_id FROM jobs WHERE status='queued'"
            "   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            " ) RETURNING *",
            (worker_id, now + lease_seconds, now),
        ).fetchone()
        return self._decode_job(row)

    def mark_job_running(self, job_id: str, worker_id: str) -> None:
        now = time.time()
        self.conn.execute(
            "UPDATE jobs SET status='running', started_at=COALESCE(started_at, %s),"
            " updated_at=%s WHERE job_id=%s AND lease_owner=%s",
            (now, now, job_id, worker_id),
        )

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int = 300) -> None:
        now = time.time()
        self.conn.execute(
            "UPDATE jobs SET lease_until=%s, updated_at=%s WHERE job_id=%s AND lease_owner=%s"
            " AND status IN ('leased','running')",
            (now + lease_seconds, now, job_id, worker_id),
        )

    def complete_job(self, job_id: str, worker_id: str, status: str,
                     error: str | None = None) -> None:
        now = time.time()
        self.conn.execute(
            "UPDATE jobs SET status=%s, finished_at=%s, updated_at=%s, last_error=%s,"
            " lease_owner=NULL, lease_until=NULL WHERE job_id=%s AND lease_owner=%s",
            (status, now, now, error, job_id, worker_id),
        )

    def release_expired_leases(self, now: float | None = None) -> int:
        now = now or time.time()
        cur = self.conn.execute(
            "UPDATE jobs SET status=CASE WHEN attempt < max_attempts THEN 'queued' ELSE 'failed' END,"
            " lease_owner=NULL, lease_until=NULL, updated_at=%s,"
            " last_error=CASE WHEN attempt < max_attempts THEN last_error ELSE 'lease expired; attempts exhausted' END"
            " WHERE status IN ('leased','running') AND lease_until IS NOT NULL AND lease_until < %s",
            (now, now),
        )
        return cur.rowcount

    def request_cancellation(self, run_id: str, by: str, reason: str = "") -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO cancellations(run_id, requested_by, reason, requested_at, honored_at)"
            " VALUES(%s,%s,%s,%s,NULL)"
            " ON CONFLICT (run_id) DO UPDATE SET requested_by=EXCLUDED.requested_by,"
            " reason=EXCLUDED.reason, requested_at=EXCLUDED.requested_at, honored_at=NULL",
            (run_id, by, reason, now),
        )

    def cancellation_requested(self, run_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM cancellations WHERE run_id=%s AND honored_at IS NULL", (run_id,)
        ).fetchone()
        return row is not None

    def honor_cancellation(self, run_id: str) -> None:
        self.conn.execute(
            "UPDATE cancellations SET honored_at=%s WHERE run_id=%s AND honored_at IS NULL",
            (time.time(), run_id),
        )

    def upsert_worker(self, worker: dict[str, Any]) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO workers(worker_id, mode, host, pid, version, capabilities, status,"
            " active_job_id, heartbeat_at, last_error)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (worker_id) DO UPDATE SET mode=EXCLUDED.mode, host=EXCLUDED.host,"
            " pid=EXCLUDED.pid, version=EXCLUDED.version, capabilities=EXCLUDED.capabilities,"
            " status=EXCLUDED.status, active_job_id=EXCLUDED.active_job_id,"
            " heartbeat_at=EXCLUDED.heartbeat_at, last_error=EXCLUDED.last_error",
            (worker["worker_id"], worker.get("mode", "embedded"), worker.get("host"),
             worker.get("pid"), worker.get("version", "0.1"),
             json.dumps(worker.get("capabilities") or []), worker.get("status", "running"),
             worker.get("active_job_id"), now, worker.get("last_error")),
        )

    def heartbeat_worker(self, worker_id: str, active_job_id: str | None = None,
                         status: str = "running") -> None:
        self.conn.execute(
            "UPDATE workers SET heartbeat_at=%s, active_job_id=%s, status=%s WHERE worker_id=%s",
            (time.time(), active_job_id, status, worker_id),
        )

    def workers(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM workers ORDER BY heartbeat_at DESC").fetchall()
        return [self._decode_worker(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
