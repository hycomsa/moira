"""The orchestration engine — Moira's core IP layer.

A dependency-free DAG engine:
- nodes form a DAG via `depends_on` (a pipeline with no edges runs linearly by order)
- ready nodes (all deps done) run; independent workers run IN PARALLEL (thread pool)
- gate nodes evaluate verifier/auto-check results (auto/hybrid/human/off)
- AUTO_CHECK nodes run a real command (pytest/lint/SAST) -> pass/fail as findings
- retry-N-then-gate on backend failure; human gates PAUSE the run (waiting_gate)
- reject at a gate resets the target + its downstream subtree, then re-drives
- every step writes an append-only event + a full audit record

Parallelism note: backend/command execution runs in worker threads, but ALL store
writes happen on the main thread (SQLite connection is single-threaded) — workers
return results, the main thread persists them deterministically by node order.

This supersedes LangGraph for now (ADR-002): it delivers arbitrary DAG + parallel
+ interrupts/resume with zero dependencies and preserves the tested gate/audit model.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from .backends.base import BackendRegistry
from .gates import evaluate_gate
from .models import (
    AuditRecord, BackendResult, Cost, Event, Finding, GateConfig, GateDecision,
    Node, NodeType, Pipeline, Severity, Status, new_id,
)
from . import gitdiff
from .persistence import RunStore
from .store import Store  # noqa: F401 — re-exported for back-compat

log = logging.getLogger("moira.engine")

MAX_PARALLEL = 8
# Retry pacing (QW3/ADR-011): linear backoff between backend attempts —
# sleep(min(MAX, BASE * failures_so_far)); never before the first attempt.
RETRY_BACKOFF_BASE = float(os.environ.get("MOIRA_RETRY_BACKOFF", "2"))
RETRY_BACKOFF_MAX = 30.0
PENDING, RUNNING, DONE, WAITING, REJECTED, FAILED = (
    "pending", "running", "succeeded", "waiting_gate", "rejected", "failed")


class RunResult:
    def __init__(self, run_id: str, status: Status, waiting_node: Optional[str] = None):
        self.run_id = run_id
        self.status = status
        self.waiting_node = waiting_node

    def __repr__(self) -> str:
        w = f", waiting={self.waiting_node}" if self.waiting_node else ""
        return f"RunResult({self.run_id}, {self.status.value}{w})"


class Engine:
    def __init__(self, store: RunStore, registry: BackendRegistry, owner: str = "unknown"):
        self.store = store
        self.registry = registry
        self.owner = owner

    # ---- public API -------------------------------------------------------- #
    def create(self, pipeline: Pipeline, context: dict[str, Any],
               workspace_id: str = "default") -> str:
        """Seed a run (row + RUNNING + initial state + run.start event) WITHOUT driving.
        Fast + synchronous — lets a caller return the run_id immediately and drive the
        slow agent work off-thread via `drive_existing` (see moira_api.background())."""
        run_id = new_id("run-")
        self.store.create_run(run_id, pipeline.id, pipeline.to_dict(),
                              self.owner, Status.RUNNING.value, workspace_id=workspace_id)
        self._event(run_id, "run.start", f"Run started: pipeline '{pipeline.name}' by {self.owner}")
        self.store.save_run_state(run_id, {n.id: PENDING for n in pipeline.nodes})
        return run_id

    def drive_existing(self, run_id: str, pipeline: Pipeline, context: dict[str, Any],
                       should_cancel: Optional[Callable[[], bool]] = None) -> RunResult:
        """Drive a previously-`create`d run from its persisted state (the slow part).

        `should_cancel`, if given, is polled between node batches (and after each
        batch executes) so a cancellation request is honored mid-drive (B1)."""
        state = self.store.get_run_state(run_id) or {n.id: PENDING for n in pipeline.nodes}
        return self._drive(run_id, pipeline, context, state, should_cancel)

    def start(self, pipeline: Pipeline, context: dict[str, Any],
              workspace_id: str = "default",
              should_cancel: Optional[Callable[[], bool]] = None) -> RunResult:
        """Synchronous create + drive (CLI / tests / synchronous callers)."""
        run_id = self.create(pipeline, context, workspace_id)
        return self.drive_existing(run_id, pipeline, context, should_cancel)

    def resume(self, run_id: str, pipeline: Pipeline, context: dict[str, Any],
               decision: GateDecision,
               should_cancel: Optional[Callable[[], bool]] = None) -> RunResult:
        run = self.store.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        state = self.store.get_run_state(run_id) or {n.id: PENDING for n in pipeline.nodes}
        waiting = next((nid for nid, s in state.items() if s == WAITING), None)
        if waiting is None:
            # Idempotent: a duplicate/late decision (e.g. a double-clicked Approve) arriving
            # after the gate was already decided must be a no-op — never a crash, which would
            # mark an already-running run as failed. Reflect the run's current status instead.
            self._event(run_id, "gate.decision",
                        f"ignored duplicate {decision.decision}: run no longer waiting at a gate")
            try:
                return RunResult(run_id, Status(run.get("status") or RUNNING))
            except ValueError:
                return RunResult(run_id, Status.RUNNING)
        node = pipeline.by_id(waiting)
        self._event(run_id, "gate.decision",
                    f"[{node.name}] {decision.decision} by {decision.by}: {decision.confirmed}", node.id)
        self._finalize_gate(run_id, node, decision)
        deps = pipeline.dep_map()

        if decision.decision == "reject":
            target = node.on_reject_goto
            if target is None:
                state[waiting] = REJECTED
                self.store.save_run_state(run_id, state)
                self.store.update_run_status(run_id, Status.REJECTED.value)
                self._event(run_id, "run.end", "Run rejected at gate (no rework target)", node.id)
                return RunResult(run_id, Status.REJECTED)
            self._event(run_id, "gate.reject",
                        f"Returning to '{target}' (+downstream) with feedback: {decision.feedback}", node.id)
            for r in {target} | pipeline.descendants(target, deps):
                state[r] = PENDING
            state[waiting] = PENDING  # the gate re-evaluates after rework
            context.setdefault("feedback", {})[target] = decision.feedback
        else:  # approve
            state[waiting] = DONE
        self.store.save_run_state(run_id, state)
        return self._drive(run_id, pipeline, context, state, should_cancel)

    def _cancel(self, run_id: str) -> RunResult:
        self.store.update_run_status(run_id, Status.CANCELLED.value)
        self._event(run_id, "run.cancel", "Run cancelled mid-drive")
        log.info("run.cancel run=%s", run_id)
        return RunResult(run_id, Status.CANCELLED)

    # ---- core DAG loop ----------------------------------------------------- #
    def _drive(self, run_id: str, pipeline: Pipeline, context: dict[str, Any],
               state: dict[str, str],
               should_cancel: Optional[Callable[[], bool]] = None) -> RunResult:
        cancelled = should_cancel or (lambda: False)
        deps = pipeline.dep_map()
        upstream = context.setdefault("upstream", {})
        vr = context.setdefault("verifier_results", {})
        # rebuild produced outputs from prior drives (cross-resume continuity)
        for rec in self.store.audit_records(run_id):
            if rec.get("status") == DONE and rec.get("output") and rec["node_id"] in state:
                upstream.setdefault(rec["node_id"], rec["output"])
                # restore discovery auto-chain across resume (audit is in rowid order,
                # so the last authored artifact wins → the next skill inherits it)
                if rec["output"].get("artifact"):
                    context["produced_artifact"] = rec["output"]["artifact"]

        while True:
            if cancelled():
                return self._cancel(run_id)
            ready = [n for n in pipeline.nodes
                     if state[n.id] == PENDING and all(state.get(d) == DONE for d in deps[n.id])]
            if not ready:
                break
            workers = [n for n in ready if n.type != NodeType.GATE]
            gates = [n for n in ready if n.type == NodeType.GATE]

            if workers:
                # mark workers RUNNING + emit node.start BEFORE executing, so the cockpit
                # shows which node is in progress during a long (e.g. claude) step instead
                # of a frozen-looking "running" run with no events.
                for n in workers:
                    state[n.id] = RUNNING
                    self._event(run_id, "node.start",
                                f"[{n.name}] running via {n.backend if n.type != NodeType.AUTO_CHECK else 'auto-check'}", n.id)
                self.store.save_run_state(run_id, state)
                results = self._exec_parallel(workers, context)
                for n in workers:  # persist deterministically by ready order
                    ex = results[n.id]
                    self._persist_exec(run_id, n, ex, context)
                    if ex["result"] is None:           # exhausted retries -> human
                        state[n.id] = WAITING
                        self.store.save_run_state(run_id, state)
                        self.store.update_run_status(run_id, Status.WAITING_GATE.value)
                        self._event(run_id, "node.escalate",
                                    f"[{n.name}] failed after retries — escalated to human", n.id)
                        log.warning("node.escalate run=%s node=%s backend=%s — exhausted retries",
                                    run_id, n.id, n.backend)
                        return RunResult(run_id, Status.WAITING_GATE, waiting_node=n.id)
                    res = ex["result"]
                    upstream[n.id] = res.output
                    # discovery chaining: hand the authored artifact id to the next skill
                    if (res.output or {}).get("artifact"):
                        context["produced_artifact"] = res.output["artifact"]
                    if n.type in (NodeType.VERIFIER, NodeType.AUTO_CHECK):
                        vr[n.id] = res
                    state[n.id] = DONE
                self.store.save_run_state(run_id, state)
                # completed nodes are persisted above; if cancellation was requested
                # (possibly killing a node mid-flight), stop now instead of scheduling more.
                if cancelled():
                    return self._cancel(run_id)
                continue

            # only gates ready
            progressed = False
            for g in gates:
                decision = self._run_gate(run_id, pipeline, g, context)
                if decision.decision == "escalate":
                    state[g.id] = WAITING
                    self.store.save_run_state(run_id, state)
                    self.store.update_run_status(run_id, Status.WAITING_GATE.value)
                    self._event(run_id, "gate.wait", f"[{g.name}] waiting for {(g.gate or GateConfig()).persona}", g.id)
                    return RunResult(run_id, Status.WAITING_GATE, waiting_node=g.id)
                if decision.decision == "reject":
                    target = g.on_reject_goto
                    if target is None:
                        state[g.id] = REJECTED
                        self.store.save_run_state(run_id, state)
                        self.store.update_run_status(run_id, Status.REJECTED.value)
                        self._event(run_id, "run.end", "Run rejected at gate", g.id)
                        return RunResult(run_id, Status.REJECTED)
                    for r in {target} | pipeline.descendants(target, deps):
                        state[r] = PENDING
                    context.setdefault("feedback", {})[target] = decision.feedback
                    self.store.save_run_state(run_id, state)
                    progressed = True
                    break  # restart the ready scan after a reset
                state[g.id] = DONE  # approve
                progressed = True
            self.store.save_run_state(run_id, state)
            if progressed:
                continue
            break

        if all(state[n.id] == DONE for n in pipeline.nodes):
            self.store.update_run_status(run_id, Status.SUCCEEDED.value)
            self._event(run_id, "run.end", "Run completed successfully")
            return RunResult(run_id, Status.SUCCEEDED)
        # not all done and nothing ready -> still waiting on a human gate
        waiting = next((nid for nid, s in state.items() if s == WAITING), None)
        self.store.update_run_status(run_id, Status.WAITING_GATE.value)
        return RunResult(run_id, Status.WAITING_GATE, waiting_node=waiting)

    # ---- node execution (parallel, no store access) ------------------------ #
    def _exec_parallel(self, nodes: list[Node], context: dict[str, Any]) -> dict[str, dict]:
        if len(nodes) == 1:
            return {nodes[0].id: self._exec_node(nodes[0], context)}
        out: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(nodes))) as pool:
            futs = {pool.submit(self._exec_node, n, context): n for n in nodes}
            for fut, n in futs.items():
                out[n.id] = fut.result()
        return out

    def _exec_node(self, node: Node, context: dict[str, Any]) -> dict:
        """Run a node's work with retry. Returns {result|None, errors, start, end, attempts}.
        No store access (thread-safe)."""
        start = time.time()
        if node.type == NodeType.AUTO_CHECK:
            res = self._run_check(node, context)
            return {"result": res, "errors": [], "start": start, "end": time.time(), "attempts": 1}
        backend = self.registry.get(node.backend)
        # capture what files a coding node changes in the dev repo (side-effect-free)
        cwd = context.get("cwd")
        capture = node.type == NodeType.PRODUCER and gitdiff.is_git_repo(cwd)
        before = gitdiff.tree_snapshot(cwd) if capture else None
        errors: list[str] = []
        attempts = node.max_retries + 1
        for attempt in range(1, attempts + 1):
            ctx = context
            if errors:
                # QW3/ADR-011: informed retry — the next attempt sees what just
                # failed (per-attempt shallow copy: the shared context dict is
                # read concurrently by parallel workers and is never mutated),
                # paced by a linear backoff so transient failures get air.
                time.sleep(min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE * len(errors)))
                ctx = {**context, "attempt_errors": list(errors)}
            result = backend.run(node, ctx)
            if result.ok:
                if capture:
                    changes = gitdiff.changes_in(cwd, before, gitdiff.tree_snapshot(cwd))
                    if changes:
                        result.output = {**(result.output or {}), **changes}
                        aid = gitdiff.artifact_id_from_changes(changes)
                        if aid:  # discovery: the artifact this skill authored
                            result.output["artifact"] = aid
                return {"result": result, "errors": errors, "start": start,
                        "end": time.time(), "attempts": attempt}
            errors.append(result.error)
        return {"result": None, "errors": errors, "start": start, "end": time.time(), "attempts": attempts}

    def _run_check(self, node: Node, context: dict[str, Any]) -> BackendResult:
        """AUTO_CHECK: built-in deterministic check (check_kind) or a shell command."""
        if node.check_kind == "ac_coverage":
            return self._run_ac_coverage_check(node, context)
        if node.check_kind == "test_exec":
            return self._run_test_exec_check(node, context)
        if node.check_kind == "log_hygiene":
            return self._run_log_hygiene_check(node, context)
        return self._run_shell_check(node, context)

    @staticmethod
    def _detect_test_cmd(cwd: str | None) -> str:
        """Best-effort test runner for a code repo: npm test (node) / pytest (python)."""
        import os
        if not cwd:
            return ""
        pj = os.path.join(cwd, "package.json")
        if os.path.exists(pj):
            try:
                import json as _j
                if "test" in ((_j.load(open(pj, encoding="utf-8")).get("scripts")) or {}):
                    return "npm test --silent"
            except Exception:  # noqa: BLE001
                pass
        if any(os.path.exists(os.path.join(cwd, m)) for m in ("pytest.ini", "pyproject.toml", "setup.cfg")) \
                or os.path.isdir(os.path.join(cwd, "tests")):
            return "pytest -q"
        return ""

    @staticmethod
    def _parse_test_counts(out: str) -> str:
        """Pull a human summary from jest/pytest output, e.g. 'tests: 10 passed, 2 failed'."""
        import re
        m = re.search(r"Tests:\s*([0-9].*?)(?:\n|$)", out)              # jest: "Tests: 2 failed, 10 passed, 12 total"
        if m:
            return "tests: " + m.group(1).strip()
        m = re.search(r"(\d+ passed(?:,\s*\d+ failed)?(?:,\s*\d+ skipped)?)", out)  # pytest
        if m:
            return "tests: " + m.group(1)
        m = re.search(r"(\d+ failed)", out)
        return "tests: " + m.group(1) if m else ""

    def _run_test_exec_check(self, node: Node, context: dict[str, Any]) -> BackendResult:
        """Run the project's test suite in cwd → 'tests actually green' (vs a test-plan that merely
        exists). Exit 0 = INFO (pass); non-zero = HIGH (fail, escalates the downstream gate)."""
        cwd = context.get("cwd")
        cmd = node.check_cmd or self._detect_test_cmd(cwd)
        if not cmd:
            f = Finding(id=node.id, confidence=1.0, severity=Severity.INFO,
                        title="no test runner detected", detail=f"no npm test / pytest in {cwd or '.'}")
            return BackendResult(output={"check": "test_exec", "passed": True, "summary": "no test runner", "cmd": None},
                                 tools_used=["test_exec"], findings=[f], cost=Cost(), ok=True)
        try:
            proc = subprocess.run(shlex.split(cmd), cwd=cwd, capture_output=True, text=True, timeout=600)
            ok = proc.returncode == 0
            out = (proc.stdout or "") + (proc.stderr or "")
            summary = self._parse_test_counts(out) or ("tests passed" if ok else "tests FAILED")
            tail = out[-800:]
        except Exception as e:  # noqa: BLE001
            ok, summary, tail = False, f"test runner error: {e}", str(e)
        sev = Severity.INFO if ok else Severity.HIGH
        finding = Finding(id=node.id, confidence=1.0, severity=sev,
                          title=("tests passed" if ok else "tests FAILED"), detail=f"{summary}\n{tail}")
        return BackendResult(output={"check": "test_exec", "passed": ok, "summary": summary, "cmd": cmd},
                             tools_used=[f"test_exec:{shlex.split(cmd)[0]}"],
                             decisions=[f"ran `{cmd}` -> {summary}"], findings=[finding], cost=Cost(), ok=True)

    def _run_ac_coverage_check(self, node: Node, context: dict[str, Any]) -> BackendResult:
        """Deterministic gate: every acceptance criterion of the FUNC must be covered by a task.

        Reads the just-authored backlog from cwd and compares against the func-spec's ACs (the same
        `tasks.completeness` the traceability badge uses). 100% -> INFO (pass); < 100% -> HIGH (fail),
        which makes the downstream AUTO gate escalate to a human."""
        from .repo_reader import AISdlcRepo
        from . import tasks
        func_id = node.spec_ref or context.get("func_id", "")
        cwd = context.get("cwd")
        try:
            comp = tasks.completeness(AISdlcRepo(cwd), func_id) if cwd else None
            total = comp["ac"]["total"] if comp else 0
            covered = comp["ac"]["in_tasks"] if comp else 0
            ntasks = comp["tasks"]["total"] if comp else 0
            ok = total > 0 and covered >= total
            detail = (f"AC coverage {covered}/{total} across {ntasks} tasks for {func_id}"
                      if total else f"no acceptance criteria found for {func_id}")
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"coverage check error: {e}"
        sev = Severity.INFO if ok else Severity.HIGH
        finding = Finding(id=node.id, confidence=1.0, severity=sev,
                          title=("AC coverage complete" if ok else "AC coverage incomplete"),
                          detail=detail)
        return BackendResult(output={"check": "ac_coverage", "passed": ok, "detail": detail},
                             tools_used=["ac_coverage"], decisions=[detail],
                             findings=[finding], cost=Cost(), ok=True)

    def _run_shell_check(self, node: Node, context: dict[str, Any]) -> BackendResult:
        """AUTO_CHECK: run a real command; exit 0 = pass (INFO), non-zero = fail (HIGH).

        If the command's executable is not on PATH, the check is **not applicable**
        (status=not_applicable, passed, INFO) rather than a failure — a governance
        pack can declare e.g. an `axe`/`gitleaks` check that's simply skipped where
        the tool isn't installed (must-fix #5: "executed or explicitly marked N/A").
        """
        cmd = node.check_cmd or "true"
        cwd = context.get("cwd")
        exe = shlex.split(cmd)[0] if cmd.strip() else "true"
        if exe != "true" and shutil.which(exe) is None:
            finding = Finding(id=node.id, title=f"{exe} not available — check skipped",
                              severity=Severity.INFO, confidence=1.0,
                              detail=f"executable '{exe}' not found on PATH; check marked not applicable")
            return BackendResult(
                output={"check": "shell", "cmd": cmd, "status": "not_applicable",
                        "passed": True, "reason": f"{exe} not installed"},
                tools_used=[f"shell:{exe}"], decisions=[f"{exe} not installed -> not applicable"],
                findings=[finding], cost=Cost(), ok=True)
        try:
            proc = subprocess.run(shlex.split(cmd), cwd=cwd, capture_output=True,
                                  text=True, timeout=300)
            ok = proc.returncode == 0
            tail = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
        except Exception as e:  # noqa: BLE001
            ok, tail = False, f"command error: {e}"
        sev = Severity.INFO if ok else Severity.HIGH
        finding = Finding(id=node.id, title=("check passed" if ok else "check FAILED"),
                          severity=sev, confidence=1.0, detail=tail)
        return BackendResult(
            # "check" marks this result as deterministic — gates.findings_feedback
            # lists such findings before LLM verifier findings on reject (QW1)
            output={"check": "shell", "cmd": cmd, "passed": ok, "output_tail": tail},
            tools_used=[f"shell:{shlex.split(cmd)[0] if cmd.strip() else 'true'}"],
            decisions=[f"ran `{cmd}` in {cwd or '.'} -> {'pass' if ok else 'FAIL'}"],
            findings=[finding], cost=Cost(), ok=True,
        )

    # log-hygiene scan patterns (deterministic, zero external deps)
    _LOG_CALL_RE = re.compile(
        r"(?:\blog(?:ger)?\s*\.\s*(?:debug|info|warning|warn|error|critical|exception|trace)"
        r"|console\s*\.\s*(?:log|info|warn|error|debug)"
        r"|System\.out\.print(?:ln)?|\bprintln!)\s*\(", re.I)
    _RAW_PRINT_RE = re.compile(
        r"(?:(?:^|[^.\w])print\s*\(|console\s*\.\s*log\s*\(|System\.out\.print)", re.I)
    _SENSITIVE_RE = re.compile(
        r"\b(?:pass(?:word|wd)?|pwd|secret|tokens?|api[_-]?keys?|apikey|authorization|bearer"
        r"|private[_-]?key|credit[_-]?card|card[_-]?number|cvv|ssn|pesel)\b", re.I)
    _EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    _PESEL_RE = re.compile(r"\b\d{11}\b")
    _CARD_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b")
    _CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rb", ".rs",
                 ".php", ".cs", ".kt", ".scala", ".c", ".cpp", ".h"}
    _SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "target",
                  "__pycache__", ".moira-runs", ".idea", ".vscode"}
    _SEV_RANK = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
                 Severity.HIGH: 3, Severity.CRITICAL: 4}

    def _run_log_hygiene_check(self, node: Node, context: dict[str, Any]) -> BackendResult:
        """Deterministic log-hygiene scan (no external tooling):

        - sensitive data (password/token/secret/api-key + PII: email/PESEL/card)
          interpolated into a log/print statement  -> CRITICAL (blocks the gate)
        - raw print()/console.log()/System.out in non-test application code -> MEDIUM

        Scans source files under `cwd` (skips vendor/build/test dirs). One Finding
        per issue (capped) so a CRITICAL leak makes `has_blocking()` true.
        """
        cwd = context.get("cwd")
        issues: list[tuple[Severity, str, int, str]] = []
        files_scanned = 0
        cap = 200
        if cwd and os.path.isdir(cwd):
            for root, dirs, files in os.walk(cwd):
                dirs[:] = [d for d in dirs if d not in self._SKIP_DIRS]
                parts = set(Path(root).parts)
                dir_is_test = bool(parts & {"tests", "test", "__tests__", "spec", "specs"})
                for fn in files:
                    if Path(fn).suffix.lower() not in self._CODE_EXT:
                        continue
                    is_test = dir_is_test or fn.startswith("test_") or fn.endswith(
                        ("_test.py", "_test.go", ".test.ts", ".spec.ts", ".test.js", ".spec.js"))
                    fp = Path(root) / fn
                    try:
                        text = fp.read_text(encoding="utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        continue
                    files_scanned += 1
                    rel = os.path.relpath(str(fp), cwd)
                    for i, line in enumerate(text.splitlines(), 1):
                        if len(issues) >= cap:
                            break
                        if self._LOG_CALL_RE.search(line) and (
                                self._SENSITIVE_RE.search(line) or self._EMAIL_RE.search(line)
                                or self._PESEL_RE.search(line) or self._CARD_RE.search(line)):
                            issues.append((Severity.CRITICAL, rel, i, "sensitive data in log statement"))
                        elif not is_test and self._RAW_PRINT_RE.search(line):
                            issues.append((Severity.MEDIUM, rel, i,
                                           "raw print/console.log in application code"))
        passed = not issues
        max_sev = max((s for s, _, _, _ in issues), key=lambda s: self._SEV_RANK[s],
                      default=Severity.INFO)
        findings = [Finding(id=f"{node.id}:{i}", severity=sev, confidence=1.0,
                            title=msg, detail=f"{rel}:{ln} — {msg}")
                    for i, (sev, rel, ln, msg) in enumerate(issues)] or [
            Finding(id=node.id, severity=Severity.INFO, confidence=1.0,
                    title="log hygiene clean", detail=f"scanned {files_scanned} file(s); no issues")]
        by_sev: dict[str, int] = {}
        for s, _, _, _ in issues:
            by_sev[s.value] = by_sev.get(s.value, 0) + 1
        return BackendResult(
            output={"check": "log_hygiene", "passed": passed, "files_scanned": files_scanned,
                    "issues_count": len(issues), "by_severity": by_sev,
                    "issues": [f"{rel}:{ln} [{sev.value}] {msg}" for sev, rel, ln, msg in issues[:50]]},
            tools_used=["log_hygiene"],
            decisions=[f"scanned {files_scanned} file(s) -> {len(issues)} issue(s), max={max_sev.value}"],
            findings=findings, cost=Cost(), ok=True)

    def _persist_exec(self, run_id: str, node: Node, ex: dict, context: dict[str, Any]) -> None:
        # node.start is emitted by the drive loop BEFORE execution (live progress)
        for err in ex["errors"]:
            self._event(run_id, "retry", f"[{node.name}] failed: {err}", node.id)
            log.warning("node.retry run=%s node=%s backend=%s err=%s",
                        run_id, node.id, node.backend, err)
        res = ex["result"]
        # audit fidelity (QW3/ADR-011): retries inject error context into the
        # prompt, so the sealed input must carry it too — plus the attempt count.
        rec_input: dict[str, Any] = {
            "spec_ref": node.spec_ref, "role": node.role,
            "backend": node.backend, "model": node.model or "(default)",
            "feedback": context.get("feedback", {}).get(node.id, ""),
            "attempts": ex["attempts"],
        }
        if ex["errors"]:
            rec_input["attempt_errors"] = [e[:500] for e in ex["errors"]]
        rec = AuditRecord(
            step_id=new_id("step-"), run_id=run_id, node_id=node.id, node_name=node.name,
            owner=self.owner,
            input=rec_input,
            output=res.output if res else {},
            tools=res.tools_used if res else [],
            decisions=res.decisions if res else [],
            cost=(res.cost.to_dict() if res else Cost().to_dict()),
            time_start=ex["start"], time_end=ex["end"],
            lineage=context.get("lineage", []),
            status=DONE if res else FAILED,
        )
        self.store.save_audit(rec)
        cost_usd = rec.cost.get("usd", 0.0) if isinstance(rec.cost, dict) else 0.0
        log.info("node.end run=%s node=%s type=%s status=%s dur=%.2fs cost_usd=%.4f",
                 run_id, node.id, node.type.value, rec.status, rec.duration, cost_usd)
        if res:
            self._event(run_id, "node.end",
                        f"[{node.name}] ok ({rec.cost.get('usd', 0):.3f} USD, {rec.duration:.2f}s)"
                        if isinstance(rec.cost, dict) else f"[{node.name}] ok", node.id)

    # ---- gate execution ---------------------------------------------------- #
    def _run_gate(self, run_id: str, pipeline: Pipeline, node: Node,
                  context: dict[str, Any]) -> GateDecision:
        cfg: GateConfig = node.gate or GateConfig()
        vr = context.get("verifier_results", {})
        consumed = [vr[c] for c in cfg.consumes if c in vr] or list(vr.values())
        # QW2/ADR-010: the rework-loop counter is DERIVED from the audit trail
        # (every system reject was sealed there by this method), so the cap
        # survives resume, sidecar restart and worker handoff — no extra state.
        system_rejects = sum(
            1 for rec in self.store.audit_records(run_id)
            if rec.get("node_id") == node.id
            for ap in (rec.get("approvals") or [])
            if ap.get("decision") == "reject" and ap.get("by") == "system")
        decision = evaluate_gate(cfg, consumed, system_rejects=system_rejects)
        upstream = context.get("upstream", {})
        review = {nid: upstream.get(nid) for nid in cfg.reviews if nid in upstream}
        rec = AuditRecord(
            step_id=new_id("step-"), run_id=run_id, node_id=node.id,
            node_name=node.name, owner=cfg.persona or "system",
            input={"mode": cfg.mode.value, "consumes": cfg.consumes,
                   "persona": cfg.persona, "audience": cfg.audience, "review": review,
                   "high_cutoff": cfg.high_cutoff, "low_cutoff": cfg.low_cutoff},
            output={"decision": decision.decision},
            approvals=[decision.to_dict()],
            time_start=time.time(), time_end=time.time(),
            status=(Status.WAITING_GATE.value if decision.decision == "escalate" else Status.SUCCEEDED.value),
        )
        self.store.save_audit(rec)
        self._event(run_id, "gate.eval", f"[{node.name}] {decision.decision}: {decision.confirmed}", node.id)
        log.info("gate.eval run=%s node=%s mode=%s decision=%s", run_id, node.id,
                 cfg.mode.value, decision.decision)
        return decision

    def _finalize_gate(self, run_id: str, node: Node, decision: GateDecision) -> None:
        rec = AuditRecord(
            step_id=new_id("step-"), run_id=run_id, node_id=node.id, node_name=node.name,
            owner=decision.by, input={"mode": (node.gate or GateConfig()).mode.value},
            output={"decision": decision.decision}, approvals=[decision.to_dict()],
            time_start=time.time(), time_end=time.time(), status=Status.SUCCEEDED.value,
        )
        self.store.save_audit(rec)

    # ---- helpers ----------------------------------------------------------- #
    def _event(self, run_id: str, kind: str, message: str, node_id: str = "") -> None:
        self.store.append_event(Event(run_id=run_id, kind=kind, message=message, node_id=node_id))
