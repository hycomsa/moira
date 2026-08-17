"""QW2 — bounded rework loop: `GateConfig.max_loop` caps SYSTEM rejects.

Before this change an auto gate with escalate_on_blocking=False (or a hybrid
gate auto-denying) could reject -> rework -> reject forever: the only bound was
the worker's lifetime (the lease heartbeat renews mid-drive, so the durable
runner never breaks the loop). These tests pin the new contract:

- a gate may issue at most `max_loop` (default 3) SYSTEM rejects; the next
  would-be system reject is converted into an escalation to a human
- exhaustion NEVER auto-approves and never fails the run — it pauses it
- human rejects do not consume the budget (a human in the loop is governance
  working as intended, not a runaway loop) and remain possible after exhaustion
- the counter derives from the audit trail, so it survives resume/restart
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import BackendRegistry, Engine, Status, Store  # noqa: E402
from moira_core.gates import evaluate_gate  # noqa: E402
from moira_core.models import (  # noqa: E402
    BackendResult, Cost, Finding, GateConfig, GateDecision, GateMode, Node,
    NodeType, Pipeline, Severity,
)


def _blocking_vr():
    return [BackendResult(output={"verdict": "fail"},
                          findings=[Finding(id="F1", title="tests FAILED",
                                            severity=Severity.HIGH, confidence=1.0)])]


def _lowconf_vr():
    return [BackendResult(output={"verdict": "fail"},
                          findings=[Finding(id="F1", title="weak evidence",
                                            severity=Severity.MEDIUM, confidence=0.2)])]


class TestLoopCapUnit(unittest.TestCase):
    def test_auto_reject_below_cap_still_rejects_with_feedback(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False)
        d = evaluate_gate(cfg, _blocking_vr(), system_rejects=2)
        self.assertEqual(d.decision, "reject")
        self.assertIn("tests FAILED", d.feedback)  # QW1 intact

    def test_auto_reject_at_cap_escalates(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False,
                         persona="lead-dev")
        d = evaluate_gate(cfg, _blocking_vr(), system_rejects=3)
        self.assertEqual(d.decision, "escalate")
        self.assertEqual(d.by, "lead-dev")
        self.assertIn("rework budget exhausted", d.confirmed)
        self.assertIn("3", d.confirmed)

    def test_hybrid_autodeny_at_cap_escalates(self):
        cfg = GateConfig(mode=GateMode.HYBRID, escalate_on_blocking=False,
                         low_cutoff=0.5, persona="architect")
        self.assertEqual(evaluate_gate(cfg, _lowconf_vr(), system_rejects=2).decision,
                         "reject")
        d = evaluate_gate(cfg, _lowconf_vr(), system_rejects=3)
        self.assertEqual(d.decision, "escalate")
        self.assertIn("rework budget exhausted", d.confirmed)

    def test_max_loop_zero_escalates_immediately(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False, max_loop=0)
        d = evaluate_gate(cfg, _blocking_vr(), system_rejects=0)
        self.assertEqual(d.decision, "escalate")

    def test_custom_max_loop_respected(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False, max_loop=5)
        self.assertEqual(evaluate_gate(cfg, _blocking_vr(), system_rejects=4).decision,
                         "reject")
        self.assertEqual(evaluate_gate(cfg, _blocking_vr(), system_rejects=5).decision,
                         "escalate")

    def test_from_dict_without_max_loop_defaults_to_3(self):
        cfg = GateConfig.from_dict({"mode": "auto", "persona": "none",
                                    "consumes": [], "reviews": [],
                                    "audience": "technical", "high_cutoff": 0.85,
                                    "low_cutoff": 0.5, "escalate_on_blocking": True})
        self.assertEqual(cfg.max_loop, 3)

    def test_approve_paths_ignore_the_counter(self):
        clean = [BackendResult(output={"verdict": "pass"},
                               findings=[Finding(id="OK", title="clean",
                                                 severity=Severity.INFO)])]
        d = evaluate_gate(GateConfig(mode=GateMode.AUTO), clean, system_rejects=99)
        self.assertEqual(d.decision, "approve")


class _AlwaysFailingVerifier:
    """Producer succeeds; verifier ALWAYS returns a blocking finding.
    Pre-QW2 this pipeline (auto gate, escalation off, on_reject_goto) looped
    forever inside one drive."""
    name = "mock"

    def __init__(self):
        self.producer_runs = 0

    def run(self, node: Node, context) -> BackendResult:
        if node.id == "verify":
            return BackendResult(
                output={"verdict": "fail"},
                findings=[Finding(id="Q1", title="still broken",
                                  severity=Severity.HIGH, confidence=1.0)],
                cost=Cost())
        self.producer_runs += 1
        return BackendResult(output={"work": self.producer_runs}, cost=Cost())


def _loop_pipeline(max_loop: int) -> Pipeline:
    return Pipeline(id="p", name="capped loop", nodes=[
        Node(id="implement", name="Implement", type=NodeType.PRODUCER),
        Node(id="verify", name="Verify", type=NodeType.VERIFIER),
        Node(id="gate", name="Gate", type=NodeType.GATE,
             gate=GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False,
                             consumes=["verify"], persona="lead-dev",
                             max_loop=max_loop),
             on_reject_goto="implement"),
    ])


def _fresh(backend):
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    reg = BackendRegistry()
    reg.register(backend)
    return Engine(Store(tmp.name), reg, owner="tester"), Store(tmp.name)


class TestLoopCapIntegration(unittest.TestCase):
    def test_always_failing_loop_terminates_at_cap_and_escalates(self):
        stub = _AlwaysFailingVerifier()
        engine, store = _fresh(stub)
        res = engine.start(_loop_pipeline(max_loop=2), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.WAITING_GATE, msg=str(res))
        self.assertEqual(res.waiting_node, "gate")
        # initial attempt + exactly max_loop reworks
        self.assertEqual(stub.producer_runs, 3)
        # the audit trail carries exactly max_loop system rejects for the gate
        rejects = [ap for rec in store.audit_records(res.run_id)
                   if rec["node_id"] == "gate"
                   for ap in rec.get("approvals", [])
                   if ap.get("decision") == "reject" and ap.get("by") == "system"]
        self.assertEqual(len(rejects), 2)

    def test_human_can_still_reject_and_then_approve_after_exhaustion(self):
        stub = _AlwaysFailingVerifier()
        engine, store = _fresh(stub)
        pipe = _loop_pipeline(max_loop=1)
        ctx = {"spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.status, Status.WAITING_GATE)
        runs_at_pause = stub.producer_runs

        # a HUMAN reject after exhaustion still drives one more rework (human
        # control is unlimited), then the gate escalates again — no system loop
        res2 = engine.resume(res.run_id, pipe, ctx,
                             GateDecision(decision="reject", by="lead-dev",
                                          feedback="try harder"))
        self.assertEqual(res2.status, Status.WAITING_GATE, msg=str(res2))
        self.assertEqual(stub.producer_runs, runs_at_pause + 1)

        res3 = engine.resume(res.run_id, pipe, ctx,
                             GateDecision(decision="approve", by="lead-dev",
                                          confirmed="accepting residual risk"))
        self.assertEqual(res3.status, Status.SUCCEEDED, msg=str(res3))


if __name__ == "__main__":
    unittest.main()
