"""Prompt & metering hygiene (QW9 + QW10 + QW13, ADR-018).

Three hardenings the research called for, made urgent by ST1 (we now inject
more uncontrolled text into prompts than ever):

- QW9: uncontrolled content (upstream outputs, failing check output, previous
  attempt errors) is explicitly framed as UNTRUSTED DATA — reference material,
  never instructions — and the shared SYSTEM contract states the rule
- QW10: a skill's support files (references/) are handed to the agent; a
  missing SKILL.md fails the node LOUDLY instead of silently degrading to a
  /slash line that headless claude ignores (a run must never claim success
  for a playbook that never executed)
- QW13: token counters weigh cache tokens the way they are billed
  (cache-read ×0.1, cache-creation ×1.25) instead of ignoring them
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core.backends import contract  # noqa: E402
from moira_core.backends.claude_code import ClaudeCodeBackend  # noqa: E402
from moira_core.models import Node, NodeType  # noqa: E402


class TestUntrustedFraming(unittest.TestCase):
    def test_system_contract_states_the_untrusted_rule(self):
        self.assertIn("UNTRUSTED", contract.SYSTEM)
        self.assertIn("instructions", contract.SYSTEM)

    def test_upstream_outputs_are_marked_untrusted(self):
        p = contract.build_stage_prompt(role="r", spec_ref="F", spec_text="s",
                                        upstream={"prev": {"note": "x"}})
        self.assertIn("UPSTREAM OUTPUTS [UNTRUSTED", p)

    def test_check_output_block_is_marked_untrusted(self):
        block = contract.check_output_block("FAIL test_x")
        self.assertIn("[UNTRUSTED", block)

    def test_attempt_errors_block_is_marked_untrusted(self):
        block = contract.attempt_errors_block(["boom"])
        self.assertIn("[UNTRUSTED", block)


def _skill_repo(with_references: bool) -> str:
    root = tempfile.mkdtemp(prefix="skillrepo-")
    d = Path(root) / ".agents" / "skills" / "ba@shape-intent-spec"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: x\n---\nDo the shaping.", encoding="utf-8")
    if with_references:
        (d / "references").mkdir()
        (d / "references" / "template.md").write_text("# T", encoding="utf-8")
    return root


class TestSkillDelivery(unittest.TestCase):
    def test_missing_skill_md_fails_the_node_loudly(self):
        cwd = tempfile.mkdtemp(prefix="noskills-")
        be = ClaudeCodeBackend()
        node = Node(id="a", name="s", type=NodeType.PRODUCER,
                    skill="ba@shape-intent-spec", skill_input="topic")
        res = be.run(node, {"cwd": cwd})
        self.assertFalse(res.ok)
        self.assertIn(".agents/skills/ba@shape-intent-spec/SKILL.md", res.error)

    def test_references_dir_is_handed_to_the_agent(self):
        cwd = _skill_repo(with_references=True)
        be = ClaudeCodeBackend()
        node = Node(id="a", name="s", type=NodeType.PRODUCER,
                    skill="ba@shape-intent-spec", skill_input="topic")
        p = be._build_prompt(node, {"cwd": cwd})
        self.assertIn("SKILL SUPPORT FILES", p)
        self.assertIn("references", p)
        self.assertIn(str(Path(cwd) / ".agents" / "skills" / "ba@shape-intent-spec"), p)

    def test_no_references_dir_no_section(self):
        cwd = _skill_repo(with_references=False)
        be = ClaudeCodeBackend()
        node = Node(id="a", name="s", type=NodeType.PRODUCER,
                    skill="ba@shape-intent-spec", skill_input="topic")
        p = be._build_prompt(node, {"cwd": cwd})
        self.assertNotIn("SKILL SUPPORT FILES", p)


class TestWeightedTokens(unittest.TestCase):
    def _stream(self, usage: dict) -> list[str]:
        import json as _j
        return [_j.dumps({"type": "result", "result": "done", "usage": usage})]

    def test_cache_tokens_are_weighted_not_ignored(self):
        final, tin, tout = ClaudeCodeBackend._reduce_stream(self._stream({
            "input_tokens": 100, "output_tokens": 50,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 10000,
        }))
        self.assertEqual(tout, 50)
        # 100 + 1.25*1000 + 0.1*10000 = 2350
        self.assertEqual(tin, 2350)

    def test_result_envelope_cost_uses_the_same_weighting(self):
        be = ClaudeCodeBackend()
        res = be._result_from_envelope({
            "result": "ok", "total_cost_usd": 0.1,
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "cache_creation_input_tokens": 1000,
                      "cache_read_input_tokens": 10000}})
        self.assertEqual(res.cost.tokens_in, 2350)
        self.assertEqual(res.cost.tokens_out, 50)

    def test_plain_usage_unchanged(self):
        final, tin, tout = ClaudeCodeBackend._reduce_stream(self._stream({
            "input_tokens": 100, "output_tokens": 50}))
        self.assertEqual((tin, tout), (100, 50))


if __name__ == "__main__":
    unittest.main()
