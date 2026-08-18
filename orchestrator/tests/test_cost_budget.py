"""ST4 — server-enforced cost budgets (per run and per workspace-month).

Budgets used to live only in the cockpit's localStorage — nothing server-side
stopped a run from spending (the dogfood report priced a 3-iteration loop at
$1.50 on a trivial task). These tests pin the new contract:

- budgets persist in a generic `settings` table (scope/key/value) — no schema
  migration, and a run budget survives resume independent of context rebuilds
- the engine checks budgets BEFORE executing each node batch; when exceeded it
  pauses the run on the next pending step (waiting_gate + `budget.wait` event
  naming spent vs limit) — spending stops, work is never silently dropped
- continuing is an explicit governed act: raise the budget (RBAC `configure`)
  and use the existing ADR-013 `retry` decision — no hidden override state
- no budget configured = behavior unchanged
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

from moira_core import BackendRegistry, Engine, GateDecision, Status, Store  # noqa: E402
from moira_core import authz  # noqa: E402
from moira_core.models import BackendResult, Cost, Node, NodeType, Pipeline  # noqa: E402


class _DollarBackend:
    """Every node execution costs exactly $1 — budgets become arithmetic."""
    name = "mock"

    def __init__(self):
        self.executed: list[str] = []

    def run(self, node: Node, context) -> BackendResult:
        self.executed.append(node.id)
        return BackendResult(output={"work": node.id},
                             cost=Cost(tokens_in=10, tokens_out=10, usd=1.0))


def _pipe(n=3) -> Pipeline:
    return Pipeline(id="p", name="chain", nodes=[
        Node(id=f"s{i}", name=f"Step {i}", type=NodeType.PRODUCER) for i in range(1, n + 1)])


def _engine(ws_id="default"):
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    store = Store(tmp.name)
    store.create_workspace(ws_id, ws_id, "", "")
    be = _DollarBackend()
    reg = BackendRegistry()
    reg.register(be)
    return Engine(store, reg, owner="tester"), store, be


class TestSettingsStore(unittest.TestCase):
    def test_settings_roundtrip_and_absence(self):
        _, store, _ = _engine()
        self.assertIsNone(store.get_setting("workspace:default", "budget_month_usd"))
        store.set_setting("workspace:default", "budget_month_usd", "12.5")
        self.assertEqual(store.get_setting("workspace:default", "budget_month_usd"), "12.5")
        store.set_setting("workspace:default", "budget_month_usd", "20")
        self.assertEqual(store.get_setting("workspace:default", "budget_month_usd"), "20")


class TestRunBudget(unittest.TestCase):
    def test_run_budget_pauses_before_overspending_further(self):
        engine, store, be = _engine()
        pipe = _pipe(3)
        ctx = {"spec_text": "x", "lineage": []}
        run_id = engine.create(pipe, ctx)
        store.set_setting(f"run:{run_id}", "budget_usd", "1.5")
        res = engine.drive_existing(run_id, pipe, ctx)
        # s1 runs ($1.0 < 1.5), s2 runs (check happens before: 1.0 < 1.5 -> ok,
        # now $2.0), before s3: 2.0 >= 1.5 -> PAUSE on s3, s3 never executes
        self.assertEqual(res.status, Status.WAITING_GATE, msg=str(res))
        self.assertEqual(res.waiting_node, "s3")
        self.assertEqual(be.executed, ["s1", "s2"])
        evs = [e for e in store.events(run_id) if e["kind"] == "budget.wait"]
        self.assertEqual(len(evs), 1)
        self.assertIn("2.00", evs[0]["message"])
        self.assertIn("1.50", evs[0]["message"])

    def test_raising_the_budget_and_retry_resumes_to_success(self):
        engine, store, be = _engine()
        pipe = _pipe(3)
        ctx = {"spec_text": "x", "lineage": []}
        run_id = engine.create(pipe, ctx)
        store.set_setting(f"run:{run_id}", "budget_usd", "1.5")
        res = engine.drive_existing(run_id, pipe, ctx)
        self.assertEqual(res.status, Status.WAITING_GATE)

        # retry WITHOUT raising the budget: pauses again, still no spend
        res2 = engine.resume(run_id, pipe, ctx,
                             GateDecision(decision="retry", by="lead-dev"))
        self.assertEqual(res2.status, Status.WAITING_GATE, msg=str(res2))
        self.assertEqual(be.executed, ["s1", "s2"])

        # the governed continue: raise the budget, then retry
        store.set_setting(f"run:{run_id}", "budget_usd", "10")
        res3 = engine.resume(run_id, pipe, ctx,
                             GateDecision(decision="retry", by="lead-dev",
                                          confirmed="budget raised to $10"))
        self.assertEqual(res3.status, Status.SUCCEEDED, msg=str(res3))
        self.assertEqual(be.executed, ["s1", "s2", "s3"])

    def test_no_budget_means_unchanged_behavior(self):
        engine, store, be = _engine()
        res = engine.start(_pipe(3), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)
        self.assertEqual(be.executed, ["s1", "s2", "s3"])


class TestWorkspaceMonthlyBudget(unittest.TestCase):
    def test_month_budget_spans_runs_and_blocks_new_spend(self):
        engine, store, be = _engine(ws_id="teamws")
        store.set_setting("workspace:teamws", "budget_month_usd", "1.5")
        ctx = {"spec_text": "x", "lineage": []}
        res1 = engine.start(_pipe(2), ctx, workspace_id="teamws")
        # run1: s1 ok (0 < 1.5), s2 ok (1.0 < 1.5) -> succeeded at $2.0 total
        self.assertEqual(res1.status, Status.SUCCEEDED)
        # run2 in the same workspace/month: $2.0 >= $1.5 -> pauses before ANY spend
        res2 = engine.start(_pipe(2), dict(ctx), workspace_id="teamws")
        self.assertEqual(res2.status, Status.WAITING_GATE, msg=str(res2))
        self.assertEqual(be.executed, ["s1", "s2", "s1"][:2] + [])  # nothing from run2
        evs = [e for e in store.events(res2.run_id) if e["kind"] == "budget.wait"]
        self.assertEqual(len(evs), 1)
        self.assertIn("workspace", evs[0]["message"])


class TestBudgetHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("MOIRA_AUTH_MODE", "MOIRA_DB")}
        db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); db.close()
        os.environ["MOIRA_AUTH_MODE"] = "off"
        os.environ["MOIRA_DB"] = db.name
        import moira_api
        moira_api.DB = db.name
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), moira_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for k, v in cls._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request(method, path,
                  body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data

    def test_budget_endpoint_persists_and_spend_reports_it(self):
        status, _ = self._req("POST", "/api/workspaces/default/budget",
                              {"month_usd": 25, "run_usd": 5})
        self.assertEqual(status, 200)
        status, data = self._req("GET", "/api/spend?ws=default")
        self.assertEqual(status, 200)
        self.assertEqual(data.get("budget", {}).get("month_usd"), 25.0)
        self.assertEqual(data.get("budget", {}).get("run_usd"), 5.0)

    def test_budget_route_requires_configure_permission(self):
        self.assertEqual(
            authz.required_action("POST", "/api/workspaces/default/budget"),
            "configure")


if __name__ == "__main__":
    unittest.main()
