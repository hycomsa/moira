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


if __name__ == "__main__":
    unittest.main(verbosity=2)
