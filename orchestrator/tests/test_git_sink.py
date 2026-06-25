"""GitExportSink — git-native audit mirror, end-to-end through the engine."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, CompositeStore, Engine, GateMode, GitExportSink, Status,
    Store, default_sdlc_pipeline,
)
from moira_core.backends.mock import MockBackend  # noqa: E402


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True).stdout


class TestGitSink(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "-C", self.repo, "init", "-q"], check=True)
        # pre-existing user work OUTSIDE .moira-runs — must stay untouched
        Path(self.repo, "MY_WORK.txt").write_text("user changes\n", "utf-8")

        db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        db.close()
        primary = Store(db.name)
        primary.create_workspace("default", "Default", self.repo, None)
        sink = GitExportSink(default_repo=self.repo)
        self.store = CompositeStore(primary, [sink])
        reg = BackendRegistry()
        reg.register(MockBackend())
        self.engine = Engine(self.store, reg, owner="tester")

    def _run(self):
        pipe = default_sdlc_pipeline(func_ref="FUNC-DEMO",
                                     analysis_gate=GateMode.AUTO, impl_gate=GateMode.AUTO)
        res = self.engine.start(pipe, {"func_id": "FUNC-DEMO", "spec_text": "x",
                                       "lineage": ["FUNC-DEMO"]})
        self.assertEqual(res.status, Status.SUCCEEDED, msg=str(res))
        return res

    def test_layout_written(self):
        res = self._run()
        d = Path(self.repo, ".moira-runs", res.run_id)
        self.assertTrue((d / "run.yaml").exists())
        self.assertTrue((d / "state.yaml").exists())
        self.assertTrue((d / "pipeline.json").exists())
        self.assertTrue((d / "events.jsonl").exists())
        self.assertTrue((d / "audit").is_dir())
        self.assertGreater(len(list((d / "audit").glob("*.json"))), 0)

    def test_events_jsonl_append_only(self):
        res = self._run()
        lines = Path(self.repo, ".moira-runs", res.run_id, "events.jsonl").read_text().splitlines()
        self.assertGreater(len(lines), 1)
        for ln in lines:  # every line is a valid event object
            ev = json.loads(ln)
            self.assertEqual(ev["run_id"], res.run_id)

    def test_final_status_persisted(self):
        res = self._run()
        from moira_core import yamlio  # noqa: PLC0415
        run = yamlio.load(Path(self.repo, ".moira-runs", res.run_id, "run.yaml").read_text())
        self.assertEqual(run["status"], "succeeded")

    def test_commits_on_transitions(self):
        res = self._run()
        log = git(self.repo, "log", "--oneline", "--", f".moira-runs/{res.run_id}")
        commits = [l for l in log.splitlines() if l.strip()]
        # start + several state transitions + terminal status => multiple commits
        self.assertGreaterEqual(len(commits), 3, msg=log)
        self.assertTrue(any("start" in c for c in commits), msg=log)
        self.assertTrue(any("succeeded" in c for c in commits), msg=log)

    def test_user_work_not_committed(self):
        self._run()
        # MY_WORK.txt was never added; it must remain untracked despite our commits
        tracked = git(self.repo, "ls-files")
        self.assertNotIn("MY_WORK.txt", tracked)
        self.assertIn(".moira-runs/", tracked)

    def _git_audit_records(self, run_id):
        adir = Path(self.repo, ".moira-runs", run_id, "audit")
        return [json.loads(f.read_text("utf-8")) for f in adir.glob("*.json")]

    def test_git_audit_files_are_sealed_and_verify(self):
        """The git mirror must carry the SAME sealed evidence as the primary —
        every exported audit record has prev_hash + hash and the chain verifies
        (the 'git-native tamper-evident audit' claim must hold for the artifact a
        reviewer actually opens). ADR-005 / must-fix #4."""
        res = self._run()
        from moira_core.integrity import verify_chain, verify_export
        git_recs = self._git_audit_records(res.run_id)
        self.assertTrue(git_recs)
        for r in git_recs:
            self.assertIn("prev_hash", r)
            self.assertIn("hash", r)
        # the mirror verifies standalone (order reconstructed from prev_hash links)
        gv = verify_export(git_recs)
        self.assertTrue(gv["ok"], gv)
        self.assertTrue(gv["sealed"])
        # and AGREES with the primary store's chain head
        pv = verify_chain(self.store.audit_records(res.run_id))
        self.assertEqual(gv["head"], pv["head"])

    def test_tampered_git_audit_record_is_detected(self):
        res = self._run()
        from moira_core.integrity import verify_export
        adir = Path(self.repo, ".moira-runs", res.run_id, "audit")
        victim = sorted(adir.glob("*.json"))[0]
        rec = json.loads(victim.read_text("utf-8"))
        rec["owner"] = "attacker"  # silent edit, hash left unchanged
        victim.write_text(json.dumps(rec), "utf-8")
        self.assertFalse(verify_export(self._git_audit_records(res.run_id))["ok"])


if __name__ == "__main__":
    unittest.main()
