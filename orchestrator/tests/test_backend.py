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

    def test_heavy_role_gets_task_tracking_directive(self):
        # any coding role (incl superpowers-coder) is told to flip task status + commit {TASK-ID}
        for role in ("code-generator", "superpowers-coder"):
            cmd = self._cmd(role, env=None)
            sys_prompt = cmd[cmd.index("--append-system-prompt") + 1]
            self.assertIn("TASK TRACKING", sys_prompt)
            self.assertIn("status:", sys_prompt)

    def test_light_role_no_task_tracking(self):
        cmd = self._cmd("requirements-analyst", env=None)
        self.assertNotIn("TASK TRACKING", cmd[cmd.index("--append-system-prompt") + 1])

    def test_backlog_dir_injected_for_coding_role(self):
        from moira_core.models import Node, NodeType
        node = Node(id="impl", name="impl", type=NodeType.PRODUCER, backend="claude_code",
                    role="superpowers-coder", spec_ref="FUNC-X")
        cmd = B()._build_cmd(node, {"spec_text": "s", "backlog_dir": "/ba/backlog"})
        sys_prompt = cmd[cmd.index("--append-system-prompt") + 1]
        self.assertIn("/ba/backlog", sys_prompt)         # told where the (separate) backlog lives
        self.assertIn("SEPARATE", sys_prompt)


class TestBudgetTiers(unittest.TestCase):
    """skill / heavy / default budgets pick the right --max-turns."""

    def _turns(self, **node_kw):
        from moira_core.models import Node, NodeType
        b = B(max_turns=12, heavy_max_turns=40, skill_max_turns=7)
        node = Node(id="n", name="n", type=NodeType.PRODUCER, backend="claude_code", **node_kw)
        cmd = b._build_cmd(node, {"spec_text": "x"})
        return cmd[cmd.index("--max-turns") + 1]

    def test_skill_node_uses_skill_budget(self):
        self.assertEqual(self._turns(role="ba-skill", skill="ba@shape-intent-spec"), "7")

    def test_heavy_role_uses_heavy_budget(self):
        self.assertEqual(self._turns(role="superpowers-coder"), "40")

    def test_default_role_uses_default_budget(self):
        self.assertEqual(self._turns(role="requirements-analyst"), "12")

    def test_env_defaults(self):
        import os
        os.environ["MOIRA_CLAUDE_SKILL_TIMEOUT"] = "111"
        try:
            self.assertEqual(B().skill_timeout, 111)
        finally:
            del os.environ["MOIRA_CLAUDE_SKILL_TIMEOUT"]


class TestSkillInlining(unittest.TestCase):
    """skill prompt inlines SKILL.md as a task (not a /slash invocation)."""

    def _prompt(self, cwd):
        from moira_core.models import Node, NodeType
        node = Node(id="a", name="ba@x", type=NodeType.PRODUCER, backend="claude_code",
                    role="ba-skill", skill="ba@shape-intent-spec", skill_input="driver onboarding")
        return B()._build_prompt(node, {"cwd": cwd})

    def test_inlines_playbook_when_skill_md_present(self):
        import tempfile, os, pathlib
        d = tempfile.mkdtemp()
        sk = pathlib.Path(d, ".agents", "skills", "ba@shape-intent-spec")
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("---\nname: ba@shape-intent-spec\n---\n# Shape Intent\nDo the steps.\n")
        p = self._prompt(d)
        self.assertIn("Follow its playbook", p)
        self.assertIn("Do the steps.", p)            # body inlined
        self.assertNotIn("name: ba@shape-intent-spec", p)  # frontmatter stripped
        self.assertIn("driver onboarding", p)         # input
        self.assertIn("non-interactive", p)           # execution directive
        self.assertNotIn("/ba@shape-intent-spec", p)  # NOT a slash invocation

    def test_falls_back_to_slash_when_missing(self):
        p = self._prompt("/nonexistent")
        self.assertIn("/ba@shape-intent-spec driver onboarding", p)


