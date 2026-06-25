"""SQLite persistence for runs, append-only event log, and audit records.

Maps to operating-model.md:
- pillar 4 (event log): append-only, ordered, the source of the audit record
- audit record: the defensible core

Design constraints baked in from day 1 (so we don't paint into a corner):
- events table is append-only (no UPDATE/DELETE in the API)
- every record carries an owner
- monotonic seq for ordering / future hash-chaining
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from . import integrity
from .models import AuditRecord, Event


SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    repo_path   TEXT NOT NULL,          -- AI SDLC repo (single source of truth)
    code_path   TEXT,                   -- software repo (where agents write code)
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    pipeline_id  TEXT NOT NULL,
    pipeline     TEXT NOT NULL,         -- json snapshot of the pipeline def
    owner        TEXT NOT NULL,
    status       TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT '{}',  -- json: {node_id: status} for DAG resume
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,   -- monotonic order
    run_id   TEXT NOT NULL,
    kind     TEXT NOT NULL,
    node_id  TEXT,
    message  TEXT NOT NULL,
    ts       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    step_id    TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    node_name  TEXT NOT NULL,
    owner      TEXT NOT NULL,
    status     TEXT NOT NULL,
    record     TEXT NOT NULL,           -- json of the full AuditRecord
    seq        INTEGER NOT NULL,
    ts         REAL NOT NULL
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
    lease_until  REAL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL,
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
    heartbeat_at  REAL NOT NULL,
    last_error    TEXT
);

CREATE TABLE IF NOT EXISTS cancellations (
    run_id       TEXT PRIMARY KEY,
    requested_by TEXT NOT NULL,
    reason       TEXT,
    requested_at REAL NOT NULL,
    honored_at   REAL
);
"""


