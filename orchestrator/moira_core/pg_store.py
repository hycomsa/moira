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

    def close(self) -> None:
        self.conn.close()
