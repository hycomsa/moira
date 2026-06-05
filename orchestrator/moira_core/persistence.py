"""Pluggable persistence for runs / events / audit.

The orchestrator was born talking to one concrete `Store` (SQLite). This module
turns that into a seam so run data can live in SQLite, a central PostgreSQL, a
git-native mirror, or any combination — chosen by config, not code.

Shape (see ADR-005):
- `RunStore`     — the full read+write contract (what the engine/API/CLI call).
                   `Store` (sqlite) and `PostgresRunStore` both satisfy it.
- `ExportSink`   — a write-only observer (no reads). `GitExportSink` is one.
- `CompositeStore` — itself a `RunStore`; reads hit the *primary* only, writes
                   hit the primary first (source of truth) then fan out to sinks
                   (sink failures are logged, never fatal).
- `make_run_store(...)` — factory that reads env/config and wires the above.

Reads have exactly one home (the primary); writes fan out. That asymmetry is
deliberate: git is a great audit mirror but a poor query/resume source.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, Protocol, runtime_checkable

from .models import AuditRecord, Event

log = logging.getLogger("moira.persistence")


@runtime_checkable
class RunStore(Protocol):
    """Full persistence contract — the method surface the engine/API/CLI use."""

    # workspaces
    def create_workspace(self, ws_id: str, name: str, repo_path: str,
                         code_path: str | None = None) -> None: ...
    def list_workspaces(self) -> list[dict[str, Any]]: ...
    def get_workspace(self, ws_id: str) -> Optional[dict[str, Any]]: ...
    # runs
    def create_run(self, run_id: str, pipeline_id: str, pipeline_json: dict,
                   owner: str, status: str, workspace_id: str = "default") -> None: ...
    def update_run_status(self, run_id: str, status: str) -> None: ...
    def save_run_state(self, run_id: str, state: dict[str, str]) -> None: ...
    def get_run_state(self, run_id: str) -> dict[str, str]: ...
    def get_run(self, run_id: str) -> Optional[dict[str, Any]]: ...
    def list_runs(self, workspace_id: Optional[str] = None) -> list[dict[str, Any]]: ...
    # events (append-only)
    def append_event(self, ev: Event) -> int: ...
    def events(self, run_id: str) -> list[dict[str, Any]]: ...
    # audit
    def save_audit(self, rec: AuditRecord) -> None: ...
    def audit_records(self, run_id: str) -> list[dict[str, Any]]: ...
    def run_cost(self, run_id: str) -> dict[str, Any]: ...
    def close(self) -> None: ...


class ExportSink:
    """Write-only observer of run mutations. Override the hooks you care about.

    A sink never serves reads. Its commit/flush policy is its own business — the
    engine and composite have no idea git (or anything else) exists.
    """

    def on_run_created(self, run_id: str, pipeline_id: str, pipeline_json: dict,
                       owner: str, status: str, workspace_id: str,
                       repo_path: str | None) -> None: ...
    def on_run_status(self, run_id: str, status: str) -> None: ...
    def on_run_state(self, run_id: str, state: dict[str, str]) -> None: ...
    def on_event(self, ev: Event) -> None: ...
    def on_audit(self, rec: AuditRecord) -> None: ...
    def close(self) -> None: ...


class CompositeStore:
    """A `RunStore` that reads from `primary` and fans writes out to `sinks`.

    Satisfies the `RunStore` protocol structurally. Reads delegate to the
    primary only. Every write goes to the primary first (so the source of truth
    is never at the mercy of a sink), then each sink is notified inside a
    try/except — a misbehaving sink degrades the mirror, never the run.
    """

    def __init__(self, primary: RunStore, sinks: list[ExportSink] | None = None):
        self.primary = primary
        self.sinks = sinks or []

    def _fan(self, hook: str, *args: Any) -> None:
        for s in self.sinks:
            try:
                getattr(s, hook)(*args)
            except Exception as e:  # noqa: BLE001 — a sink must never break a run
                log.warning("export sink %s.%s failed: %s", type(s).__name__, hook, e)

    # ---- reads (primary only) --------------------------------------------- #
    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.primary.list_workspaces()

    def get_workspace(self, ws_id: str) -> Optional[dict[str, Any]]:
        return self.primary.get_workspace(ws_id)

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        return self.primary.get_run(run_id)

    def get_run_state(self, run_id: str) -> dict[str, str]:
        return self.primary.get_run_state(run_id)

    def list_runs(self, workspace_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self.primary.list_runs(workspace_id)

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return self.primary.events(run_id)

    def audit_records(self, run_id: str) -> list[dict[str, Any]]:
        return self.primary.audit_records(run_id)

    def run_cost(self, run_id: str) -> dict[str, Any]:
        return self.primary.run_cost(run_id)

    # ---- writes (primary, then fan out) ----------------------------------- #
    def create_workspace(self, ws_id: str, name: str, repo_path: str,
                         code_path: str | None = None) -> None:
        self.primary.create_workspace(ws_id, name, repo_path, code_path)

    def create_run(self, run_id: str, pipeline_id: str, pipeline_json: dict,
                   owner: str, status: str, workspace_id: str = "default") -> None:
        self.primary.create_run(run_id, pipeline_id, pipeline_json, owner, status,
                                workspace_id=workspace_id)
        repo_path = self._repo_for_workspace(workspace_id)
        self._fan("on_run_created", run_id, pipeline_id, pipeline_json, owner,
                  status, workspace_id, repo_path)

    def update_run_status(self, run_id: str, status: str) -> None:
        self.primary.update_run_status(run_id, status)
        self._fan("on_run_status", run_id, status)
        if status in ("succeeded", "rejected"):
            self._auto_report(run_id)

    def save_run_state(self, run_id: str, state: dict[str, str]) -> None:
        self.primary.save_run_state(run_id, state)
        self._fan("on_run_state", run_id, state)

    def append_event(self, ev: Event) -> int:
        seq = self.primary.append_event(ev)
        self._fan("on_event", ev)
        return seq

    def save_audit(self, rec: AuditRecord) -> None:
        self.primary.save_audit(rec)
        self._fan("on_audit", rec)

    def close(self) -> None:
        for s in self.sinks:
            try:
                s.close()
            except Exception as e:  # noqa: BLE001
                log.warning("sink close failed: %s", e)
        self.primary.close()

    def _auto_report(self, run_id: str) -> None:
        """On terminal status, render + commit a report via any git sink.

        Reads the full audit from the primary (the source of truth), so it works
        for fresh runs and for resumes that complete in a separate process.
        """
        reporters = [s for s in self.sinks if hasattr(s, "write_report")]
        if not reporters:
            return
        try:
            run = self.primary.get_run(run_id)
            if not run:
                return
            repo = self._repo_for_workspace(run.get("workspace_id", "default"))
            if not repo:
                return
            from .report import render_run_report
            payload = {"run": run, "pipeline": json.loads(run["pipeline"]),
                       "audit": self.primary.audit_records(run_id),
                       "cost": self.primary.run_cost(run_id)}
            md = render_run_report(payload)
            for s in reporters:
                s.write_report(repo, run_id, md)
        except Exception as e:  # noqa: BLE001 — reporting must never break a run
            log.warning("auto-report failed for %s: %s", run_id, e)

    # ---- helpers ---------------------------------------------------------- #
    def _repo_for_workspace(self, workspace_id: str) -> str | None:
        try:
            ws = self.primary.get_workspace(workspace_id)
            return ws.get("repo_path") if ws else None
        except Exception:  # noqa: BLE001
            return None


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def _build_primary(db_path: str, primary: str) -> RunStore:
    if primary == "postgres":
        dsn = os.environ.get("MOIRA_PG_DSN")
        if not dsn:
            raise RuntimeError("MOIRA_PRIMARY=postgres requires MOIRA_PG_DSN to be set")
        from .pg_store import PostgresRunStore  # lazy: psycopg only loaded for this path
        return PostgresRunStore(dsn)
    from .store import Store
    return Store(db_path)


def make_run_store(db_path: str = ".moira/moira.sqlite", *,
                   repo_path: str | None = None,
                   primary: str | None = None,
                   git_export: bool | None = None) -> RunStore:
    """Build the configured run store.

    Config precedence: explicit kwargs > env > defaults.
      MOIRA_PRIMARY    sqlite (default) | postgres
      MOIRA_DB         sqlite path (the `db_path` arg mirrors this)
      MOIRA_PG_DSN     postgres DSN (when primary=postgres)
      MOIRA_GIT_EXPORT 0/1 — enable the git audit mirror
      MOIRA_GIT_REPO   git target (fallback when a run has no workspace repo)

    Returns a bare primary when no sinks are configured (zero overhead), else a
    `CompositeStore`. Both satisfy `RunStore`, so callers don't branch.
    """
    primary = primary or os.environ.get("MOIRA_PRIMARY", "sqlite")
    if git_export is None:
        git_export = os.environ.get("MOIRA_GIT_EXPORT", "0") not in ("", "0", "false", "False")

    store = _build_primary(db_path, primary)
    sinks: list[ExportSink] = []
    if git_export:
        from .git_sink import GitExportSink
        default_repo = repo_path or os.environ.get("MOIRA_GIT_REPO")

        def _resolver(run_id: str, _store: RunStore = store, _fallback=default_repo) -> str | None:
            run = _store.get_run(run_id)
            if run:
                ws = _store.get_workspace(run.get("workspace_id", "default"))
                if ws and ws.get("repo_path"):
                    return ws["repo_path"]
            return _fallback

        sinks.append(GitExportSink(repo_resolver=_resolver, default_repo=default_repo))

    if not sinks:
        return store
    return CompositeStore(store, sinks)
