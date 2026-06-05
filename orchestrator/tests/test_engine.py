"""End-to-end + unit tests for the Moira orchestration core (stdlib unittest).

Run: python -m unittest discover -s tests -v   (from orchestrator/)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, Engine, GateConfig, GateDecision, GateMode, MockBackend,
    Severity, Status, Store, default_sdlc_pipeline,
)
from moira_core.backends.mock import MockBackend as Mock  # noqa: E402
from moira_core.gates import evaluate_gate, simulate_routing  # noqa: E402
from moira_core.models import BackendResult, Finding  # noqa: E402
from moira_core.repo_reader import AISdlcRepo  # noqa: E402


def fresh_engine(scenario=None, owner="tester"):
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    store = Store(tmp.name)
    reg = BackendRegistry()
    reg.register(Mock(scenario=scenario or {}))
    return Engine(store, reg, owner=owner), store


class TestHappyPath(unittest.TestCase):
    def test_full_pipeline_all_auto_succeeds(self):
        engine, store = fresh_engine()
        pipe = default_sdlc_pipeline(func_ref="FUNC-DEMO",
                                     analysis_gate=GateMode.AUTO,
                                     impl_gate=GateMode.AUTO)
        ctx = {"func_id": "FUNC-DEMO", "spec_text": "auth spec", "lineage": ["FUNC-DEMO", "REQ-X"]}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.status, Status.SUCCEEDED, msg=str(res))

    def test_audit_records_have_required_fields(self):
        engine, store = fresh_engine()
        pipe = default_sdlc_pipeline(impl_gate=GateMode.AUTO)
        res = engine.start(pipe, {"func_id": "FUNC-DEMO", "spec_text": "x", "lineage": ["FUNC-DEMO"]})
        recs = store.audit_records(res.run_id)
        self.assertGreater(len(recs), 0)
        required = {"input", "output", "tools", "decisions", "approvals", "cost",
                    "time_start", "time_end", "owner"}
        for r in recs:
            self.assertTrue(required.issubset(r.keys()), msg=f"missing fields in {r['node_name']}")

    def test_cost_aggregated(self):
        engine, store = fresh_engine()
        pipe = default_sdlc_pipeline(impl_gate=GateMode.AUTO)
        res = engine.start(pipe, {"func_id": "F", "spec_text": "x", "lineage": []})
        cost = store.run_cost(res.run_id)
        self.assertGreater(cost["usd"], 0.0)
        self.assertGreater(cost["tokens_in"], 0)

    def test_lineage_recorded_on_steps(self):
        engine, store = fresh_engine()
        pipe = default_sdlc_pipeline(impl_gate=GateMode.AUTO)
        res = engine.start(pipe, {"func_id": "FUNC-DEMO", "spec_text": "x",
                                  "lineage": ["FUNC-DEMO", "REQ-AUTH-01", "ADR-005"]})
        recs = store.audit_records(res.run_id)
        producer = [r for r in recs if r["node_id"] == "implement"][0]
        self.assertIn("ADR-005", producer["lineage"])


class TestHybridGate(unittest.TestCase):
    def test_low_confidence_escalates_then_approve_resumes(self):
        # force a low-confidence quality finding -> hybrid gate must escalate
        scenario = {"verify-quality": {"confidence": 0.60, "severity": "medium"}}
        engine, store = fresh_engine(scenario=scenario)
        pipe = default_sdlc_pipeline(impl_gate=GateMode.HYBRID)
        ctx = {"func_id": "F", "spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "gate-impl")
        # the run row's status must reflect the pause (drives the cockpit Inbox)
        self.assertEqual(store.get_run(res.run_id)["status"], Status.WAITING_GATE.value)

        # human approves -> run resumes and completes
        res2 = engine.resume(res.run_id, pipe, ctx,
                             GateDecision(decision="approve", by="lead-dev",
                                          confirmed="reviewed missing docstring, acceptable"))
        self.assertEqual(res2.status, Status.SUCCEEDED, msg=str(res2))

    def test_high_confidence_auto_accepts(self):
        scenario = {"verify-quality": {"confidence": 0.97},
                    "verify-security": {"confidence": 0.99}}
        engine, store = fresh_engine(scenario=scenario)
        pipe = default_sdlc_pipeline(impl_gate=GateMode.HYBRID)
        res = engine.start(pipe, {"func_id": "F", "spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)

    def test_blocking_severity_escalates_even_on_auto(self):
        scenario = {"verify-security": {"severity": "critical", "confidence": 0.99}}
        engine, store = fresh_engine(scenario=scenario)
        pipe = default_sdlc_pipeline(impl_gate=GateMode.AUTO)
        res = engine.start(pipe, {"func_id": "F", "spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.WAITING_GATE)


class TestRejectRework(unittest.TestCase):
    def test_reject_returns_to_producer_then_approve(self):
        scenario = {"verify-quality": {"confidence": 0.60}}
        engine, store = fresh_engine(scenario=scenario)
        pipe = default_sdlc_pipeline(impl_gate=GateMode.HYBRID)
        ctx = {"func_id": "F", "spec_text": "x", "lineage": []}
        res = engine.start(pipe, ctx)
        self.assertEqual(res.status, Status.WAITING_GATE)

        # reject -> should go back to 'implement' (on_reject_goto) and re-run forward.
        # raise the confidence so the re-run passes the gate this time.
        engine.registry.get("mock").scenario["verify-quality"] = {"confidence": 0.97}
        engine.registry.get("mock").scenario["verify-security"] = {"confidence": 0.97}
        res2 = engine.resume(res.run_id, pipe, ctx,
                             GateDecision(decision="reject", by="lead-dev",
                                          feedback="add docstring to token.verify"))
        self.assertEqual(res2.status, Status.SUCCEEDED, msg=str(res2))
        # feedback must have been delivered to the producer
        recs = store.audit_records(res.run_id)
        impl_recs = [r for r in recs if r["node_id"] == "implement"]
        self.assertGreaterEqual(len(impl_recs), 2)  # ran at least twice


class TestRetryThenGate(unittest.TestCase):
    def test_exhausted_retries_escalates(self):
        # implement fails 3 times; max_retries=2 -> 3 attempts -> all fail -> escalate
        scenario = {"implement": {"fail_times": 99}}
        engine, store = fresh_engine(scenario=scenario)
        pipe = default_sdlc_pipeline(impl_gate=GateMode.AUTO)
        res = engine.start(pipe, {"func_id": "F", "spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "implement")
        # verify retries were logged
        kinds = [e["kind"] for e in store.events(res.run_id)]
        self.assertGreaterEqual(kinds.count("retry"), 3)

    def test_transient_failure_then_success(self):
        scenario = {"implement": {"fail_times": 1}}  # fails once, succeeds on retry
        engine, store = fresh_engine(scenario=scenario)
        pipe = default_sdlc_pipeline(impl_gate=GateMode.AUTO)
        res = engine.start(pipe, {"func_id": "F", "spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)


class TestGateUnit(unittest.TestCase):
    def _vr(self, conf, sev=Severity.LOW):
        return [BackendResult(findings=[Finding(id="f", title="t", severity=sev, confidence=conf)])]

    def test_auto_clean_approves(self):
        d = evaluate_gate(GateConfig(mode=GateMode.AUTO), self._vr(0.9))
        self.assertEqual(d.decision, "approve")

    def test_off_always_approves(self):
        d = evaluate_gate(GateConfig(mode=GateMode.OFF), self._vr(0.1, Severity.CRITICAL))
        self.assertEqual(d.decision, "approve")

    def test_human_always_escalates(self):
        d = evaluate_gate(GateConfig(mode=GateMode.HUMAN, persona="ciso"), self._vr(0.99))
        self.assertEqual(d.decision, "escalate")
        self.assertEqual(d.by, "ciso")

    def test_hybrid_thresholds(self):
        cfg = GateConfig(mode=GateMode.HYBRID, high_cutoff=0.85, low_cutoff=0.5)
        self.assertEqual(evaluate_gate(cfg, self._vr(0.9)).decision, "approve")
        self.assertEqual(evaluate_gate(cfg, self._vr(0.3)).decision, "reject")
        self.assertEqual(evaluate_gate(cfg, self._vr(0.7)).decision, "escalate")

    def test_simulate_routing(self):
        cfg = GateConfig(mode=GateMode.HYBRID, high_cutoff=0.85, low_cutoff=0.5)
        buckets = simulate_routing(cfg, [0.95, 0.9, 0.7, 0.6, 0.3])
        self.assertEqual(len(buckets["approve"]), 2)
        self.assertEqual(len(buckets["escalate"]), 2)
        self.assertEqual(len(buckets["reject"]), 1)


class TestRepoReader(unittest.TestCase):
    def test_lineage_extraction(self):
        repo = AISdlcRepo("/nonexistent")
        ids = repo.trace_lineage("Implements REQ-AUTH-01 per ADR-005 and INT-MOIRA-cockpit",
                                 "FUNC-AUTH")
        self.assertIn("FUNC-AUTH", ids)
        self.assertIn("REQ-AUTH-01", ids)
        self.assertIn("ADR-005", ids)
        self.assertIn("INT-MOIRA-cockpit", ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
