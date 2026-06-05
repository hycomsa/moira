"""Unit tests for ClaudeCodeBackend output-contract parsing (no API needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core.backends.claude_code import ClaudeCodeBackend  # noqa: E402

B = ClaudeCodeBackend


class TestContractParsing(unittest.TestCase):
    def test_sentinel_markers(self):
        text = (
            "Here is my analysis. Lots of prose.\n"
            f"{B.START}\n"
            '{"output": {"summary": "ok"}, "decisions": ["chose X"], "tools_used": ["read"]}\n'
            f"{B.END}\n"
        )
        obj = ClaudeCodeBackend()._extract_contract(text)
        self.assertEqual(obj["decisions"], ["chose X"])
        self.assertEqual(obj["output"]["summary"], "ok")

    def test_sentinel_with_code_fence(self):
        text = (
            f"{B.START}\n```json\n"
            '{"output": {}, "decisions": ["d"]}\n```\n'
            f"{B.END}"
        )
        obj = ClaudeCodeBackend()._extract_contract(text)
        self.assertEqual(obj["decisions"], ["d"])

    def test_balanced_brace_fallback(self):
        text = (
            "no markers here, but a json object at the end with nested braces\n"
            '{"output": {"files": [{"path": "a.py"}]}, "decisions": ["x"]}'
        )
        obj = ClaudeCodeBackend()._extract_contract(text)
        self.assertEqual(obj["decisions"], ["x"])
        self.assertEqual(obj["output"]["files"][0]["path"], "a.py")

    def test_raw_fallback_on_pure_prose(self):
        text = "I explored the repo and it looks largely implemented. No JSON here."
        obj = ClaudeCodeBackend()._extract_contract(text)
        self.assertIn("raw", obj)

    def test_ignores_non_contract_braces(self):
        # a stray object without output/decisions should be skipped in favor of raw
        text = "config was {\"a\": 1} but no contract object"
        obj = ClaudeCodeBackend()._extract_contract(text)
        self.assertIn("raw", obj)


class TestSuperpowersWiring(unittest.TestCase):
    """Role-gated Superpowers (--plugin-dir) + heavy turn budget + autonomy nudge."""

    def _cmd(self, role, env=None):
        from moira_core.models import Node, NodeType
        old = os.environ.get("MOIRA_SUPERPOWERS_DIR")
        if env is None:
            os.environ.pop("MOIRA_SUPERPOWERS_DIR", None)
        else:
            os.environ["MOIRA_SUPERPOWERS_DIR"] = env
        try:
            node = Node(id="impl", name="impl", type=NodeType.PRODUCER,
                        backend="claude_code", role=role, spec_ref="FUNC-X")
            return B()._build_cmd(node, {"spec_text": "spec"})
        finally:
            if old is None:
                os.environ.pop("MOIRA_SUPERPOWERS_DIR", None)
            else:
                os.environ["MOIRA_SUPERPOWERS_DIR"] = old

    def test_superpowers_role_loads_plugin_dir_when_env_set(self):
        cmd = self._cmd("superpowers-coder", env="/plugins/superpowers")
        self.assertIn("--plugin-dir", cmd)
        self.assertIn("/plugins/superpowers", cmd)
        # heavy turn budget
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], str(B().heavy_max_turns))
        # autonomy nudge appended to the system prompt
        sys_prompt = cmd[cmd.index("--append-system-prompt") + 1]
        self.assertIn("autonomously", sys_prompt)

    def test_superpowers_role_noop_without_env(self):
        cmd = self._cmd("superpowers-coder", env=None)
        self.assertNotIn("--plugin-dir", cmd)  # opt-in: no env → behaviour unchanged

    def test_other_roles_never_get_plugin_dir(self):
        cmd = self._cmd("code-generator", env="/plugins/superpowers")
        self.assertNotIn("--plugin-dir", cmd)  # only the opt-in role
        # but code-generator is still "heavy" → bigger budget, no Superpowers
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], str(B().heavy_max_turns))

    def test_light_role_keeps_small_budget_and_no_autonomy(self):
        cmd = self._cmd("requirements-analyst", env="/plugins/superpowers")
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], str(B().max_turns))
        self.assertNotIn("autonomously", cmd[cmd.index("--append-system-prompt") + 1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
