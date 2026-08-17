"""QW3 — backend retry with error context and backoff.

Before this change `Engine._exec_node` retried a failed backend call
immediately and blind: the next attempt's prompt carried no trace of what just
failed, so a deterministic failure (e.g. a contract-parse error) burned every
retry the same way. These tests pin the new contract:

- the retry attempt sees the previous errors via `context["attempt_errors"]`
  (a per-attempt shallow copy — the shared context dict is never mutated)
- `contract.attempt_errors_block()` renders them as a capped, truncated
  `=== PREVIOUS ATTEMPT FAILED ===` prompt section (most recent last)
- retries are paced by a linear backoff (env `MOIRA_RETRY_BACKOFF`)
- the audit record's input carries `attempts` and the truncated errors, so the
  sealed audit still fully describes what the model was shown
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import BackendRegistry, Engine, Status, Store  # noqa: E402
from moira_core.backends import contract  # noqa: E402
from moira_core.models import (  # noqa: E402
    BackendResult, Cost, Node, NodeType, Pipeline,
)


class TestAttemptErrorsBlock(unittest.TestCase):
    def test_empty_errors_render_nothing(self):
        self.assertEqual(contract.attempt_errors_block([]), "")
        self.assertEqual(contract.attempt_errors_block(None), "")

    def test_block_lists_numbered_attempts(self):
        block = contract.attempt_errors_block(["timeout after 300s", "contract parse error"])
        self.assertIn("PREVIOUS ATTEMPT FAILED", block)
        self.assertIn("attempt 1: timeout after 300s", block)
        self.assertIn("attempt 2: contract parse error", block)

    def test_long_error_truncated(self):
        block = contract.attempt_errors_block(["x" * 5000])
        self.assertLess(len(block), 1000)
        self.assertIn("…", block)

    def test_only_last_three_errors_shown(self):
        block = contract.attempt_errors_block([f"err {i}" for i in range(1, 6)])
        self.assertNotIn("err 1", block)
        self.assertNotIn("err 2", block)
        self.assertIn("err 3", block)
        self.assertIn("err 5", block)
        self.assertIn("2 earlier", block)


class _FlakyBackend:
    """Fails `fail_times` times, then succeeds; records the attempt_errors each
    call saw — proving the retry is informed and the shared context untouched."""
    name = "mock"

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0
        self.seen_errors: list = []

    def run(self, node: Node, context) -> BackendResult:
        self.calls += 1
        self.seen_errors.append(context.get("attempt_errors"))
        if self.calls <= self.fail_times:
            return BackendResult(ok=False, error=f"boom #{self.calls}", cost=Cost())
        return BackendResult(output={"work": "done"}, cost=Cost())


def _engine(backend):
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    reg = BackendRegistry()
    reg.register(backend)
    return Engine(Store(tmp.name), reg, owner="tester"), Store(tmp.name)


def _one_node_pipeline() -> Pipeline:
    return Pipeline(id="p", name="retry", nodes=[
        Node(id="work", name="Work", type=NodeType.PRODUCER, max_retries=2)])


class TestInformedRetryIntegration(unittest.TestCase):
    def test_retry_sees_previous_errors_and_shared_context_stays_clean(self):
        stub = _FlakyBackend(fail_times=2)
        engine, _ = _engine(stub)
        ctx = {"spec_text": "x", "lineage": []}
        with mock.patch.object(sys.modules["moira_core.engine"], "RETRY_BACKOFF_BASE", 0.0):
            res = engine.start(_one_node_pipeline(), ctx)
        self.assertEqual(res.status, Status.SUCCEEDED, msg=str(res))
        self.assertEqual(stub.calls, 3)
        self.assertIsNone(stub.seen_errors[0])            # first attempt: clean
        self.assertEqual(stub.seen_errors[1], ["boom #1"])
        self.assertEqual(stub.seen_errors[2], ["boom #1", "boom #2"])
        self.assertNotIn("attempt_errors", ctx)           # shared context untouched

    def test_audit_input_records_attempts_and_errors(self):
        stub = _FlakyBackend(fail_times=1)
        engine, store = _engine(stub)
        with mock.patch.object(sys.modules["moira_core.engine"], "RETRY_BACKOFF_BASE", 0.0):
            res = engine.start(_one_node_pipeline(), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)
        rec = [r for r in store.audit_records(res.run_id) if r["node_id"] == "work"][0]
        self.assertEqual(rec["input"]["attempts"], 2)
        self.assertEqual(rec["input"]["attempt_errors"], ["boom #1"])

    def test_first_attempt_audit_has_no_error_noise(self):
        stub = _FlakyBackend(fail_times=0)
        engine, store = _engine(stub)
        res = engine.start(_one_node_pipeline(), {"spec_text": "x", "lineage": []})
        rec = [r for r in store.audit_records(res.run_id) if r["node_id"] == "work"][0]
        self.assertEqual(rec["input"]["attempts"], 1)
        self.assertNotIn("attempt_errors", rec["input"])

    def test_linear_backoff_paces_retries(self):
        stub = _FlakyBackend(fail_times=2)
        engine, _ = _engine(stub)
        eng_mod = sys.modules["moira_core.engine"]
        with mock.patch.object(eng_mod, "RETRY_BACKOFF_BASE", 2.0), \
             mock.patch.object(eng_mod.time, "sleep") as slept:
            res = engine.start(_one_node_pipeline(), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)
        waits = [c.args[0] for c in slept.call_args_list]
        self.assertEqual(waits, [2.0, 4.0])  # base*1, base*2 — never before attempt 1


class TestPromptWiring(unittest.TestCase):
    def test_claude_code_stage_prompt_contains_error_section(self):
        from moira_core.backends.claude_code import ClaudeCodeBackend
        be = ClaudeCodeBackend()
        node = Node(id="implement", name="Implement", type=NodeType.PRODUCER,
                    role="code-generator")
        prompt = be._build_prompt(node, {"spec_text": "spec",
                                         "attempt_errors": ["timeout after 300s"]})
        self.assertIn("PREVIOUS ATTEMPT FAILED", prompt)
        self.assertIn("timeout after 300s", prompt)

    def test_claude_code_skill_prompt_contains_error_section(self):
        from moira_core.backends.claude_code import ClaudeCodeBackend
        be = ClaudeCodeBackend()
        node = Node(id="author", name="Author", type=NodeType.PRODUCER,
                    role="ba-skill", skill="ba@shape-func-spec", skill_input="REQ-X-01")
        prompt = be._build_prompt(node, {"attempt_errors": ["contract parse error"]})
        self.assertIn("PREVIOUS ATTEMPT FAILED", prompt)
        self.assertIn("contract parse error", prompt)

    def test_prompt_without_errors_has_no_section(self):
        from moira_core.backends.claude_code import ClaudeCodeBackend
        be = ClaudeCodeBackend()
        node = Node(id="implement", name="Implement", type=NodeType.PRODUCER,
                    role="code-generator")
        prompt = be._build_prompt(node, {"spec_text": "spec"})
        self.assertNotIn("PREVIOUS ATTEMPT FAILED", prompt)


if __name__ == "__main__":
    unittest.main()
