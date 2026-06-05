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

    def close(self) -> None:
        self.conn.close()


# `Store` is the SQLite implementation of the `RunStore` protocol
# (see persistence.py). The alias makes that explicit at call sites that want it.
SqliteRunStore = Store
