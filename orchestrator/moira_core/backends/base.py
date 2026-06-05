"""Agent backend interface (ADR-004: execution is delegated, not re-implemented).

A backend executes ONE node's work and returns a BackendResult. Moira's engine
owns orchestration, gates, audit and state; backends own execution. Concrete
backends: MockBackend (deterministic, for tests), ClaudeCodeBackend (subprocess
to the claude CLI under the user's own login — no per-seat API cost).
"""
from __future__ import annotations

from typing import Any, Protocol

from ..models import BackendResult, Node


class AgentBackend(Protocol):
    name: str

    def run(self, node: Node, context: dict[str, Any]) -> BackendResult:
        """Execute the node against the given context (specs + upstream outputs)."""
        ...


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, AgentBackend] = {}

    def register(self, backend: AgentBackend) -> None:
        self._backends[backend.name] = backend

    def get(self, name: str) -> AgentBackend:
        if name not in self._backends:
            raise KeyError(f"backend not registered: {name} (have: {list(self._backends)})")
        return self._backends[name]

    def available(self) -> list[str]:
        return list(self._backends)
