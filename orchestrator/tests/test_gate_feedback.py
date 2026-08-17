"""QW1 — system-generated rework feedback at auto/hybrid gate reject.

Before this change a system reject (auto gate with escalate_on_blocking=False,
or a hybrid auto-deny) returned an EMPTY GateDecision.feedback, so the rework
loop re-ran the producer blind. These tests pin the new contract:

- reject decisions carry a serialized digest of the findings that caused them
- deterministic check findings (test_exec/ac_coverage/...) come before LLM ones
- INFO findings (passes) are skipped; the list is capped; long details truncated
- approve/escalate decisions and human-provided feedback are untouched
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import BackendRegistry, Engine, Status, Store  # noqa: E402
from moira_core.gates import evaluate_gate  # noqa: E402
from moira_core.models import (  # noqa: E402
    BackendResult, Cost, Finding, GateConfig, GateMode, Node, NodeType,
    Pipeline, Severity,
)


def _llm_result(*findings: Finding) -> BackendResult:
    return BackendResult(output={"verdict": "fail"}, findings=list(findings))


def _check_result(kind: str, *findings: Finding) -> BackendResult:
    return BackendResult(output={"check": kind, "passed": False}, findings=list(findings))


class TestRejectFeedbackUnit(unittest.TestCase):
    def test_auto_reject_carries_findings_feedback(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False)
        vr = [_llm_result(Finding(id="F1", title="tests FAILED", severity=Severity.HIGH,
                                  confidence=1.0, detail="2 failed, 10 passed"))]
        d = evaluate_gate(cfg, vr)
        self.assertEqual(d.decision, "reject")
        self.assertIn("tests FAILED", d.feedback)
        self.assertIn("high", d.feedback)
        self.assertIn("2 failed, 10 passed", d.feedback)

    def test_hybrid_autodeny_carries_findings_feedback(self):
        cfg = GateConfig(mode=GateMode.HYBRID, escalate_on_blocking=False,
                         high_cutoff=0.85, low_cutoff=0.50)
        vr = [_llm_result(Finding(id="F1", title="unverified session expiry",
                                  severity=Severity.MEDIUM, confidence=0.30))]
        d = evaluate_gate(cfg, vr)
        self.assertEqual(d.decision, "reject")
        self.assertIn("unverified session expiry", d.feedback)

    def test_feedback_lists_deterministic_checks_first(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False)
        vr = [
            _llm_result(Finding(id="L1", title="insecure token handling",
                                severity=Severity.HIGH, confidence=0.9)),
            _check_result("test_exec", Finding(id="C1", title="tests FAILED",
                                               severity=Severity.HIGH, confidence=1.0)),
        ]
        d = evaluate_gate(cfg, vr)
        self.assertEqual(d.decision, "reject")
        self.assertLess(d.feedback.index("tests FAILED"),
                        d.feedback.index("insecure token handling"))

    def test_feedback_skips_info_findings(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False)
        vr = [_llm_result(
            Finding(id="OK", title="naming conventions clean", severity=Severity.INFO),
            Finding(id="F1", title="missing input validation", severity=Severity.HIGH),
        )]
        d = evaluate_gate(cfg, vr)
        self.assertEqual(d.decision, "reject")
        self.assertIn("missing input validation", d.feedback)
        self.assertNotIn("naming conventions clean", d.feedback)

    def test_feedback_caps_findings_and_reports_remainder(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False)
        findings = [Finding(id=f"F{i}", title=f"defect number {i}",
                            severity=Severity.HIGH) for i in range(8)]
        d = evaluate_gate(cfg, [_llm_result(*findings)])
        self.assertEqual(d.decision, "reject")
        listed = [ln for ln in d.feedback.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(listed), 5)
        self.assertIn("3 more", d.feedback)

    def test_feedback_truncates_long_detail(self):
        cfg = GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False)
        vr = [_llm_result(Finding(id="F1", title="huge trace", severity=Severity.HIGH,
                                  detail="x" * 5000))]
        d = evaluate_gate(cfg, vr)
        self.assertEqual(d.decision, "reject")
        self.assertLess(len(d.feedback), 1200)
        self.assertIn("…", d.feedback)

    def test_approve_and_escalate_have_no_generated_feedback(self):
        ok = evaluate_gate(GateConfig(mode=GateMode.AUTO),
                           [_llm_result(Finding(id="OK", title="clean",
                                                severity=Severity.INFO))])
        self.assertEqual(ok.decision, "approve")
        self.assertEqual(ok.feedback, "")
        esc = evaluate_gate(GateConfig(mode=GateMode.HUMAN, persona="lead-dev"), [])
        self.assertEqual(esc.decision, "escalate")
        self.assertEqual(esc.feedback, "")


class _StubQualityBackend:
    """Verifier fails (HIGH) on the first pass, is clean afterwards; the producer
    records the rework feedback it was given. Lets one drive() traverse the
    reject -> rework -> approve loop deterministically."""
    name = "mock"

    def __init__(self):
        self.verifier_calls = 0
        self.producer_feedback: list[str] = []

    def run(self, node: Node, context) -> BackendResult:
        if node.id == "verify":
            self.verifier_calls += 1
            if self.verifier_calls == 1:
                return BackendResult(
                    output={"verdict": "fail"},
                    findings=[Finding(id="Q1", title="token.verify lacks expiry check",
                                      severity=Severity.HIGH, confidence=1.0,
                                      detail="see standards/security.md")],
                    cost=Cost())
            return BackendResult(
                output={"verdict": "pass"},
                findings=[Finding(id="Q1", title="clean", severity=Severity.INFO)],
                cost=Cost())
        self.producer_feedback.append(context.get("feedback", {}).get(node.id, ""))
        return BackendResult(output={"work": f"attempt {len(self.producer_feedback)}"},
                             cost=Cost())


class TestRejectFeedbackIntegration(unittest.TestCase):
    def test_auto_reject_rework_delivers_generated_feedback(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        stub = _StubQualityBackend()
        reg = BackendRegistry()
        reg.register(stub)
        engine = Engine(Store(tmp.name), reg, owner="tester")
        pipe = Pipeline(id="p", name="rework loop", nodes=[
            Node(id="implement", name="Implement", type=NodeType.PRODUCER),
            Node(id="verify", name="Verify", type=NodeType.VERIFIER),
            Node(id="gate", name="Gate", type=NodeType.GATE,
                 gate=GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False,
                                 consumes=["verify"]),
                 on_reject_goto="implement"),
        ])
        res = engine.start(pipe, {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED, msg=str(res))
        # first attempt ran without feedback, the rework attempt got the findings
        self.assertEqual(len(stub.producer_feedback), 2)
        self.assertEqual(stub.producer_feedback[0], "")
        self.assertIn("token.verify lacks expiry check", stub.producer_feedback[1])


if __name__ == "__main__":
    unittest.main()
