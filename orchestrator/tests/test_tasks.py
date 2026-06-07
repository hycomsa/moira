"""Unit tests for deterministic FUNC completeness from the Zdzira-compatible task backlog."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import tasks  # noqa: E402
from moira_core.repo_reader import AISdlcRepo  # noqa: E402


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestParsing(unittest.TestCase):
    def test_func_slug(self):
        self.assertEqual(tasks.func_slug("FUNC-APP-onboarding"), "func-app-onboarding")

    def test_frontmatter_flow_list_and_scalars(self):
        fm = tasks._frontmatter(
            "---\n"
            "id: PROJ-1\n"
            "status: done\n"
            "acceptance_criteria: [AC-APP-01-01, AC-APP-01-02]\n"
            "jira:\n"
            "  issue_key: TEST-1\n"   # nested — must be ignored
            "---\nbody\n")
        self.assertEqual(fm["id"], "PROJ-1")
        self.assertEqual(fm["status"], "done")
        self.assertEqual(fm["acceptance_criteria"], ["AC-APP-01-01", "AC-APP-01-02"])
        self.assertNotIn("issue_key", fm)   # nested key not promoted


class TestCompleteness(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        fs = self.root / ".ai" / "context" / "func-specs" / "FUNC-APP-x"
        _write(fs / "func-spec.md",
               "# FUNC-APP-x\n> Requirement: REQ-APP-03\n"
               "**AC-APP-01-01:** a\n**AC-APP-01-02:** b\n**AC-APP-02-01:** c\n")
        _write(fs / "test-plan.md",
               "# Test plan\nCovers AC-APP-01-01 and AC-APP-02-01.\n")
        bl = self.root / "backlog" / "func-app-x"
        _write(bl / "epic-func-app-x.md",
               "---\nid: epic-func-app-x\ntype: epic\nstatus: todo\nsource_func: FUNC-APP-x\n---\n# epic\n")
        _write(bl / "PROJ-1.md",
               "---\nid: PROJ-1\ntype: task\nstatus: done\nparent_epic_id: epic-func-app-x\n"
               "acceptance_criteria: [AC-APP-01-01, AC-APP-01-02]\n---\n# t1\n")
        _write(bl / "PROJ-2.md",
               "---\nid: PROJ-2\ntype: task\nstatus: todo\nparent_epic_id: epic-func-app-x\n"
               "acceptance_criteria: [AC-APP-02-01]\n---\n# t2\n")
        self.repo = AISdlcRepo(self.root)

    def test_epic_dir_resolved_by_source_func(self):
        d = tasks.epic_dir_for_func(self.repo, "FUNC-APP-x")
        self.assertIsNotNone(d)
        self.assertEqual(d.name, "func-app-x")

    def test_completeness_counts(self):
        c = tasks.completeness(self.repo, "FUNC-APP-x")
        self.assertEqual(c["tasks"], {"total": 2, "done": 1, "by_status": {"done": 1, "todo": 1}})
        self.assertEqual(c["ac"]["total"], 3)
        self.assertEqual(c["ac"]["in_tasks"], 3)   # all 3 ACs appear in some task
        self.assertEqual(c["ac"]["done"], 2)       # only PROJ-1's two ACs are done
        self.assertEqual(c["ac"]["tested"], 2)     # test-plan covers 2 of 3
        self.assertEqual(c["build_pct"], 0.5)
        self.assertEqual(c["level"], "partial")
        self.assertTrue(c["has_epic"])

    def test_complete_when_all_done(self):
        # flip PROJ-2 to closed -> every task done AND every AC covered+done
        (self.root / "backlog" / "func-app-x" / "PROJ-2.md").write_text(
            "---\nid: PROJ-2\ntype: task\nstatus: closed\nparent_epic_id: epic-func-app-x\n"
            "acceptance_criteria: [AC-APP-02-01]\n---\n# t2\n", encoding="utf-8")
        c = tasks.completeness(self.repo, "FUNC-APP-x")
        self.assertEqual(c["build_pct"], 1.0)
        self.assertEqual(c["level"], "complete")

    def test_no_backlog_is_none_level(self):
        c = tasks.completeness(self.repo, "FUNC-MISSING")
        self.assertEqual(c["tasks"]["total"], 0)
        self.assertEqual(c["level"], "none")
        self.assertFalse(c["has_epic"])

    def test_traceability_assembly(self):
        t = tasks.traceability(self.repo, "FUNC-APP-x", lineage=["FUNC-APP-x", "REQ-APP-03"])
        self.assertTrue(t["spec"]["present"])
        self.assertTrue(t["tests"]["present"])
        self.assertEqual(t["tests"]["ac_covered"], 2)
        self.assertEqual(t["tests"]["ac_total"], 3)
        self.assertIn("REQ-APP-03", t["lineage"]["refs"])
        self.assertEqual(t["tasks"]["build_pct"], 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
