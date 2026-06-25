"""DurableRunner + job store smoke tests (ADR-006)."""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, DurableRunner, Engine, GateConfig, GateDecision, GateMode,
    MockBackend, Node, NodeType, Pipeline, Store, new_id,
)
from moira_core.models import BackendResult, Cost  # noqa: E402


def registry():
    reg = BackendRegistry()
    reg.register(MockBackend())
    return reg


class _GatedBackend:
    """A backend that blocks inside run() until released — lets a test hold a job
    in 'running' for a controlled, lease-exceeding duration."""
    name = "gated"

    def __init__(self, entered: threading.Event, release: threading.Event):
        self.entered = entered
        self.release = release

    def run(self, node, context):
        self.entered.set()
        self.release.wait(timeout=10)
        return BackendResult(output={"summary": "done"}, tools_used=["gated"], cost=Cost(), ok=True)


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


    def test_completion_reports_zero_rows_when_lease_stolen(self):
        """If a worker's lease is stolen mid-execution, its complete_job must report
        0 rows (lease lost) instead of silently succeeding (ADR-006 P1)."""
        s = self.store()
        s.create_run("run-steal", "p", {"id": "p", "name": "P", "nodes": []}, "owner", "running")
        s.enqueue_job({"job_id": "job-steal", "run_id": "run-steal", "kind": "drive_run"})

        s.claim_next_job("w1", [], lease_seconds=30)          # w1 owns the lease
        s.release_expired_leases(now=time.time() + 10_000)    # force expiry -> requeued
        s.claim_next_job("w2", [], lease_seconds=30)          # w2 steals the lease

        # w1 (the loser) must NOT be able to record completion
        self.assertEqual(s.complete_job("job-steal", "w1", "succeeded"), 0)
        # w2 (the current owner) can
        self.assertEqual(s.complete_job("job-steal", "w2", "succeeded"), 1)
        s.close()

    def test_mark_running_reports_zero_rows_when_not_owner(self):
        s = self.store()
        s.create_run("run-mr", "p", {"id": "p", "name": "P", "nodes": []}, "owner", "running")
        s.enqueue_job({"job_id": "job-mr", "run_id": "run-mr", "kind": "drive_run"})
        s.claim_next_job("w1", [], lease_seconds=30)
        self.assertEqual(s.mark_job_running("job-mr", "w1"), 1)
        self.assertEqual(s.mark_job_running("job-mr", "someone-else"), 0)
        s.close()

    def test_heartbeat_keeps_lease_held_during_long_drive(self):
        """A drive that outlives the lease must keep its lease via heartbeat, so a
        second worker cannot steal and double-execute the run (ADR-006:110)."""
        entered, release = threading.Event(), threading.Event()

        def gated_registry():
            reg = BackendRegistry()
            reg.register(_GatedBackend(entered, release))
            return reg

        pipe = Pipeline(id="p", name="P", nodes=[
            Node(id="a", name="Worker", type=NodeType.PRODUCER, role="x",
                 backend="gated", spec_ref="FUNC-X"),
        ])
        s = self.store()
        run_id = Engine(s, gated_registry(), owner="o").create(pipe, {}, workspace_id="default")
        s.enqueue_job({"job_id": "job-long", "run_id": run_id, "kind": "drive_run",
                       "payload": {"context": {}}})
        s.close()

        runner = DurableRunner(self.store, gated_registry, worker_id="w1", lease_seconds=1)
        t = threading.Thread(target=runner.run_once, daemon=True)
        t.start()
        try:
            self.assertTrue(entered.wait(timeout=5), "backend never started executing")
            # hold past the ORIGINAL 1s lease window — a missing heartbeat lets it expire
            time.sleep(1.4)
            # a second worker tries to steal the (supposedly expired) job
            s2 = self.store()
            s2.release_expired_leases()
            stolen = s2.claim_next_job("w2", [], lease_seconds=30)
            s2.close()
        finally:
            release.set()
            t.join(timeout=5)

        self.assertIsNone(stolen, "lease must stay held by heartbeat; job not stealable")
        out = self.store()
        self.assertEqual(out.get_run(run_id)["status"], "succeeded")
        self.assertEqual(out.jobs(run_id)[0]["status"], "succeeded")


    def test_external_runner_mode_requires_postgres(self):
        """External (team) mode against SQLite is unsafe for multi-process — reject it."""
        import moira_runner
        old = os.environ.pop("MOIRA_PRIMARY", None)
        try:
            with self.assertRaises(SystemExit):
                moira_runner.main(["--mode", "external", "--db", self.path])
        finally:
            if old is not None:
                os.environ["MOIRA_PRIMARY"] = old


if __name__ == "__main__":
    unittest.main()
