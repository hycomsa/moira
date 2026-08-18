"""ST1 — closed test-fix loop: failing check output reaches the rework prompt.

QW1 gave the rework loop a findings DIGEST; this change (opt-in per gate,
`GateConfig.rework_check_output`) adds the RAW EVIDENCE: the tail of the
failing auto_check's output (the actual failing tests), so the producer can
make them pass instead of guessing. These tests pin:

- checks persist their full output tail (`output.check_output`, capped) into
  the audit record — the trail carries what the model will be shown
- on reject (system OR human) with the flag on, the engine derives the failing
  consumed checks' output FROM THE AUDIT TRAIL (works across resume/restart)
  and delivers it to the rework target via `context["check_output"]`
- the flag is opt-in: default gates behave exactly as before
- backends render it as a distinct `=== FAILING CHECK OUTPUT ===` section
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import BackendRegistry, Engine, Status, Store  # noqa: E402
from moira_core.backends import contract  # noqa: E402
from moira_core.models import (  # noqa: E402
    BackendResult, Cost, GateConfig, GateDecision, GateMode, Node, NodeType,
    Pipeline,
)


class TestCheckOutputBlock(unittest.TestCase):
    def test_empty_renders_nothing(self):
        self.assertEqual(contract.check_output_block(""), "")
        self.assertEqual(contract.check_output_block(None), "")

    def test_block_has_marker_and_content(self):
        block = contract.check_output_block("FAIL test_login — expected 200 got 500")
        self.assertIn("FAILING CHECK OUTPUT", block)
        self.assertIn("expected 200 got 500", block)

    def test_truncation_keeps_the_tail(self):
        text = ("earlier noise\n" * 3000) + "THE ACTUAL FAILURE AT THE END"
        block = contract.check_output_block(text)
        self.assertLessEqual(len(block), contract.CHECK_OUTPUT_CAP + 200)
        self.assertIn("THE ACTUAL FAILURE AT THE END", block)


class _RecordingProducer:
    """Producer that records the check_output and feedback each call saw."""
    name = "mock"

    def __init__(self):
        self.check_outputs: list = []
        self.feedbacks: list = []

    def run(self, node: Node, context) -> BackendResult:
        if node.type == NodeType.PRODUCER:
            self.check_outputs.append(context.get("check_output", {}).get(node.id))
            self.feedbacks.append(context.get("feedback", {}).get(node.id, ""))
        return BackendResult(output={"work": "attempt"}, cost=Cost())


def _pipeline(gate_mode: GateMode, rework_check_output: bool) -> Pipeline:
    return Pipeline(id="p", name="test-fix loop", nodes=[
        Node(id="implement", name="Implement", type=NodeType.PRODUCER),
        Node(id="tests", name="Tests", type=NodeType.AUTO_CHECK,
             check_cmd="bash -c 'echo BROKEN_MARKER_42; exit 1'",
             depends_on=["implement"]),
        Node(id="gate", name="Gate", type=NodeType.GATE,
             gate=GateConfig(mode=gate_mode, escalate_on_blocking=False,
                             consumes=["tests"], persona="lead-dev",
                             max_loop=1, rework_check_output=rework_check_output),
             on_reject_goto="implement"),
    ])


def _engine(backend):
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    reg = BackendRegistry()
    reg.register(backend)
    store = Store(tmp.name)
    return Engine(store, reg, owner="tester"), store


class TestReworkGetsCheckOutput(unittest.TestCase):
    def test_system_reject_delivers_failing_check_output(self):
        stub = _RecordingProducer()
        engine, store = _engine(stub)
        res = engine.start(_pipeline(GateMode.AUTO, True), {"spec_text": "x", "lineage": []})
        # max_loop=1: one system reject (rework) then forced escalation
        self.assertEqual(res.status, Status.WAITING_GATE, msg=str(res))
        self.assertEqual(len(stub.check_outputs), 2)
        self.assertIsNone(stub.check_outputs[0])           # first attempt: clean
        self.assertIn("BROKEN_MARKER_42", stub.check_outputs[1])

    def test_flag_defaults_off_no_raw_output(self):
        stub = _RecordingProducer()
        engine, _ = _engine(stub)
        res = engine.start(_pipeline(GateMode.AUTO, False), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(len(stub.check_outputs), 2)
        self.assertIsNone(stub.check_outputs[1])           # digest only (QW1), no raw evidence

    def test_human_reject_after_resume_also_delivers(self):
        stub = _RecordingProducer()
        engine, store = _engine(stub)
        pipe = _pipeline(GateMode.HUMAN, True)
        ctx = {"spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "gate")
        # fresh context — simulates the API rebuilding it across the process boundary
        res2 = engine.resume(res.run_id, pipe, {"spec_text": "x", "lineage": []},
                             GateDecision(decision="reject", by="lead-dev",
                                          feedback="make the suite green"))
        self.assertEqual(res2.status, Status.WAITING_GATE, msg=str(res2))
        self.assertEqual(len(stub.check_outputs), 2)
        self.assertIn("BROKEN_MARKER_42", stub.check_outputs[1])
        self.assertIn("make the suite green", stub.feedbacks[1])

    def test_check_output_lands_in_rework_audit_input(self):
        stub = _RecordingProducer()
        engine, store = _engine(stub)
        res = engine.start(_pipeline(GateMode.AUTO, True), {"spec_text": "x", "lineage": []})
        recs = [r for r in store.audit_records(res.run_id) if r["node_id"] == "implement"]
        self.assertEqual(len(recs), 2)
        self.assertNotIn("check_output", recs[0]["input"])
        self.assertIn("BROKEN_MARKER_42", recs[1]["input"]["check_output"])

    def test_checks_persist_their_output_tail(self):
        stub = _RecordingProducer()
        engine, store = _engine(stub)
        res = engine.start(_pipeline(GateMode.AUTO, False), {"spec_text": "x", "lineage": []})
        check_rec = next(r for r in store.audit_records(res.run_id)
                         if r["node_id"] == "tests")
        self.assertIn("BROKEN_MARKER_42", check_rec["output"].get("check_output", ""))


class TestPromptWiring(unittest.TestCase):
    def test_claude_code_prompt_contains_check_output_section(self):
        from moira_core.backends.claude_code import ClaudeCodeBackend
        be = ClaudeCodeBackend()
        node = Node(id="implement", name="Implement", type=NodeType.PRODUCER,
                    role="code-generator")
        prompt = be._build_prompt(node, {
            "spec_text": "spec",
            "check_output": {"implement": "FAIL test_login expected 200 got 500"}})
        self.assertIn("FAILING CHECK OUTPUT", prompt)
        self.assertIn("expected 200 got 500", prompt)

    def test_prompt_without_check_output_has_no_section(self):
        from moira_core.backends.claude_code import ClaudeCodeBackend
        be = ClaudeCodeBackend()
        node = Node(id="implement", name="Implement", type=NodeType.PRODUCER,
                    role="code-generator")
        self.assertNotIn("FAILING CHECK OUTPUT",
                         be._build_prompt(node, {"spec_text": "spec"}))


if __name__ == "__main__":
    unittest.main()
