"""Git-native audit mirror — a write-only `ExportSink`.

Mirrors every run into the workspace's AI SDLC repo under `.moira-runs/<run_id>/`
and commits at status transitions, so the defensible audit trail lives in git
next to the specs/agents it was produced from (single source of truth).

Design (ADR-005):
- write-through: every hook writes/append to the working tree immediately (cheap)
- commit only at transitions: run created, run-state change, run status change
  (~8-12 commits/run). Events/audit written in between are swept into the next
  transition commit, so a skipped write self-heals.
- never touch the user's work: `git add` is scoped to `.moira-runs/<run_id>`,
  commit uses `--only <pathspec>`; no `add -A`, no branch switching.
- concurrency: an in-process lock per repo serializes commits; git's own
  index.lock is the cross-process backstop. Hard subprocess timeout so a hung
  git never wedges the caller.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from . import yamlio
from .models import AuditRecord, Event
from .persistence import ExportSink

log = logging.getLogger("moira.git_sink")

RUNS_DIR = ".moira-runs"
GIT_TIMEOUT = 15  # seconds — a commit of a few small files is sub-second

# One lock per real repo path, guarding concurrent commits within this process.
_repo_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(repo: str) -> threading.Lock:
    key = str(Path(repo).resolve())
    with _locks_guard:
        lock = _repo_locks.get(key)
        if lock is None:
            lock = _repo_locks[key] = threading.Lock()
        return lock


class GitExportSink(ExportSink):
    def __init__(self, repo_resolver: Callable[[str], Optional[str]] | None = None,
                 default_repo: str | None = None):
        self._resolver = repo_resolver
        self._default_repo = default_repo
        self._repo_cache: dict[str, str] = {}  # run_id -> repo_path

    # ---- repo resolution -------------------------------------------------- #
    def _repo(self, run_id: str, hint: str | None = None) -> str | None:
        if run_id in self._repo_cache:
            return self._repo_cache[run_id]
        repo = hint
        if not repo and self._resolver:
            try:
                repo = self._resolver(run_id)
            except Exception as e:  # noqa: BLE001
                log.warning("repo resolver failed for %s: %s", run_id, e)
        repo = repo or self._default_repo
        if repo:
            self._repo_cache[run_id] = repo
        return repo

    def _run_dir(self, repo: str, run_id: str) -> Path:
        d = Path(repo) / RUNS_DIR / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- hooks ------------------------------------------------------------ #
    def on_run_created(self, run_id, pipeline_id, pipeline_json, owner, status,
                       workspace_id, repo_path) -> None:
        repo = self._repo(run_id, hint=repo_path)
        if not repo:
            return
        self._ensure_git(repo)
        d = self._run_dir(repo, run_id)
        (d / "pipeline.json").write_text(json.dumps(pipeline_json, indent=2), "utf-8")
        (d / "run.yaml").write_text(yamlio.dump({
            "run_id": run_id, "pipeline_id": pipeline_id, "owner": owner,
            "status": status, "workspace_id": workspace_id,
        }), "utf-8")
        self._commit(repo, run_id, f"moira run {_short(run_id)}: start {pipeline_id}")

    def on_run_state(self, run_id, state) -> None:
        repo = self._repo(run_id)
        if not repo:
            return
        d = self._run_dir(repo, run_id)
        (d / "state.yaml").write_text(yamlio.dump(dict(state)), "utf-8")
        done = sum(1 for s in state.values() if s == "succeeded")
        waiting = [n for n, s in state.items() if s == "waiting_gate"]
        msg = f"moira run {_short(run_id)}: {done}/{len(state)} done"
        if waiting:
            msg += f" — waiting@{','.join(waiting)}"
        self._commit(repo, run_id, msg)

    def on_run_status(self, run_id, status) -> None:
        repo = self._repo(run_id)
        if not repo:
            return
        d = self._run_dir(repo, run_id)
        run_yaml = d / "run.yaml"
        data = yamlio.load(run_yaml.read_text("utf-8")) if run_yaml.exists() else {}
        data["status"] = status
        run_yaml.write_text(yamlio.dump(data), "utf-8")
        self._commit(repo, run_id, f"moira run {_short(run_id)}: {status}")

    def on_event(self, ev: Event) -> None:
        repo = self._repo(ev.run_id)
        if not repo:
            return
        d = self._run_dir(repo, ev.run_id)
        with (d / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")
        # no commit — swept into the next transition commit

    def on_audit(self, rec: AuditRecord) -> None:
        repo = self._repo(rec.run_id)
        if not repo:
            return
        d = self._run_dir(repo, rec.run_id)
        adir = d / "audit"
        adir.mkdir(exist_ok=True)
        # filename by step_id (mirrors the primary's overwrite-by-step semantics)
        (adir / f"{rec.step_id}.json").write_text(
            json.dumps(rec.to_dict(), ensure_ascii=False, indent=2), "utf-8")
        # no commit — swept into the next transition commit

    def write_report(self, repo: str, run_id: str, markdown: str) -> str:
        """Write + commit a run report into .moira-runs/<run_id>/report.md.

        Returns the path relative to the repo. Reuses the scoped commit, so it
        never touches the user's working changes.
        """
        self._ensure_git(repo)
        d = self._run_dir(repo, run_id)
        (d / "report.md").write_text(markdown, "utf-8")
        self._commit(repo, run_id, f"moira run {_short(run_id)}: report")
        return f"{RUNS_DIR}/{run_id}/report.md"

    # ---- git -------------------------------------------------------------- #
    def _ensure_git(self, repo: str) -> None:
        if not (Path(repo) / ".git").exists():
            self._git(repo, ["init", "-q"])

    def _commit(self, repo: str, run_id: str, message: str) -> None:
        pathspec = f"{RUNS_DIR}/{run_id}"
        with _lock_for(repo):
            self._git(repo, ["add", "--", pathspec])
            res = self._git(repo, [
                "-c", "user.name=Moira", "-c", "user.email=moira@hycom.local",
                "commit", "-q", "--no-gpg-sign", "--only", "-m", message,
                "--", pathspec,
            ], check=False)
            if res is not None and res.returncode != 0:
                out = (res.stdout or "") + (res.stderr or "")
                if "nothing to commit" not in out and "no changes added" not in out:
                    log.warning("git commit failed in %s: %s", repo, out.strip()[:300])

    def _git(self, repo: str, args: list[str], check: bool = True):
        try:
            res = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                                 text=True, timeout=GIT_TIMEOUT)
            if check and res.returncode != 0:
                log.warning("git %s failed: %s", args[0], (res.stderr or "").strip()[:300])
            return res
        except Exception as e:  # noqa: BLE001 — git issues degrade the mirror, never the run
            log.warning("git %s errored in %s: %s", args[0] if args else "?", repo, e)
            return None


def _short(run_id: str) -> str:
    return run_id.replace("run-", "")[:10]
