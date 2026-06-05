"""LiteLLMBackend tests using a fake litellm (no network, deterministic).

Verifies: model routing via node.model, shared output contract parsing, cost
capture, and graceful error when a provider call fails — proving the
model-agnostic path end-to-end through the engine without any real API.
"""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core.backends import contract  # noqa: E402
import moira_core.backends.litellm_backend as lb  # noqa: E402
from moira_core.models import Node, NodeType  # noqa: E402


def make_fake_litellm(content: str, fail: bool = False):
    """Build a fake `litellm` module object."""
    fake = types.SimpleNamespace()

    class Usage:
        prompt_tokens = 1234
        completion_tokens = 567

    class Msg:
        def __init__(self, c): self.content = c

    class Choice:
        def __init__(self, c): self.message = Msg(c)

    class Resp:
        def __init__(self, c):
            self.choices = [Choice(c)]
            self.usage = Usage()

    def completion(model, messages, **kw):
        if fail:
            raise RuntimeError("simulated provider auth failure")
        return Resp(content)

    fake.completion = completion
    fake.completion_cost = lambda completion_response=None: 0.0123
    return fake


class TestLiteLLMBackend(unittest.TestCase):
    def _node(self, model="gpt-4o"):
        return Node(id="analyze", name="A", type=NodeType.PRODUCER,
                    role="requirements-analyst", backend="litellm", model=model)

    def test_parses_contract_and_cost(self):
        content = (
            "Did the analysis.\n"
            f"{contract.START}\n"
            '{"output": {"summary": "ok"}, "decisions": ["chose A"], "tools_used": ["reason"]}\n'
            f"{contract.END}"
        )
        lb.litellm = None  # reset; set below
        lb.litellm = make_fake_litellm(content)
        be = lb.LiteLLMBackend()
        res = be.run(self._node(), {"spec_text": "spec", "upstream": {}})
        self.assertTrue(res.ok)
        self.assertEqual(res.decisions, ["chose A"])
        self.assertEqual(res.output["summary"], "ok")
        self.assertEqual(res.cost.tokens_in, 1234)
        self.assertEqual(res.cost.tokens_out, 567)
        self.assertAlmostEqual(res.cost.usd, 0.0123, places=4)

    def test_graceful_provider_error(self):
        lb.litellm = None  # reset; set below
        lb.litellm = make_fake_litellm("", fail=True)
        be = lb.LiteLLMBackend()
        res = be.run(self._node("ollama/llama3"), {"spec_text": "s", "upstream": {}})
        self.assertFalse(res.ok)
        self.assertIn("simulated provider auth failure", res.error)

    def test_unavailable_when_not_installed(self):
        lb.litellm = False  # sentinel: tried, unavailable
        be = lb.LiteLLMBackend()
        self.assertFalse(be.available())
        res = be.run(self._node(), {"spec_text": "s", "upstream": {}})
        self.assertFalse(res.ok)
        self.assertIn("not installed", res.error)

    def test_model_routing_uses_node_model(self):
        captured = {}
        content = f'{contract.START}{{"output":{{}},"decisions":[]}}{contract.END}'
        fake = make_fake_litellm(content)
        orig = fake.completion
        def spy(model, messages, **kw):
            captured["model"] = model
            return orig(model, messages, **kw)
        fake.completion = spy
        lb.litellm = None  # reset; set below
        lb.litellm = fake
        lb.LiteLLMBackend().run(self._node("ollama/qwen2.5-coder"),
                                {"spec_text": "s", "upstream": {}})
        self.assertEqual(captured["model"], "ollama/qwen2.5-coder")


if __name__ == "__main__":
    unittest.main(verbosity=2)
