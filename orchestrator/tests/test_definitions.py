"""Git-native agent + pipeline definitions: save -> read -> build -> run."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import BackendRegistry, Engine, Status, Store  # noqa: E402
from moira_core.backends.mock import MockBackend  # noqa: E402
from moira_core.repo_reader import AISdlcRepo  # noqa: E402


class TestDefinitions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = AISdlcRepo(self.tmp)
        (Path(self.tmp) / ".ai" / "context").mkdir(parents=True)

    def test_agent_save_read_roundtrip(self):
        self.repo.save_agent({"id": "analyst", "name": "Analyst", "type": "producer",
                              "category": "analysis", "role": "requirements-analyst",
                              "backend": "mock", "skill_refs": ["ba@shape-func-spec"]})
        got = self.repo.get_agent("analyst")
        self.assertEqual(got["name"], "Analyst")
        self.assertEqual(got["skill_refs"], ["ba@shape-func-spec"])
        self.assertIn("analyst", [a["id"] for a in self.repo.list_agents()])

    def test_pipeline_build_resolves_agents_and_runs(self):
        # seed two agents + a verifier
        self.repo.save_agent({"id": "analyst", "name": "Analyst", "type": "producer",
                              "role": "requirements-analyst", "backend": "mock"})
        self.repo.save_agent({"id": "coder", "name": "Coder", "type": "producer",
                              "role": "code-generator", "backend": "mock"})
        self.repo.save_agent({"id": "qa", "name": "QA", "type": "verifier",
                              "role": "code-quality", "backend": "mock"})
        self.repo.save_pipeline_def({
            "id": "mini", "name": "Mini", "nodes": [
                {"id": "analyze", "agent": "analyst"},
                {"id": "implement", "agent": "coder"},
                {"id": "qa", "agent": "qa"},
                {"id": "gate", "type": "gate",
                 "gate": {"mode": "auto", "consumes": ["qa"]}},
            ],
        })
        pdef = self.repo.get_pipeline_def("mini")
        pipe = self.repo.build_pipeline(pdef, func_ref="FUNC-X")
        # resolved correctly
        self.assertEqual(len(pipe.nodes), 4)
        self.assertEqual(pipe.nodes[0].role, "requirements-analyst")
        self.assertEqual(pipe.nodes[2].type.value, "verifier")
        self.assertEqual(pipe.nodes[3].type.value, "gate")
        # and it actually runs on the mock backend
        store = Store(tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name)
        reg = BackendRegistry(); reg.register(MockBackend())
        res = Engine(store, reg, owner="t").start(pipe, {"func_id": "F", "spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED, msg=str(res))

    def test_delete(self):
        self.repo.save_agent({"id": "tmp", "name": "Tmp"})
        self.assertTrue(self.repo.delete_agent("tmp"))
        self.assertIsNone(self.repo.get_agent("tmp"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
