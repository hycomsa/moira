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


class TestEffortField(unittest.TestCase):
    """`effort` is a first-class field resolved like `model`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = AISdlcRepo(self.tmp)
        (Path(self.tmp) / ".ai" / "context").mkdir(parents=True)

    def test_node_and_agentdef_default_effort_empty(self):
        from moira_core.models import Node, NodeType, AgentDefinition
        n = Node(id="n", name="n", type=NodeType.PRODUCER)
        self.assertEqual(n.effort, "")
        self.assertEqual(n.to_dict()["effort"], "")
        self.assertEqual(Node.from_dict(n.to_dict()).effort, "")
        self.assertEqual(AgentDefinition(id="a", name="A").effort, "")

    def test_agentdef_effort_roundtrips_through_repo(self):
        self.repo.save_agent({"id": "analyst", "name": "Analyst",
                              "role": "requirements-analyst", "backend": "claude_code",
                              "effort": "high"})
        self.assertEqual(self.repo.get_agent("analyst")["effort"], "high")

    def test_build_pipeline_inherits_agent_effort(self):
        self.repo.save_agent({"id": "coder", "name": "Coder", "role": "code-generator",
                              "backend": "claude_code", "effort": "xhigh"})
        self.repo.save_pipeline_def({"id": "p", "name": "P", "nodes": [
            {"id": "implement", "agent": "coder"}]})
        pipe = self.repo.build_pipeline(self.repo.get_pipeline_def("p"), func_ref="FUNC-X")
        self.assertEqual(pipe.nodes[0].effort, "xhigh")

    def test_build_pipeline_node_effort_override_wins(self):
        self.repo.save_agent({"id": "coder", "name": "Coder", "role": "code-generator",
                              "backend": "claude_code", "effort": "xhigh"})
        self.repo.save_pipeline_def({"id": "p", "name": "P", "nodes": [
            {"id": "implement", "agent": "coder", "effort": "low"}]})
        pipe = self.repo.build_pipeline(self.repo.get_pipeline_def("p"), func_ref="FUNC-X")
        self.assertEqual(pipe.nodes[0].effort, "low")   # node YAML key overrides agent default

    def test_load_pipeline_run_override_applies_effort(self):
        from moira_api import load_pipeline
        store = Store(tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name)
        # no pipeline_id -> default pipeline; run-body effort applies to non-gate nodes
        pipe = load_pipeline(store, "default", {"effort": "high"}, "FUNC-X")
        non_gate = [n for n in pipe.nodes if n.type.value != "gate"]
        self.assertTrue(non_gate)
        self.assertTrue(all(n.effort == "high" for n in non_gate))
        # gates carry no effort
        self.assertTrue(all(n.effort == "" for n in pipe.nodes if n.type.value == "gate"))


class TestAgentSystemPromptField(unittest.TestCase):
    """`system_prompt` defined on an agent flows into the Node (resolved like model/effort)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = AISdlcRepo(self.tmp)
        (Path(self.tmp) / ".ai" / "context").mkdir(parents=True)

    def test_node_default_empty_and_roundtrips(self):
        from moira_core.models import Node, NodeType
        self.assertEqual(Node(id="n", name="n", type=NodeType.PRODUCER).system_prompt, "")
        n = Node(id="n", name="n", type=NodeType.PRODUCER, system_prompt="X")
        self.assertEqual(n.to_dict()["system_prompt"], "X")
        self.assertEqual(Node.from_dict(n.to_dict()).system_prompt, "X")

    def test_build_pipeline_inherits_agent_system_prompt(self):
        self.repo.save_agent({"id": "coder", "name": "Coder", "role": "code-generator",
                              "backend": "claude_code", "system_prompt": "Follow the house style."})
        self.repo.save_pipeline_def({"id": "p", "name": "P", "nodes": [
            {"id": "implement", "agent": "coder"}]})
        pipe = self.repo.build_pipeline(self.repo.get_pipeline_def("p"), func_ref="F")
        self.assertEqual(pipe.nodes[0].system_prompt, "Follow the house style.")

    def test_node_key_overrides_agent_system_prompt(self):
        self.repo.save_agent({"id": "coder", "name": "Coder", "role": "code-generator",
                              "backend": "claude_code", "system_prompt": "agent default"})
        self.repo.save_pipeline_def({"id": "p", "name": "P", "nodes": [
            {"id": "implement", "agent": "coder", "system_prompt": "node override"}]})
        pipe = self.repo.build_pipeline(self.repo.get_pipeline_def("p"), func_ref="F")
        self.assertEqual(pipe.nodes[0].system_prompt, "node override")


if __name__ == "__main__":
    unittest.main(verbosity=2)
