"""PostgresRunStore conformance — SKIPPED unless MOIRA_PG_DSN is set.

Run against a local docker Postgres:
    docker compose up -d db
    pip install "psycopg[binary]"
    MOIRA_PG_DSN=postgresql://moira:moira@localhost:5432/moira \
        python3 -m unittest tests.test_pg_store
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DSN = os.environ.get("MOIRA_PG_DSN")

from moira_core.models import AuditRecord, Event  # noqa: E402


@unittest.skipUnless(DSN, "MOIRA_PG_DSN not set — skipping live Postgres tests")
class TestPostgresRunStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from moira_core.pg_store import PostgresRunStore
        cls.store = PostgresRunStore(DSN)
        # clean slate for the test ids we use
        for t in ("audit", "events", "runs", "workspaces"):
            cls.store.conn.execute(f"DELETE FROM {t} WHERE TRUE")

    @classmethod
    def tearDownClass(cls):
        cls.store.close()

    def test_run_lifecycle_roundtrip(self):
        s = self.store
        s.create_workspace("ws-pg", "PG", "/tmp/repo", "/tmp/code")
        self.assertEqual(s.get_workspace("ws-pg")["repo_path"], "/tmp/repo")

        s.create_run("run-pg-1", "pipe", {"id": "pipe", "name": "P", "nodes": []},
                     "owner", "running", workspace_id="ws-pg")
        s.save_run_state("run-pg-1", {"a": "succeeded", "b": "pending"})
        self.assertEqual(s.get_run_state("run-pg-1")["a"], "succeeded")
        s.update_run_status("run-pg-1", "succeeded")
        self.assertEqual(s.get_run("run-pg-1")["status"], "succeeded")
        self.assertEqual(len(s.list_runs("ws-pg")), 1)

    def test_events_monotonic_seq(self):
        s = self.store
        s.create_run("run-pg-2", "p", {"id": "p", "name": "P", "nodes": []}, "o", "running")
        seq1 = s.append_event(Event(run_id="run-pg-2", kind="run.start", message="a"))
        seq2 = s.append_event(Event(run_id="run-pg-2", kind="node.end", message="b"))
        self.assertGreater(seq2, seq1)  # DB-side IDENTITY, globally monotonic
        self.assertEqual(len(s.events("run-pg-2")), 2)

    def test_audit_overwrite_by_step_and_cost(self):
        s = self.store
        s.create_run("run-pg-3", "p", {"id": "p", "name": "P", "nodes": []}, "o", "running")
        rec = AuditRecord(step_id="step-x", run_id="run-pg-3", node_id="n", node_name="N",
                          owner="o", status="succeeded", cost={"tokens_in": 10, "tokens_out": 5, "usd": 0.02})
        s.save_audit(rec)
        rec.status = "failed"  # same step_id -> overwrite, not duplicate
        s.save_audit(rec)
        recs = s.audit_records("run-pg-3")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["status"], "failed")
        self.assertAlmostEqual(s.run_cost("run-pg-3")["usd"], 0.02)


if __name__ == "__main__":
    unittest.main()
