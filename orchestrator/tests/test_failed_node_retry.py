"""QW5 — a third decision, "retry", on an escalated failed node.

When a node exhausts its backend retries the run escalates to a human — but the
only decisions were approve/reject, and both had wrong semantics for a FAILED
node: approve marked it DONE with NO output (downstream ran on an empty
upstream, silently), and reject usually ended the run (producers rarely have an
on_reject_goto). These tests pin the new contract:

- decision "retry" resets the failed node to pending and re-drives it with a
  fresh attempt budget; optional human feedback reaches the node's prompt
- "retry" is recorded in the audit like any human gate decision
- "retry" on a run waiting at a real GATE is invalid (gates re-evaluate, they
  don't re-execute)
- approve on a failed node still works (a human may accept the gap) but now
  emits an explicit `node.accept_failed` event — never a silent skip
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, Engine, GateConfig, GateDecision, GateMode, Status, Store,
    default_sdlc_pipeline,
)
from moira_core import authz  # noqa: E402
from moira_core.backends.mock import MockBackend  # noqa: E402
from moira_core.models import BackendResult, Cost, Node, NodeType, Pipeline  # noqa: E402


def _fresh_engine(scenario=None):
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    store = Store(tmp.name)
    reg = BackendRegistry()
    reg.register(MockBackend(scenario=scenario or {}))
    return Engine(store, reg, owner="tester"), store


class _SwitchableBackend:
    """Fails while `broken` is True; records the feedback each call saw."""
    name = "mock"

    def __init__(self):
        self.broken = True
        self.feedback_seen: list[str] = []

    def run(self, node: Node, context) -> BackendResult:
        self.feedback_seen.append(context.get("feedback", {}).get(node.id, ""))
        if self.broken:
            return BackendResult(ok=False, error="deterministic breakage", cost=Cost())
        return BackendResult(output={"work": "done"}, cost=Cost())


class TestRetryDecision(unittest.TestCase):
    def setUp(self):
        # zero the QW3 retry backoff — these tests exhaust attempts on purpose
        import moira_core.engine as eng
        self._backoff = eng.RETRY_BACKOFF_BASE
        eng.RETRY_BACKOFF_BASE = 0.0

    def tearDown(self):
        import moira_core.engine as eng
        eng.RETRY_BACKOFF_BASE = self._backoff

    def test_retry_reruns_failed_node_to_success(self):
        scenario = {"implement": {"fail_times": 99}}
        engine, store = _fresh_engine(scenario)
        pipe = default_sdlc_pipeline(impl_gate=GateMode.AUTO)
        ctx = {"func_id": "F", "spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "implement")

        engine.registry.get("mock").scenario["implement"] = {}  # fixed now
        res2 = engine.resume(res.run_id, pipe, ctx,
                             GateDecision(decision="retry", by="lead-dev",
                                          confirmed="transient breakage, try again"))
        self.assertEqual(res2.status, Status.SUCCEEDED, msg=str(res2))
        # the retry decision is part of the audit trail
        retries = [ap for rec in store.audit_records(res.run_id)
                   for ap in rec.get("approvals", []) if ap.get("decision") == "retry"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["by"], "lead-dev")

    def test_retry_delivers_human_feedback_to_the_node(self):
        stub = _SwitchableBackend()
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); tmp.close()
        reg = BackendRegistry(); reg.register(stub)
        engine = Engine(Store(tmp.name), reg, owner="tester")
        pipe = Pipeline(id="p", name="one", nodes=[
            Node(id="work", name="Work", type=NodeType.PRODUCER, max_retries=0)])
        ctx = {"spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.status, Status.WAITING_GATE)

        stub.broken = False
        res2 = engine.resume(res.run_id, pipe, ctx,
                             GateDecision(decision="retry", by="lead-dev",
                                          feedback="use the staging config this time"))
        self.assertEqual(res2.status, Status.SUCCEEDED, msg=str(res2))
        self.assertIn("use the staging config this time", stub.feedback_seen[-1])

    def test_retry_on_a_real_gate_is_invalid(self):
        engine, store = _fresh_engine({"verify-quality": {"confidence": 0.60}})
        pipe = default_sdlc_pipeline(impl_gate=GateMode.HYBRID)
        ctx = {"func_id": "F", "spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "gate-impl")  # a GATE, not a failed node
        with self.assertRaises(ValueError):
            engine.resume(res.run_id, pipe, ctx,
                          GateDecision(decision="retry", by="lead-dev"))

    def test_approve_on_failed_node_is_explicit_not_silent(self):
        scenario = {"implement": {"fail_times": 99}}
        engine, store = _fresh_engine(scenario)
        pipe = default_sdlc_pipeline(impl_gate=GateMode.AUTO)
        ctx = {"func_id": "F", "spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.waiting_node, "implement")
        engine.resume(res.run_id, pipe, ctx,
                      GateDecision(decision="approve", by="lead-dev",
                                   confirmed="accepting the gap"))
        kinds = [e["kind"] for e in store.events(res.run_id)]
        self.assertIn("node.accept_failed", kinds)

    def test_approve_on_a_real_gate_stays_silent(self):
        engine, store = _fresh_engine({"verify-quality": {"confidence": 0.60}})
        pipe = default_sdlc_pipeline(impl_gate=GateMode.HYBRID)
        ctx = {"func_id": "F", "spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        engine.resume(res.run_id, pipe, ctx,
                      GateDecision(decision="approve", by="lead-dev", confirmed="ok"))
        kinds = [e["kind"] for e in store.events(res.run_id)]
        self.assertNotIn("node.accept_failed", kinds)


class TestRetryHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("MOIRA_AUTH_MODE", "MOIRA_DB")}
        db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); db.close()
        os.environ["MOIRA_AUTH_MODE"] = "off"
        os.environ["MOIRA_DB"] = db.name
        import moira_api
        moira_api.DB = db.name
        cls.moira_api = moira_api
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), moira_api.Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for k, v in cls._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _seed_waiting(self, rid, node):
        s = self.moira_api.open_store()
        pipe = Pipeline(id="p", name="P", nodes=[node])
        s.create_run(rid, "p", pipe.to_dict(), "owner", "waiting_gate")
        s.save_run_state(rid, {node.id: "waiting_gate"})
        s.close()

    def _retry(self, rid):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", f"/api/runs/{rid}/retry",
                  body=json.dumps({"feedback": "again please"}),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data

    def test_retry_on_failed_node_is_accepted(self):
        self._seed_waiting("run-failed-node", Node(id="work", name="W",
                                                   type=NodeType.PRODUCER))
        status, data = self._retry("run-failed-node")
        self.assertEqual(status, 200, msg=str(data))

    def test_retry_on_gate_wait_is_409(self):
        self._seed_waiting("run-gate-wait", Node(
            id="g", name="G", type=NodeType.GATE,
            gate=GateConfig(mode=GateMode.HUMAN, persona="lead-dev")))
        status, data = self._retry("run-gate-wait")
        self.assertEqual(status, 409, msg=str(data))

    def test_retry_requires_approve_gate_permission(self):
        self.assertEqual(authz.required_action("POST", "/api/runs/x/retry"),
                         "approve_gate")


if __name__ == "__main__":
    unittest.main()