class Store:
    def __init__(self, db_path: str | Path = ".moira/moira.sqlite"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        # WAL + a busy timeout so a request-thread read and a background drive-thread
        # write (non-blocking run launch) don't collide with "database is locked".
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            pass
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._seq = 0

    # ---- workspaces -------------------------------------------------------- #
    def create_workspace(self, ws_id: str, name: str, repo_path: str,
                         code_path: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO workspaces(id, name, repo_path, code_path, created_at)"
            " VALUES(?,?,?,?,?)",
            (ws_id, name, repo_path, code_path, time.time()),
        )
        self.conn.commit()

    def list_workspaces(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, name, repo_path, code_path, created_at FROM workspaces ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_workspace(self, ws_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM workspaces WHERE id=?", (ws_id,)).fetchone()
        return dict(row) if row else None

    # ---- runs -------------------------------------------------------------- #
    def create_run(self, run_id: str, pipeline_id: str, pipeline_json: dict,
                   owner: str, status: str, workspace_id: str = "default") -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO runs(run_id, workspace_id, pipeline_id, pipeline, owner, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (run_id, workspace_id, pipeline_id, json.dumps(pipeline_json), owner, status, now, now),
        )
        self.conn.commit()

    def update_run_status(self, run_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
            (status, time.time(), run_id),
        )
        self.conn.commit()

    def save_run_state(self, run_id: str, state: dict[str, str]) -> None:
        self.conn.execute("UPDATE runs SET state=?, updated_at=? WHERE run_id=?",
                          (json.dumps(state), time.time(), run_id))
        self.conn.commit()

    def get_run_state(self, run_id: str) -> dict[str, str]:
        row = self.conn.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row["state"]) if row and row["state"] else {}

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, workspace_id: Optional[str] = None) -> list[dict[str, Any]]:
        if workspace_id:
            rows = self.conn.execute(
                "SELECT run_id, workspace_id, pipeline_id, owner, status, created_at, updated_at"
                " FROM runs WHERE workspace_id=? ORDER BY created_at DESC", (workspace_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT run_id, workspace_id, pipeline_id, owner, status, created_at, updated_at"
                " FROM runs ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- events (append-only) --------------------------------------------- #
    def append_event(self, ev: Event) -> int:
        cur = self.conn.execute(
            "INSERT INTO events(run_id, kind, node_id, message, ts) VALUES(?,?,?,?,?)",
            (ev.run_id, ev.kind, ev.node_id, ev.message, ev.ts),
        )
        self.conn.commit()
        return cur.lastrowid

    def events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT seq, kind, node_id, message, ts FROM events WHERE run_id=? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- audit records ----------------------------------------------------- #
    def save_audit(self, rec: AuditRecord) -> None:
        self._seq += 1
        # tamper-evidence: chain this record to the run's previous one (rowid =
        # true insertion order, stable across the per-instance _seq reset)
        prev = self.conn.execute(
            "SELECT record FROM audit WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (rec.run_id,)
        ).fetchone()
        prev_hash = json.loads(prev["record"]).get("hash", "") if prev else integrity.GENESIS
        body = integrity.seal(rec.to_dict(), prev_hash)
        self.conn.execute(
            "INSERT OR REPLACE INTO audit(step_id, run_id, node_id, node_name, owner, status, record, seq, ts)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (rec.step_id, rec.run_id, rec.node_id, rec.node_name, rec.owner,
             rec.status, json.dumps(body), self._seq, time.time()),
        )
        self.conn.commit()

    def audit_records(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT record FROM audit WHERE run_id=? ORDER BY rowid", (run_id,)
        ).fetchall()
        return [json.loads(r["record"]) for r in rows]

    def run_cost(self, run_id: str) -> dict[str, Any]:
        """Aggregate cost across all steps of a run."""
        tokens_in = tokens_out = 0
        usd = 0.0
        for rec in self.audit_records(run_id):
            c = rec.get("cost") or {}
            tokens_in += c.get("tokens_in", 0)
            tokens_out += c.get("tokens_out", 0)
            usd += c.get("usd", 0.0)
        return {"tokens_in": tokens_in, "tokens_out": tokens_out, "usd": round(usd, 4)}

    # ---- durable execution jobs (ADR-006) ----------------------------------- #
    @staticmethod
    def _decode_job(row: sqlite3.Row | None) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        return d

    @staticmethod
    def _decode_worker(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["capabilities"] = json.loads(d.get("capabilities") or "[]")
        except json.JSONDecodeError:
            d["capabilities"] = []
        return d

    def enqueue_job(self, job: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        payload = json.dumps(job.get("payload") or {})
        self.conn.execute(
            "INSERT INTO jobs(job_id, run_id, workspace_id, kind, status, payload,"
            " attempt, max_attempts, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job["job_id"], job["run_id"], job.get("workspace_id", "default"),
             job["kind"], job.get("status", "queued"), payload,
             int(job.get("attempt", 0)), int(job.get("max_attempts", 3)), now, now),
        )
        self.conn.commit()
        return self.get_job(job["job_id"]) or job

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._decode_job(row)

    def jobs(self, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE run_id=? ORDER BY created_at", (run_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
        return [self._decode_job(r) for r in rows if r is not None]  # type: ignore[arg-type]

    def claim_next_job(self, worker_id: str, capabilities: list[str] | None = None,
                       lease_seconds: int = 300) -> Optional[dict[str, Any]]:
        now = time.time()
        lease_until = now + lease_seconds
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT * FROM jobs"
                " WHERE status='queued'"
                " ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                self.conn.commit()
                return None
            job = self._decode_job(row)
            self.conn.execute(
                "UPDATE jobs SET status='leased', lease_owner=?, lease_until=?,"
                " attempt=attempt+1, updated_at=? WHERE job_id=?",
                (worker_id, lease_until, now, job["job_id"]),
            )
            self.conn.commit()
            return self.get_job(job["job_id"])
        except sqlite3.Error:
            self.conn.rollback()
            raise

    def mark_job_running(self, job_id: str, worker_id: str) -> int:
        now = time.time()
        cur = self.conn.execute(
            "UPDATE jobs SET status='running', started_at=COALESCE(started_at, ?),"
            " updated_at=? WHERE job_id=? AND lease_owner=?",
            (now, now, job_id, worker_id),
        )
        self.conn.commit()
        return cur.rowcount

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int = 300) -> int:
        now = time.time()
        cur = self.conn.execute(
            "UPDATE jobs SET lease_until=?, updated_at=? WHERE job_id=? AND lease_owner=?"
            " AND status IN ('leased','running')",
            (now + lease_seconds, now, job_id, worker_id),
        )
        self.conn.commit()
        return cur.rowcount

    def complete_job(self, job_id: str, worker_id: str, status: str,
                     error: str | None = None) -> int:
        now = time.time()
        cur = self.conn.execute(
            "UPDATE jobs SET status=?, finished_at=?, updated_at=?, last_error=?,"
            " lease_owner=NULL, lease_until=NULL WHERE job_id=? AND lease_owner=?",
            (status, now, now, error, job_id, worker_id),
        )
        self.conn.commit()
        return cur.rowcount

    def release_expired_leases(self, now: float | None = None) -> int:
        now = now or time.time()
        cur = self.conn.execute(
            "UPDATE jobs SET status=CASE WHEN attempt < max_attempts THEN 'queued' ELSE 'failed' END,"
            " lease_owner=NULL, lease_until=NULL, updated_at=?,"
            " last_error=CASE WHEN attempt < max_attempts THEN last_error ELSE 'lease expired; attempts exhausted' END"
            " WHERE status IN ('leased','running') AND lease_until IS NOT NULL AND lease_until < ?",
            (now, now),
        )
        self.conn.commit()
        return cur.rowcount

    def request_cancellation(self, run_id: str, by: str, reason: str = "") -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO cancellations(run_id, requested_by, reason, requested_at, honored_at)"
            " VALUES(?,?,?,?,NULL)"
            " ON CONFLICT(run_id) DO UPDATE SET requested_by=excluded.requested_by,"
            " reason=excluded.reason, requested_at=excluded.requested_at, honored_at=NULL",
            (run_id, by, reason, now),
        )
        self.conn.commit()

    def cancellation_requested(self, run_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM cancellations WHERE run_id=? AND honored_at IS NULL", (run_id,)
        ).fetchone()
        return row is not None

    def honor_cancellation(self, run_id: str) -> None:
        self.conn.execute(
            "UPDATE cancellations SET honored_at=? WHERE run_id=? AND honored_at IS NULL",
            (time.time(), run_id),
        )
        self.conn.commit()

    def upsert_worker(self, worker: dict[str, Any]) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO workers(worker_id, mode, host, pid, version, capabilities, status,"
            " active_job_id, heartbeat_at, last_error)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(worker_id) DO UPDATE SET mode=excluded.mode, host=excluded.host,"
            " pid=excluded.pid, version=excluded.version, capabilities=excluded.capabilities,"
            " status=excluded.status, active_job_id=excluded.active_job_id,"
            " heartbeat_at=excluded.heartbeat_at, last_error=excluded.last_error",
            (worker["worker_id"], worker.get("mode", "embedded"), worker.get("host"),
             worker.get("pid"), worker.get("version", "0.1"),
             json.dumps(worker.get("capabilities") or []), worker.get("status", "running"),
             worker.get("active_job_id"), now, worker.get("last_error")),
        )
        self.conn.commit()

    def heartbeat_worker(self, worker_id: str, active_job_id: str | None = None,
                         status: str = "running") -> None:
        self.conn.execute(
            "UPDATE workers SET heartbeat_at=?, active_job_id=?, status=? WHERE worker_id=?",
            (time.time(), active_job_id, status, worker_id),
        )
        self.conn.commit()

    def workers(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM workers ORDER BY heartbeat_at DESC").fetchall()
        return [self._decode_worker(r) for r in rows]

    def close(self) -> None:
        self.conn.close()


# `Store` is the SQLite implementation of the `RunStore` protocol
# (see persistence.py). The alias makes that explicit at call sites that want it.
SqliteRunStore = Store
