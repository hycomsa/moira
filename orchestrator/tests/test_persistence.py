"""CompositeStore fan-out + make_run_store factory behavior."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    CompositeStore, ExportSink, Store, make_run_store,
)
from moira_core.models import AuditRecord, Event  # noqa: E402


def fresh_sqlite() -> Store:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    return Store(tmp.name)


class RecordingSink(ExportSink):
    def __init__(self):
        self.calls: list[str] = []

    def on_run_created(self, *a):
        self.calls.append("created")

    def on_run_status(self, *a):
        self.calls.append("status")

    def on_run_state(self, *a):
        self.calls.append("state")

    def on_event(self, *a):
        self.calls.append("event")

    def on_audit(self, *a):
        self.calls.append("audit")

    def close(self):
        self.calls.append("close")


class ExplodingSink(ExportSink):
    def on_audit(self, *a):
        raise RuntimeError("boom")


class ReporterSink(ExportSink):
    """Mimics GitExportSink's report hook (duck-typed via write_report)."""
    def __init__(self):
        self.reports: list[tuple] = []

    def write_report(self, repo, run_id, md):
        self.reports.append((repo, run_id, md))


class TestCompositeFanOut(unittest.TestCase):
    def setUp(self):
        self.primary = fresh_sqlite()
        self.sink = RecordingSink()
        self.store = CompositeStore(self.primary, [self.sink])
        self.store.create_workspace("default", "Default", "/tmp/repo", None)

    def test_writes_fan_out_to_sink(self):
        self.store.create_run("run-1", "p", {"id": "p", "name": "P", "nodes": []},
                              "owner", "running")
        self.store.save_run_state("run-1", {"a": "succeeded"})
        self.store.update_run_status("run-1", "succeeded")
        self.store.append_event(Event(run_id="run-1", kind="run.start", message="hi"))
        self.store.save_audit(AuditRecord(step_id="s1", run_id="run-1", node_id="a",
                                          node_name="A", owner="owner"))
        self.assertEqual(self.sink.calls,
                         ["created", "state", "status", "event", "audit"])

    def test_reads_hit_primary_only(self):
        self.store.create_run("run-2", "p", {"id": "p", "name": "P", "nodes": []},
                              "owner", "running")
        # a read should return primary data and NOT touch the sink
        run = self.store.get_run("run-2")
        self.assertIsNotNone(run)
        self.assertEqual(run["run_id"], "run-2")
        self.assertNotIn("event", self.sink.calls)  # no read fans out

    def test_sink_exception_is_non_fatal(self):
        store = CompositeStore(self.primary, [ExplodingSink()])
        store.create_run("run-3", "p", {"id": "p", "name": "P", "nodes": []},
                         "owner", "running")
        # the exploding sink must not break the primary write
        store.save_audit(AuditRecord(step_id="s", run_id="run-3", node_id="a",
                                     node_name="A", owner="o"))
        self.assertEqual(len(self.primary.audit_records("run-3")), 1)


class TestAutoReport(unittest.TestCase):
    def test_terminal_status_triggers_report(self):
        primary = fresh_sqlite()
        reporter = ReporterSink()
        store = CompositeStore(primary, [reporter])
        store.create_workspace("default", "Default", "/tmp/repo", None)
        store.create_run("run-r", "p", {"id": "p", "name": "P", "nodes": []},
                         "owner", "running")
        store.save_audit(AuditRecord(step_id="s", run_id="run-r", node_id="n",
                                     node_name="N", owner="o", status="succeeded"))
        self.assertEqual(reporter.reports, [])  # not terminal yet
        store.update_run_status("run-r", "succeeded")
        self.assertEqual(len(reporter.reports), 1)
        repo, run_id, md = reporter.reports[0]
        self.assertEqual((repo, run_id), ("/tmp/repo", "run-r"))
        self.assertIn("# Run report", md)

    def test_non_terminal_status_no_report(self):
        primary = fresh_sqlite()
        reporter = ReporterSink()
        store = CompositeStore(primary, [reporter])
        store.create_workspace("default", "Default", "/tmp/repo", None)
        store.create_run("run-w", "p", {"id": "p", "name": "P", "nodes": []}, "o", "running")
        store.update_run_status("run-w", "waiting_gate")
        self.assertEqual(reporter.reports, [])


class TestFactory(unittest.TestCase):
    def test_default_is_bare_sqlite_no_composite(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        env = {k: os.environ.pop(k) for k in
               ("MOIRA_PRIMARY", "MOIRA_GIT_EXPORT", "MOIRA_PG_DSN") if k in os.environ}
        try:
            store = make_run_store(tmp.name)
            self.assertIsInstance(store, Store)
            self.assertNotIsInstance(store, CompositeStore)
        finally:
            os.environ.update(env)

    def test_git_export_yields_composite(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        store = make_run_store(tmp.name, repo_path="/tmp/x", git_export=True)
        self.assertIsInstance(store, CompositeStore)
        self.assertEqual(len(store.sinks), 1)


if __name__ == "__main__":
    unittest.main()
