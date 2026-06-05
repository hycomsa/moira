"""Client gate tests — the wedge: a non-technical client approves the artifact
(requirements/analysis in business language) before any code is written."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, Engine, GateDecision, Status, Store, client_gated_pipeline,
)
from moira_core.backends.mock import MockBackend  # noqa: E402


def engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    store = Store(tmp.name)
    reg = BackendRegistry()
    reg.register(MockBackend())
    return Engine(store, reg, owner="tomasz.skonieczny"), store


class TestClientGate(unittest.TestCase):
    def test_pauses_at_client_gate_before_code(self):
        eng, store = engine()
        pipe = client_gated_pipeline(func_ref="FUNC-MOIRA-audit-record")
        res = eng.start(pipe, {"func_id": "F", "spec_text": "auth", "lineage": ["FUNC-X"]})
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "gate-client")
        # code must NOT have run yet — client approves before implementation
        recs = store.audit_records(res.run_id)
        self.assertFalse(any(r["node_id"] == "implement" for r in recs))

    def test_client_gate_surfaces_the_artifact(self):
        eng, store = engine()
        pipe = client_gated_pipeline()
        res = eng.start(pipe, {"func_id": "F", "spec_text": "auth", "lineage": []})
        gate_rec = next(a for a in store.audit_records(res.run_id)
                        if a["node_id"] == "gate-client")
        self.assertEqual(gate_rec["input"]["persona"], "client")
        self.assertEqual(gate_rec["input"]["audience"], "client")
        # the analyst's output is surfaced for the client to review (business language)
        review = gate_rec["input"]["review"]
        self.assertIn("analyze", review)
        self.assertIn("requirements", review["analyze"])

    def test_client_approves_then_pipeline_proceeds(self):
        eng, store = engine()
        pipe = client_gated_pipeline()
        ctx = {"func_id": "F", "spec_text": "auth", "lineage": []}
        res = eng.start(pipe, ctx)
        res2 = eng.resume(res.run_id, pipe, ctx,
                          GateDecision(decision="approve", by="client@bank.example",
                                       confirmed="Zatwierdzam zakres — zgodny z intencją biznesową"))
        # now it reaches the hybrid impl gate (auto-accepts at default mock confidence) -> done
        self.assertEqual(res2.status, Status.SUCCEEDED, msg=str(res2))
        recs = store.audit_records(res.run_id)
        self.assertTrue(any(r["node_id"] == "implement" for r in recs))

    def test_client_rejects_returns_to_analysis(self):
        eng, store = engine()
        pipe = client_gated_pipeline()
        ctx = {"func_id": "F", "spec_text": "auth", "lineage": []}
        res = eng.start(pipe, ctx)
        res2 = eng.resume(res.run_id, pipe, ctx,
                          GateDecision(decision="reject", by="client@bank.example",
                                       feedback="Brakuje wymagania o eksporcie danych"))
        # rejected -> back to analyze -> re-runs forward -> pauses again at client gate
        self.assertEqual(res2.status, Status.WAITING_GATE)
        recs = store.audit_records(res.run_id)
        self.assertGreaterEqual(len([r for r in recs if r["node_id"] == "analyze"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
