"""LiteLLM backend — model-agnostic execution (ADR-003: no vendor lock-in).

One interface for 100+ providers + local Ollama. The node's `model` field selects
the provider/model, e.g.:
    "claude-3-5-sonnet-latest"  (Anthropic)
    "gpt-4o"                    (OpenAI)
    "ollama/llama3"             (local, no data leaves the machine)
    "ollama/qwen2.5-coder"      (local)

Guarded: importing/using never crashes the orchestrator. If litellm isn't
installed, `available()` is False and run() returns a clean error → the engine's
retry-then-gate policy handles it. Uses the shared output contract (contract.py)
so its audit output is identically shaped to the Claude Code backend.
"""
from __future__ import annotations

from typing import Any

from ..models import BackendResult, Cost, Node
from . import contract

# litellm is imported LAZILY (it's a heavy import, ~seconds) so the API sidecar
# starts instantly. Importing at module load delayed sidecar readiness and caused
# the desktop window to hit "connection refused" before the API was listening.
litellm = None  # type: ignore


def _load_litellm():
    global litellm
    if litellm is None:
        try:
            import litellm as _l  # type: ignore
            litellm = _l
        except Exception:  # noqa: BLE001
            litellm = False  # sentinel: tried and unavailable
    return litellm


class LiteLLMBackend:
    name = "litellm"

    def __init__(self, default_model: str = "gpt-4o-mini", temperature: float = 0.2,
                 timeout: int = 300) -> None:
        self.default_model = default_model
        self.temperature = temperature
        self.timeout = timeout

    def available(self) -> bool:
        return bool(_load_litellm())

    def run(self, node: Node, context: dict[str, Any]) -> BackendResult:
        ll = _load_litellm()
        if not ll:
            return BackendResult(ok=False, error="litellm not installed "
                                 "(pip install 'moira-orchestrator[backends]')")
        model = node.model if node.model and node.model != "mock" else self.default_model
        messages = [
            {"role": "system", "content": contract.SYSTEM},
            {"role": "user", "content": contract.build_stage_prompt(
                role=node.role or node.id, spec_ref=node.spec_ref,
                spec_text=context.get("spec_text", ""),
                upstream=context.get("upstream", {}),
                feedback=context.get("feedback", {}).get(node.id, ""),
            )},
        ]
        try:
            resp = ll.completion(
                model=model, messages=messages,
                temperature=self.temperature, timeout=self.timeout,
            )
        except Exception as e:  # noqa: BLE001 — auth, network, unknown model, etc.
            return BackendResult(ok=False, error=f"litellm error ({model}): {e}")

        return self._parse(resp, model)

    def _parse(self, resp: Any, model: str) -> BackendResult:
        try:
            text = resp.choices[0].message.content or ""
        except Exception:  # noqa: BLE001
            text = str(resp)

        cost = self._cost(resp)
        payload = contract.extract_contract(text)
        tools = payload.get("tools_used") or [f"litellm:{model}"]
        return BackendResult(
            output=payload.get("output", payload.get("raw", {})),
            tools_used=tools,
            decisions=payload.get("decisions", []),
            cost=cost,
            ok=True,
        )

    @staticmethod
    def _cost(resp: Any) -> Cost:
        tin = tout = 0
        usd = 0.0
        try:
            usage = getattr(resp, "usage", None)
            if usage is not None:
                tin = getattr(usage, "prompt_tokens", 0) or 0
                tout = getattr(usage, "completion_tokens", 0) or 0
        except Exception:  # noqa: BLE001
            pass
        try:
            usd = float(litellm.completion_cost(completion_response=resp))  # type: ignore
        except Exception:  # noqa: BLE001
            usd = 0.0
        return Cost(tokens_in=tin, tokens_out=tout, usd=round(usd or 0.0, 6))
