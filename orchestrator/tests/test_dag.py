"""DAG engine: parallel branches, AUTO_CHECK nodes, depends_on ordering."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, Engine, GateConfig, GateMode, Node, NodeType, Pipeline,
    Status, Store,
)
from moira_core.backends.base import AgentBackend  # noqa: E402
from moira_core.backends.mock import MockBackend  # noqa: E402
from moira_core.models import BackendResult, Cost  # noqa: E402


def engine(backend=None):
    store = Store(tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name)
    reg = BackendRegistry(); reg.register(backend or MockBackend())
    return Engine(store, reg, owner="t"), store


class SlowBackend:
    """Records overlap to prove parallel execution."""
    name = "mock"
    def __init__(self):
        self.active = 0; self.max_active = 0; self.lock_n = 0
    def run(self, node: Node, context) -> BackendResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(0.15)
        self.active -= 1
        return BackendResult(output={"n": node.id}, ok=True, cost=Cost())


class TestParallel(unittest.TestCase):
    def test_independent_nodes_run_concurrently(self):
        be = SlowBackend()
        eng, store = engine(be)
        # root -> {a, b, c} all depend on root, then join gate
        nodes = [
            Node(id="root", name="root", type=NodeType.PRODUCER, backend="mock"),
            Node(id="a", name="a", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
            Node(id="b", name="b", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
            Node(id="c", name="c", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
        ]
        res = eng.start(Pipeline(id="p", name="p", nodes=nodes), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)
        self.assertGreaterEqual(be.max_active, 2, "a,b,c should have run in parallel")

    def test_dag_join_waits_for_all(self):
        eng, store = engine()
        nodes = [
            Node(id="root", name="root", type=NodeType.PRODUCER, backend="mock"),
            Node(id="a", name="a", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
            Node(id="b", name="b", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
            Node(id="gate", name="gate", type=NodeType.GATE, depends_on=["a", "b"],
                 gate=GateConfig(mode=GateMode.AUTO, consumes=["a", "b"])),
        ]
        res = eng.start(Pipeline(id="p", name="p", nodes=nodes), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)
        recs = {r["node_id"] for r in store.audit_records(res.run_id)}
        self.assertTrue({"root", "a", "b", "gate"}.issubset(recs))


class TestAutoCheck(unittest.TestCase):
    def _pipe(self, cmd):
        return Pipeline(id="ac", name="ac", nodes=[
            Node(id="impl", name="impl", type=NodeType.PRODUCER, backend="mock"),
            Node(id="check", name="check", type=NodeType.AUTO_CHECK, check_cmd=cmd, depends_on=["impl"]),
            Node(id="gate", name="gate", type=NodeType.GATE, depends_on=["check"],
                 gate=GateConfig(mode=GateMode.AUTO, consumes=["check"])),
        ])

    def test_passing_check_auto_approves(self):
        eng, store = engine()
        res = eng.start(self._pipe('python3 -c "print(1)"'), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED, msg=str(res))

    def test_failing_check_escalates_gate(self):
        eng, store = engine()
        res = eng.start(self._pipe('python3 -c "import sys; sys.exit(1)"'),
                        {"spec_text": "x", "lineage": []})
        # auto gate escalates on the HIGH finding from a failed check
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "gate")

    def test_check_records_real_result(self):
        eng, store = engine()
        res = eng.start(self._pipe('python3 -c "print(1)"'), {"spec_text": "x", "lineage": []})
        check = next(r for r in store.audit_records(res.run_id) if r["node_id"] == "check")
        self.assertTrue(check["output"]["passed"])
        self.assertTrue(any(t.startswith("shell:") for t in check["tools"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
