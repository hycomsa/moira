"""Round-trip tests for the minimal YAML I/O (controlled subset)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import yamlio  # noqa: E402


class TestYamlIO(unittest.TestCase):
    def _round(self, data):
        return yamlio.load(yamlio.dump(data))

    def test_agent_schema(self):
        agent = {
            "id": "requirements-analyst",
            "name": "Requirements Analyst",
            "type": "producer",
            "category": "analysis",
            "role": "requirements-analyst",
            "backend": "mock",
            "model": "",
            "description": "Parses a FUNC spec into structured, testable requirements.",
            "tools_policy": "reasoning",
            "system_prompt": "Be concise.\nReturn JSON.",
            "skill_refs": ["ba@shape-func-spec", "ba@discover-requirements"],
        }
        out = self._round(agent)
        self.assertEqual(out["id"], "requirements-analyst")
        self.assertEqual(out["name"], "Requirements Analyst")
        self.assertEqual(out["skill_refs"], ["ba@shape-func-spec", "ba@discover-requirements"])
        self.assertEqual(out["system_prompt"], "Be concise.\nReturn JSON.")
        self.assertEqual(out["model"], "")

    def test_empty_list_and_scalars(self):
        out = self._round({"skill_refs": [], "max": 3, "ok": True, "x": None})
        self.assertEqual(out["skill_refs"], [])
        self.assertEqual(out["max"], 3)
        self.assertIs(out["ok"], True)

    def test_pipeline_schema_with_gates(self):
        pipe = {
            "id": "sdlc-client-gated",
            "name": "SDLC + Client Gate",
            "nodes": [
                {"id": "analyze", "agent": "requirements-analyst", "spec_ref": "", "max_retries": 2},
                {"id": "gate-client", "type": "gate",
                 "gate": {"mode": "human", "persona": "client",
                          "reviews": ["analyze"], "audience": "client"},
                 "on_reject_goto": "analyze"},
                {"id": "implement", "agent": "code-generator", "max_retries": 2},
            ],
        }
        out = self._round(pipe)
        self.assertEqual(out["id"], "sdlc-client-gated")
        self.assertEqual(len(out["nodes"]), 3)
        n0, n1, n2 = out["nodes"]
        self.assertEqual(n0["agent"], "requirements-analyst")
        self.assertEqual(n0["max_retries"], 2)
        self.assertEqual(n1["type"], "gate")
        self.assertEqual(n1["gate"]["mode"], "human")
        self.assertEqual(n1["gate"]["reviews"], ["analyze"])
        self.assertEqual(n1["on_reject_goto"], "analyze")
        self.assertEqual(n2["agent"], "code-generator")

    def test_ignores_comments_and_blanks(self):
        text = "# a comment\nid: x\n\nname: Y\n"
        out = yamlio.load(text)
        self.assertEqual(out["id"], "x")
        self.assertEqual(out["name"], "Y")


if __name__ == "__main__":
    unittest.main(verbosity=2)
