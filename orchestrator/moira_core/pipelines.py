"""Default SDLC vertical-slice pipeline for the v0.1 spike.

analyze -> [gate] -> design -> implement -> (code-quality + security) ->
[hybrid gate] -> test

Maps to agent-and-gate-model.md SDLC map. Gates are configurable; defaults here
make the demo runnable headless (auto/hybrid), with one human gate optional.
"""
from __future__ import annotations

from .models import GateConfig, GateMode, Node, NodeType, Pipeline


def default_sdlc_pipeline(func_ref: str = "FUNC-DEMO",
                          analysis_gate: GateMode = GateMode.AUTO,
                          impl_gate: GateMode = GateMode.HYBRID,
                          backend: str = "mock") -> Pipeline:
    nodes = [
        Node(id="analyze", name="Requirements Analyst", type=NodeType.PRODUCER,
             role="requirements-analyst", backend=backend, spec_ref=func_ref),
        Node(id="gate-analysis", name="Gate: Analysis Review", type=NodeType.GATE,
             gate=GateConfig(mode=analysis_gate, persona="ba"),
             on_reject_goto="analyze"),
        Node(id="design", name="Solution Architect", type=NodeType.PRODUCER,
             role="solution-architect", backend=backend, spec_ref=func_ref),
        Node(id="implement", name="Code Generator", type=NodeType.PRODUCER,
             role="code-generator", backend=backend, spec_ref=func_ref,
             max_retries=2),
        Node(id="verify-quality", name="Code Quality Reviewer", type=NodeType.VERIFIER,
             role="code-quality", backend=backend, spec_ref=func_ref),
        Node(id="verify-security", name="Security / Pentest Agent", type=NodeType.VERIFIER,
             role="security", backend=backend, spec_ref=func_ref),
        Node(id="gate-impl", name="Gate: Post-Implementation", type=NodeType.GATE,
             gate=GateConfig(mode=impl_gate, persona="lead-dev",
                             consumes=["verify-quality", "verify-security"],
                             high_cutoff=0.85, low_cutoff=0.50),
             on_reject_goto="implement"),
        Node(id="test", name="Test Author / Runner", type=NodeType.PRODUCER,
             role="test-author", backend=backend, spec_ref=func_ref),
    ]
    return Pipeline(id="sdlc-slice-v0.1", name="SDLC Slice (v0.1 spike)", nodes=nodes)


def client_gated_pipeline(func_ref: str = "FUNC-DEMO", backend: str = "mock") -> Pipeline:
    """SDLC slice with a CLIENT approval gate before any code is written.

    The wedge (DEC-MOIRA-001): a non-technical client approves the intent/analysis
    in business language — the one job incumbents' engineer-centric tools don't do.
    The client gate `reviews` the analyst's output (artifact), `audience=client`.
    """
    nodes = [
        Node(id="analyze", name="Requirements Analyst", type=NodeType.PRODUCER,
             role="requirements-analyst", backend=backend, spec_ref=func_ref),
        Node(id="gate-client", name="Client Approval: Requirements", type=NodeType.GATE,
             gate=GateConfig(mode=GateMode.HUMAN, persona="client",
                             reviews=["analyze"], audience="client"),
             on_reject_goto="analyze"),
        Node(id="design", name="Solution Architect", type=NodeType.PRODUCER,
             role="solution-architect", backend=backend, spec_ref=func_ref),
        Node(id="implement", name="Code Generator", type=NodeType.PRODUCER,
             role="code-generator", backend=backend, spec_ref=func_ref, max_retries=2),
        Node(id="verify-quality", name="Code Quality Reviewer", type=NodeType.VERIFIER,
             role="code-quality", backend=backend, spec_ref=func_ref),
        Node(id="gate-impl", name="Gate: Post-Implementation", type=NodeType.GATE,
             gate=GateConfig(mode=GateMode.HYBRID, persona="lead-dev",
                             consumes=["verify-quality"]),
             on_reject_goto="implement"),
        Node(id="test", name="Test Author / Runner", type=NodeType.PRODUCER,
             role="test-author", backend=backend, spec_ref=func_ref),
    ]
    return Pipeline(id="sdlc-client-gated", name="SDLC + Client Gate", nodes=nodes)


def available_pipelines() -> list[Pipeline]:
    """All pipeline templates Moira can run — for listing + visual graph (exAI)."""
    return [default_sdlc_pipeline(), client_gated_pipeline()]
