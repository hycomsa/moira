"""DurableRunner + job store smoke tests (ADR-006)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, DurableRunner, Engine, GateConfig, GateDecision, GateMode,
    MockBackend, Node, NodeType, Pipeline, Store, new_id,
)


def registry():
    reg = BackendRegistry()
    reg.register(MockBackend())
    return reg


class TestDurableRunner(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        self.path = tmp.name

    def store(self):
        return Store(self.path)

    def test_claim_and_complete_job_lifecycle(self):
        s = self.store()
        s.create_workspace("default", "Default", "/tmp/repo", None)
        s.create_run("run-job", "p", {"id": "p", "name": "P", "nodes": []}, "owner", "running")
        s.enqueue_job({"job_id": "job-1", "run_id": "run-job", "kind": "drive_run"})

        job = s.claim_next_job("worker-1", [], lease_seconds=30)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "leased")
        self.assertEqual(job["attempt"], 1)

        s.mark_job_running("job-1", "worker-1")
        self.assertEqual(s.get_job("job-1")["status"], "running")

        s.complete_job("job-1", "worker-1", "succeeded")
        done = s.get_job("job-1")
        self.assertEqual(done["status"], "succeeded")
        self.assertIsNone(done["lease_owner"])

    def test_runner_drives_existing_run_from_queued_job(self):
        pipe = Pipeline(id="p", name="P", nodes=[
            Node(id="a", name="Analyst", type=NodeType.PRODUCER,
                 role="requirements-analyst", backend="mock", spec_ref="FUNC-X"),
        ])
        s = self.store()
        run_id = Engine(s, registry(), owner="owner").create(
            pipe, {"func_id": "FUNC-X", "spec_text": "spec"}, workspace_id="default")
        s.enqueue_job({"job_id": new_id("job-"), "run_id": run_id, "kind": "drive_run",
                       "payload": {"context": {"func_id": "FUNC-X", "spec_text": "spec"}}})
        s.close()

        runner = DurableRunner(self.store, registry, worker_id="worker-test")
        self.assertTrue(runner.run_once())

        out = self.store()
        self.assertEqual(out.get_run(run_id)["status"], "succeeded")
        self.assertEqual(out.jobs(run_id)[0]["status"], "succeeded")
        self.assertEqual(len(out.audit_records(run_id)), 1)

    def test_runner_resumes_waiting_gate_from_queued_job(self):
        pipe = Pipeline(id="p", name="P", nodes=[
            Node(id="a", name="Analyst", type=NodeType.PRODUCER,
                 role="requirements-analyst", backend="mock", spec_ref="FUNC-X"),
            Node(id="g", name="Human Gate", type=NodeType.GATE,
                 gate=GateConfig(mode=GateMode.HUMAN, persona="lead")),
        ])
        s = self.store()
        run_id = Engine(s, registry(), owner="owner").create(
            pipe, {"func_id": "FUNC-X", "spec_text": "spec"}, workspace_id="default")
        s.enqueue_job({"job_id": "job-drive", "run_id": run_id, "kind": "drive_run",
                       "payload": {"context": {"func_id": "FUNC-X", "spec_text": "spec"}}})
        s.close()

        runner = DurableRunner(self.store, registry, worker_id="worker-test")
        self.assertTrue(runner.run_once())
        mid = self.store()
        self.assertEqual(mid.get_run(run_id)["status"], "waiting_gate")
        mid.enqueue_job({"job_id": "job-resume", "run_id": run_id, "kind": "resume_run",
                         "payload": {"context": {"func_id": "FUNC-X", "spec_text": "spec"},
                                     "decision": GateDecision(decision="approve", by="lead",
                                                              confirmed="checked").to_dict()}})
        mid.update_run_status(run_id, "running")
        mid.close()

        self.assertTrue(runner.run_once())
        out = self.store()
        self.assertEqual(out.get_run(run_id)["status"], "succeeded")
        self.assertEqual([j["status"] for j in out.jobs(run_id)], ["waiting_gate", "succeeded"])


if __name__ == "__main__":
    unittest.main()
