"""Discovery refinements: artifact-id derivation + auto-chaining between skills."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import BackendRegistry, Engine, Store  # noqa: E402
from moira_core import gitdiff  # noqa: E402
from moira_core.backends.claude_code import ClaudeCodeBackend  # noqa: E402
from moira_core.models import BackendResult, Cost, Node, NodeType, Pipeline  # noqa: E402


class TestArtifactId(unittest.TestCase):
    def test_derives_ids_from_paths(self):
        f = lambda p: [{"path": p}]  # noqa: E731
        self.assertEqual(gitdiff.artifact_id(f(".ai/context/intent-specs/INT-APP-x/intent.md")), "INT-APP-x")
        self.assertEqual(gitdiff.artifact_id(f(".ai/context/func-specs/FUNC-APP-onboarding/func-spec.md")), "FUNC-APP-onboarding")
        self.assertEqual(gitdiff.artifact_id(f(".ai/context/adrs/ADR-005-x.md")), "ADR-005-x")
        self.assertIsNone(gitdiff.artifact_id(f("src/foo.ts")))


class TestSkillPromptInheritance(unittest.TestCase):
    def test_empty_input_inherits_produced_artifact(self):
        be = ClaudeCodeBackend()
        node = Node(id="a1", name="s", type=NodeType.PRODUCER, skill="ba@shape-func-spec", skill_input="")
        prompt = be._build_prompt(node, {"produced_artifact": "REQ-APP-03"})
        self.assertIn("/ba@shape-func-spec REQ-APP-03", prompt)

    def test_explicit_input_wins(self):
        be = ClaudeCodeBackend()
        node = Node(id="a0", name="s", type=NodeType.PRODUCER, skill="ba@shape-intent-spec", skill_input="driver onboarding")
        prompt = be._build_prompt(node, {"produced_artifact": "REQ-APP-03"})
        self.assertIn("/ba@shape-intent-spec driver onboarding", prompt)


class _WriterBackend:
    """Fake skill backend: writes an artifact file into cwd (no real CLI)."""
    name = "fakeskill"
    def available(self): return True
    def run(self, node, context):
        d = Path(context["cwd"]) / ".ai" / "context" / "func-specs" / "FUNC-T"
        d.mkdir(parents=True, exist_ok=True)
        (d / "func-spec.md").write_text("# FUNC-T\nshaped\n", "utf-8")
        return BackendResult(output={"summary": "shaped"}, tools_used=["Write"], cost=Cost(), ok=True)


class TestEnginePropagation(unittest.TestCase):
    def test_producer_records_and_propagates_artifact(self):
        repo = tempfile.mkdtemp()
        subprocess.run(["git", "-C", repo, "init", "-q"], check=True)
        Path(repo, "seed.txt").write_text("x\n", "utf-8")
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], check=True)

        db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); db.close()
        store = Store(db.name)
        reg = BackendRegistry(); reg.register(_WriterBackend())
        eng = Engine(store, reg, owner="t")
        node = Node(id="author0", name="shape", type=NodeType.PRODUCER, backend="fakeskill", skill="ba@shape-func-spec")
        res = eng.start(Pipeline(id="p", name="P", nodes=[node]), {"cwd": repo, "lineage": []})
        rec = [a for a in store.audit_records(res.run_id) if a["node_id"] == "author0"][0]
        # derived + attached the authored artifact id (drives the Inbox "Authored" chip)
        self.assertEqual(rec["output"].get("artifact"), "FUNC-T")
        self.assertTrue(any(fch["path"].endswith("FUNC-T/func-spec.md") for fch in rec["output"]["files"]))
        store.close()


if __name__ == "__main__":
    unittest.main()
