"""QW6 — fail-loud model identity (no silent model substitution).

LiteLLMBackend used to silently fall back to `gpt-4o-mini` when a node had no
model (or the Node default "mock") — so the audit's `input.model` said
"(default)"/"mock" while a different model did the work and incurred the cost.
For a product whose value is the audit, that is a lie in the record. These
tests pin the new contract:

- litellm refuses to run without an explicit model — a loud, actionable error
- the refusal happens before any provider call is attempted
- validate_pipeline rejects a litellm node without an explicit model at
  save/launch time (fail at configuration, not mid-run)
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moira_core.backends.litellm_backend as lb  # noqa: E402
from moira_core.models import (  # noqa: E402
    GateConfig, GateMode, Node, NodeType, Pipeline,
)
from moira_core.validation import validate_pipeline  # noqa: E402


def _exploding_litellm():
    fake = types.SimpleNamespace()
    def completion(*a, **kw):
        raise AssertionError("provider must not be called without an explicit model")
    fake.completion = completion
    fake.completion_cost = lambda **kw: 0.0
    return fake


class TestLiteLLMFailLoud(unittest.TestCase):
    def _node(self, model):
        return Node(id="analyze", name="A", type=NodeType.PRODUCER,
                    role="requirements-analyst", backend="litellm", model=model)

    def test_empty_model_refuses_loudly_without_calling_provider(self):
        lb.litellm = _exploding_litellm()
        res = lb.LiteLLMBackend().run(self._node(""), {"spec_text": "s", "upstream": {}})
        self.assertFalse(res.ok)
        self.assertIn("explicit model", res.error)

    def test_mock_model_refuses_loudly(self):
        lb.litellm = _exploding_litellm()
        res = lb.LiteLLMBackend().run(self._node("mock"), {"spec_text": "s", "upstream": {}})
        self.assertFalse(res.ok)
        self.assertIn("explicit model", res.error)

    def test_no_silent_default_attribute_left(self):
        # the silent gpt-4o-mini fallback is gone from the backend entirely
        self.assertFalse(hasattr(lb.LiteLLMBackend(), "default_model"))


class TestPipelineValidation(unittest.TestCase):
    def _pipe(self, model, backend="litellm"):
        return Pipeline(id="p", name="P", nodes=[
            Node(id="work", name="W", type=NodeType.PRODUCER,
                 backend=backend, model=model),
            Node(id="gate", name="G", type=NodeType.GATE,
                 gate=GateConfig(mode=GateMode.AUTO)),
        ])

    def test_litellm_node_without_model_is_rejected_at_validation(self):
        errs = validate_pipeline(self._pipe(""))
        self.assertTrue(any("explicit model" in e for e in errs), msg=str(errs))
        errs = validate_pipeline(self._pipe("mock"))
        self.assertTrue(any("explicit model" in e for e in errs), msg=str(errs))

    def test_litellm_node_with_model_passes(self):
        self.assertEqual(validate_pipeline(self._pipe("ollama/llama3.1")), [])

    def test_other_backends_unaffected(self):
        self.assertEqual(validate_pipeline(self._pipe("mock", backend="mock")), [])
        self.assertEqual(validate_pipeline(self._pipe("", backend="claude_code")), [])


if __name__ == "__main__":
    unittest.main()
