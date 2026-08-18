"""Structural validation for pipelines (quick-win A).

A malformed pipeline (duplicate ids, dangling edges, a gate with no config, a
dependency cycle) otherwise fails cryptically deep in a run. `validate_pipeline`
checks the RESOLVED `Pipeline` the engine will actually drive and returns a list
of human-readable errors (empty == valid). Callers reject save/launch on errors.
"""
from __future__ import annotations

from .models import NodeType, Pipeline


def validate_pipeline(pipeline: Pipeline) -> list[str]:
    errors: list[str] = []
    if not (pipeline.id or "").strip():
        errors.append("pipeline id is required")
    if not (pipeline.name or "").strip():
        errors.append("pipeline name is required")

    nodes = pipeline.nodes or []
    if not nodes:
        errors.append("pipeline must have at least one node")
        return errors

    ids = [n.id for n in nodes]
    seen: set[str] = set()
    for nid in ids:
        if nid in seen:
            errors.append(f"duplicate node id: {nid}")
        seen.add(nid)
    idset = set(ids)

    for n in nodes:
        for dep in n.depends_on:
            if dep not in idset:
                errors.append(f"node '{n.id}' depends_on unknown node '{dep}'")
        if n.on_reject_goto and n.on_reject_goto not in idset:
            errors.append(f"node '{n.id}' on_reject_goto unknown node '{n.on_reject_goto}'")
        if n.type == NodeType.GATE and n.gate is None:
            errors.append(f"gate node '{n.id}' has no gate config")
        if n.type == NodeType.GATE and n.gate is not None:
            ml = n.gate.max_loop
            if isinstance(ml, bool) or not isinstance(ml, int) or ml < 0:
                errors.append(f"gate node '{n.id}' max_loop must be a non-negative integer")
        # fail-loud model identity (QW6/ADR-015): litellm has no default model —
        # reject the configuration at save/launch, not minutes later mid-run
        if (n.type in (NodeType.PRODUCER, NodeType.VERIFIER) and n.backend == "litellm"
                and (not (n.model or "").strip() or n.model == "mock")):
            errors.append(f"node '{n.id}': backend 'litellm' requires an explicit model "
                          "(e.g. 'gpt-4o', 'ollama/llama3.1')")

    if _has_cycle(pipeline.dep_map()):
        errors.append("dependency cycle detected in pipeline")
    return errors


def _has_cycle(deps: dict[str, list[str]]) -> bool:
    """Cycle detection over the effective predecessor graph (3-color DFS)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in deps}

    def visit(u: str) -> bool:
        color[u] = GRAY
        for v in deps.get(u, []):
            if v not in color:        # dangling edge — reported separately
                continue
            if color[v] == GRAY or (color[v] == WHITE and visit(v)):
                return True
        color[u] = BLACK
        return False

    return any(color[n] == WHITE and visit(n) for n in deps)
