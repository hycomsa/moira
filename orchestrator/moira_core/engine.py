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

import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .backends.base import BackendRegistry
from .gates import evaluate_gate
from .models import (
    AuditRecord, BackendResult, Cost, Event, Finding, GateConfig, GateDecision,
    Node, NodeType, Pipeline, Severity, Status, new_id,
)
from . import gitdiff
from .persistence import RunStore
from .store import Store  # noqa: F401 — re-exported for back-compat

MAX_PARALLEL = 8
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

    def drive_existing(self, run_id: str, pipeline: Pipeline,
                       context: dict[str, Any]) -> RunResult:
        """Drive a previously-`create`d run from its persisted state (the slow part)."""
        state = self.store.get_run_state(run_id) or {n.id: PENDING for n in pipeline.nodes}
        return self._drive(run_id, pipeline, context, state)

    def start(self, pipeline: Pipeline, context: dict[str, Any],
              workspace_id: str = "default") -> RunResult:
        """Synchronous create + drive (CLI / tests / synchronous callers)."""
        run_id = self.create(pipeline, context, workspace_id)
        return self.drive_existing(run_id, pipeline, context)

    def resume(self, run_id: str, pipeline: Pipeline, context: dict[str, Any],
               decision: GateDecision) -> RunResult:
        run = self.store.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        state = self.store.get_run_state(run_id) or {n.id: PENDING for n in pipeline.nodes}
        waiting = next((nid for nid, s in state.items() if s == WAITING), None)
        if waiting is None:
            raise RuntimeError("run is not waiting at a gate")
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
        return self._drive(run_id, pipeline, context, state)

    # ---- core DAG loop ----------------------------------------------------- #
    def _drive(self, run_id: str, pipeline: Pipeline, context: dict[str, Any],
               state: dict[str, str]) -> RunResult:
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
            ready = [n for n in pipeline.nodes
                     if state[n.id] == PENDING and all(state.get(d) == DONE for d in deps[n.id])]
            if not ready:
                break
            workers = [n for n in ready if n.type != NodeType.GATE]
            gates = [n for n in ready if n.type == NodeType.GATE]

            if workers:
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
            result = backend.run(node, context)
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
        """AUTO_CHECK: run a real command; exit 0 = pass (INFO), non-zero = fail (HIGH)."""
        cmd = node.check_cmd or "true"
        cwd = context.get("cwd")
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
            output={"cmd": cmd, "passed": ok, "output_tail": tail},
            tools_used=[f"shell:{shlex.split(cmd)[0] if cmd.strip() else 'true'}"],
            decisions=[f"ran `{cmd}` in {cwd or '.'} -> {'pass' if ok else 'FAIL'}"],
            findings=[finding], cost=Cost(), ok=True,
        )

    def _persist_exec(self, run_id: str, node: Node, ex: dict, context: dict[str, Any]) -> None:
        attempts = node.max_retries + 1 if node.type != NodeType.AUTO_CHECK else 1
        self._event(run_id, "node.start",
                    f"[{node.name}] start via {node.backend if node.type != NodeType.AUTO_CHECK else 'auto-check'}", node.id)
        for err in ex["errors"]:
            self._event(run_id, "retry", f"[{node.name}] failed: {err}", node.id)
        res = ex["result"]
        rec = AuditRecord(
            step_id=new_id("step-"), run_id=run_id, node_id=node.id, node_name=node.name,
            owner=self.owner,
            input={"spec_ref": node.spec_ref, "role": node.role,
                   "backend": node.backend, "model": node.model or "(default)",
                   "feedback": context.get("feedback", {}).get(node.id, "")},
            output=res.output if res else {},
            tools=res.tools_used if res else [],
            decisions=res.decisions if res else [],
            cost=(res.cost.to_dict() if res else Cost().to_dict()),
            time_start=ex["start"], time_end=ex["end"],
            lineage=context.get("lineage", []),
            status=DONE if res else FAILED,
        )
        self.store.save_audit(rec)
        if res:
            self._event(run_id, "node.end",
                        f"[{node.name}] ok ({rec.cost.get('usd', 0):.3f} USD, {rec.duration:.2f}s)"
                        if isinstance(rec.cost, dict) else f"[{node.name}] ok", node.id)
        _ = attempts

    # ---- gate execution ---------------------------------------------------- #
    def _run_gate(self, run_id: str, pipeline: Pipeline, node: Node,
                  context: dict[str, Any]) -> GateDecision:
        cfg: GateConfig = node.gate or GateConfig()
        vr = context.get("verifier_results", {})
        consumed = [vr[c] for c in cfg.consumes if c in vr] or list(vr.values())
        decision = evaluate_gate(cfg, consumed)
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