class TestStreamReduce(unittest.TestCase):
    """stream-json NDJSON → live records + final result envelope."""

    SAMPLE = [
        '{"type":"system","subtype":"init","session_id":"x"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Analyzing the spec…"}],"usage":{"input_tokens":1200,"output_tokens":40}}}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file":"a.ts"}}],"usage":{"input_tokens":1200,"output_tokens":60}}}',
        '',  # blank line tolerated
        'not json — ignored',
        '{"type":"result","subtype":"success","result":"done","usage":{"input_tokens":1500,"output_tokens":300},"total_cost_usd":0.12}',
    ]

    def test_reduce_emits_records_and_final(self):
        recs = []
        final, tin, tout = B._reduce_stream(self.SAMPLE, on_record=lambda r, i, o: recs.append((r, i, o)))
        kinds = [r["kind"] for r, _, _ in recs]
        self.assertEqual(kinds, ["assistant", "tool", "result"])
        self.assertIn("Analyzing", recs[0][0]["text"])
        self.assertTrue(recs[1][0]["text"].startswith("Read"))
        self.assertIsNotNone(final)
        self.assertEqual(tin, 1500)            # final usage wins
        self.assertEqual(tout, 300)
        # tokens stream upward on each emitted record
        self.assertEqual(recs[0][2], 40)       # output tokens after first assistant msg

    def test_final_envelope_parses_like_json_mode(self):
        final, _, _ = B._reduce_stream(self.SAMPLE)
        res = B()._result_from_envelope(final)
        self.assertTrue(res.ok)
        self.assertEqual(res.cost.tokens_in, 1500)
        self.assertEqual(res.cost.usd, 0.12)
        self.assertEqual(res.output.get("result"), "done")

    def test_no_final_returns_none(self):
        final, _, _ = B._reduce_stream(['{"type":"assistant","message":{"content":[]}}'])
        self.assertIsNone(final)


class TestDebugLogging(unittest.TestCase):
    """MOIRA_DEBUG=1 records the exact command/prompt as a live `debug` record."""

    def _run(self, debug_env, returncode=0, stdout_lines=None):
        import io, tempfile, pathlib, json as _json
        from unittest import mock
        from moira_core.models import Node, NodeType
        d = tempfile.mkdtemp()
        live = str(pathlib.Path(d, "live.jsonl"))
        node = Node(id="impl", name="impl", type=NodeType.PRODUCER,
                    backend="claude_code", role="requirements-analyst", spec_ref="FUNC-X")

        class _Proc:
            def __init__(self):
                self.stdout = iter(stdout_lines or [])
                self.stderr = io.StringIO("boom" if returncode else "")
                self.returncode = returncode

            def wait(self):
                return self.returncode

        old = os.environ.get("MOIRA_DEBUG")
        if debug_env is None:
            os.environ.pop("MOIRA_DEBUG", None)
        else:
            os.environ["MOIRA_DEBUG"] = debug_env
        try:
            with mock.patch("subprocess.Popen", return_value=_Proc()), \
                 mock.patch("shutil.which", return_value="/usr/bin/claude"):
                B().run(node, {"spec_text": "spec", "live_path": live, "cwd": d})
        finally:
            if old is None:
                os.environ.pop("MOIRA_DEBUG", None)
            else:
                os.environ["MOIRA_DEBUG"] = old
        if not os.path.exists(live):
            return []
        with open(live, encoding="utf-8") as f:
            return [_json.loads(ln) for ln in f]

    def test_debug_records_command(self):
        recs = self._run("1")
        self.assertTrue(recs and recs[0]["kind"] == "debug")
        self.assertIn("$ ", recs[0]["text"])         # rendered command
        self.assertIn("timeout=", recs[0]["text"])    # chosen budget annotated

    def test_no_debug_record_without_env(self):
        recs = self._run(None)
        self.assertFalse(any(r.get("kind") == "debug" for r in recs))

    def test_debug_records_failure(self):
        recs = self._run("1", returncode=2)  # no result envelope -> failure path
        self.assertTrue(any(r["kind"] == "debug" and "FAILED" in r["text"] for r in recs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
