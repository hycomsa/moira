"""gitdiff — side-effect-free per-step file-change capture."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import gitdiff  # noqa: E402


class TestGitDiff(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "-C", self.repo, "init", "-q"], check=True)
        Path(self.repo, "existing.txt").write_text("hello\n", "utf-8")
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                        "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "-m", "base"], check=True)

    def test_not_a_git_repo(self):
        self.assertFalse(gitdiff.is_git_repo(tempfile.mkdtemp()))
        self.assertIsNone(gitdiff.tree_snapshot(tempfile.mkdtemp()))

    def test_no_change_no_diff(self):
        before = gitdiff.tree_snapshot(self.repo)
        after = gitdiff.tree_snapshot(self.repo)
        self.assertEqual(before, after)
        self.assertIsNone(gitdiff.changes_in(self.repo, before, after))

    def test_captures_added_and_modified(self):
        before = gitdiff.tree_snapshot(self.repo)
        Path(self.repo, "src").mkdir()
        Path(self.repo, "src", "new.ts").write_text("export const x = 1;\n", "utf-8")
        Path(self.repo, "existing.txt").write_text("hello\nworld\n", "utf-8")
        after = gitdiff.tree_snapshot(self.repo)

        changes = gitdiff.changes_in(self.repo, before, after)
        self.assertIsNotNone(changes)
        paths = {f["path"]: f for f in changes["files"]}
        self.assertIn("src/new.ts", paths)
        self.assertIn("existing.txt", paths)
        self.assertEqual(paths["src/new.ts"]["status"], "A")
        self.assertEqual(paths["existing.txt"]["status"], "M")
        self.assertEqual(paths["src/new.ts"]["additions"], 1)
        self.assertIn("export const x = 1;", changes["patch"])
        self.assertFalse(changes["truncated"])

    def test_respects_gitignore(self):
        Path(self.repo, ".gitignore").write_text("node_modules/\n", "utf-8")
        before = gitdiff.tree_snapshot(self.repo)
        Path(self.repo, "node_modules").mkdir()
        Path(self.repo, "node_modules", "junk.js").write_text("x\n", "utf-8")
        Path(self.repo, "kept.txt").write_text("k\n", "utf-8")
        after = gitdiff.tree_snapshot(self.repo)
        changes = gitdiff.changes_in(self.repo, before, after)
        paths = {f["path"] for f in changes["files"]}
        self.assertIn("kept.txt", paths)
        self.assertNotIn("node_modules/junk.js", paths)  # ignored, excluded

    def test_artifact_id_from_changes_picks_new_req(self):
        # path-derived id wins when present
        ch = {"files": [{"path": ".ai/context/func-specs/FUNC-APP-x/spec.md"}], "patch": ""}
        self.assertEqual(gitdiff.artifact_id_from_changes(ch), "FUNC-APP-x")
        # otherwise a requirements change yields the first new REQ-ID from the patch
        ch = {"files": [{"path": ".ai/context/requirements/APP/index.md"}],
              "patch": "@@ -1 +1,3 @@\n context\n+### REQ-APP-12 — track & trace\n+more\n"}
        self.assertEqual(gitdiff.artifact_id_from_changes(ch), "REQ-APP-12")
        # no recognizable artifact -> None
        self.assertIsNone(gitdiff.artifact_id_from_changes({"files": [{"path": "src/x.ts"}], "patch": "+foo"}))

    def test_real_index_untouched(self):
        gitdiff.tree_snapshot(self.repo)  # uses a throwaway index
        Path(self.repo, "staged_nothing.txt").write_text("x\n", "utf-8")
        gitdiff.tree_snapshot(self.repo)
        # the repo's real index must still be clean (our temp index never bled in)
        out = subprocess.run(["git", "-C", self.repo, "diff", "--cached", "--name-only"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
