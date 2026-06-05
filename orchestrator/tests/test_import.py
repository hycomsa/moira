"""Agent importer (.md -> Moira YAML) + cross-model pipeline wiring."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from import_agents import convert_md, import_dir  # noqa: E402
from moira_core import BackendRegistry, Engine, Status, Store  # noqa: E402
from moira_core.backends.mock import MockBackend  # noqa: E402
from moira_core.repo_reader import AISdlcRepo  # noqa: E402

REVIEWER_MD = """---
name: code-reviewer
description: Reviews code for quality, security and best practices
tools: Read, Grep, Glob
model: opus
---
You are a meticulous code reviewer. Analyse the diff and report issues.
"""
CODER_MD = """---
name: backend-builder
description: Implements backend services
tools: Read, Write, Edit, Bash
---
You implement backend services per the spec.
"""


class TestConvert(unittest.TestCase):
    def test_reviewer_maps_to_verifier_reasoning(self):
        a = convert_md(REVIEWER_MD)
        self.assertEqual(a["id"], "code-reviewer")
        self.assertEqual(a["type"], "verifier")          # 'review' -> verifier
        self.assertEqual(a["tools_policy"], "reasoning")  # no Write/Edit/Bash
        self.assertEqual(a["model"], "opus")
        self.assertTrue(a["category"])  # category is a best-effort heuristic grouping
        self.assertIn("meticulous code reviewer", a["system_prompt"])

    def test_coder_maps_to_producer_coding(self):
        a = convert_md(CODER_MD)
        self.assertEqual(a["type"], "producer")
        self.assertEqual(a["tools_policy"], "coding")     # Write/Edit/Bash present
        self.assertEqual(a["category"], "implementation")

    def test_import_dir_roundtrip(self):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / ".ai" / "context").mkdir(parents=True)
        src = Path(tmp) / "src"; src.mkdir()
        (src / "code-reviewer.md").write_text(REVIEWER_MD, "utf-8")
        (src / "backend-builder.md").write_text(CODER_MD, "utf-8")
        ids = import_dir(tmp, str(src))
        self.assertEqual(set(ids), {"code-reviewer", "backend-builder"})
        listed = {a["id"] for a in AISdlcRepo(tmp).list_agents()}
        self.assertTrue({"code-reviewer", "backend-builder"}.issubset(listed))


class TestCrossModelPipeline(unittest.TestCase):
    def test_model_override_survives_and_runs(self):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / ".ai" / "context").mkdir(parents=True)
        repo = AISdlcRepo(tmp)
        repo.save_agent({"id": "coder", "name": "Coder", "type": "producer",
                         "role": "code-generator", "backend": "mock", "model": ""})
        repo.save_agent({"id": "judge", "name": "Judge", "type": "verifier",
                         "role": "code-quality", "backend": "mock", "model": "opus"})
        repo.save_pipeline_def({"id": "x", "name": "x", "nodes": [
            {"id": "implement", "agent": "coder"},
            {"id": "review", "agent": "judge", "depends_on": ["implement"]},
            {"id": "gate", "type": "gate", "depends_on": ["review"],
             "gate": {"mode": "auto", "consumes": ["review"]}},
        ]})
        pipe = repo.build_pipeline(repo.get_pipeline_def("x"))
        review = next(n for n in pipe.nodes if n.id == "review")
        self.assertEqual(review.model, "opus")  # cross-model override preserved
        store = Store(tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name)
        reg = BackendRegistry(); reg.register(MockBackend())
        res = Engine(store, reg, owner="t").start(pipe, {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED, msg=str(res))


if __name__ == "__main__":
    unittest.main(verbosity=2)
