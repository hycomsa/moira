"""Mid-drive cancellation (B1): engine cooperative cancel + runner subprocess kill."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, DurableRunner, Engine, MockBackend, Node, NodeType, Pipeline,
    Status, Store, new_id,
)
from moira_core.models import BackendResult, Cost  # noqa: E402


def _reg():
    r = BackendRegistry(); r.register(MockBackend()); return r


def _store():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); f.close()
    return Store(f.name)


class TestEngineCooperativeCancel(unittest.TestCase):
    def test_should_cancel_stops_between_nodes(self):
        store = _store()
        pipe = Pipeline(id="p", name="P", nodes=[
            Node(id="a", name="a", type=NodeType.PRODUCER, role="x", backend="mock"),
            Node(id="b", name="b", type=NodeType.PRODUCER, role="x", backend="mock", depends_on=["a"]),
            Node(id="c", name="c", type=NodeType.PRODUCER, role="x", backend="mock", depends_on=["b"]),
        ])
        ticks = {"n": 0}

        def should_cancel():
            ticks["n"] += 1
            return ticks["n"] > 1  # allow the first batch, cancel before the second

        res = Engine(store, _reg(), owner="t").start(pipe, {}, should_cancel=should_cancel)
        self.assertEqual(res.status, Status.CANCELLED)
        # only node 'a' ran; b/c never executed
        done = [a["node_id"] for a in store.audit_records(res.run_id)]
        self.assertIn("a", done)
        self.assertNotIn("c", done)
        self.assertEqual(store.get_run(res.run_id)["status"], "cancelled")
        store.close()


class _CancellableBackend:
    """Blocks in run() until released OR cancel() is called; records that it was cancelled."""
    name = "cancellable"

    def __init__(self):
        self.entered = threading.Event()
        self._cancel = threading.Event()
        self.cancelled = False

    def run(self, node, context):
        self.entered.set()
        self._cancel.wait(timeout=10)
        return BackendResult(output={"done": True}, tools_used=[], cost=Cost(), ok=True)

    def cancel(self):
        self.cancelled = True
        self._cancel.set()


class TestRunnerSubprocessKill(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); f.close()
        self.path = f.name

    def test_cancel_during_long_node_kills_backend_and_cancels_run(self):
        backend = _CancellableBackend()

        def reg():
            r = BackendRegistry(); r.register(backend); return r

        pipe = Pipeline(id="p", name="P", nodes=[
            Node(id="work", name="work", type=NodeType.PRODUCER, role="x", backend="cancellable")])
        seed = Store(self.path)
        run_id = Engine(seed, reg(), owner="t").create(pipe, {})
        seed.enqueue_job({"job_id": "j", "run_id": run_id, "kind": "drive_run",
                          "payload": {"context": {}}})
        seed.close()

        runner = DurableRunner(lambda: Store(self.path), reg, worker_id="w1", lease_seconds=30)
        t = threading.Thread(target=runner.run_once, daemon=True)
        t.start()
        self.assertTrue(backend.entered.wait(timeout=5), "backend never started")
        # request cancellation while the node is mid-flight
        s = Store(self.path)
        s.request_cancellation(run_id, by="user", reason="stop")
        s.close()
        t.join(timeout=8)

        self.assertTrue(backend.cancelled, "backend.cancel() was not called")
        out = Store(self.path)
        self.assertEqual(out.get_run(run_id)["status"], "cancelled")
        out.close()


if __name__ == "__main__":
    unittest.main()
