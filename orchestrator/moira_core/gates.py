"""Gate evaluation (agent-and-gate-model.md).

A gate consumes verifier results and decides: approve / reject / escalate.
Modes: auto / hybrid (confidence-driven) / human / off.

This resolves the gate paradox concretely:
- auto:   verdict decides; HIGH/CRITICAL findings can force escalation
- hybrid: confidence >= high_cutoff -> approve; < low_cutoff -> reject;
          in between (or blocking severity) -> escalate to the named persona
- human:  always escalate to the persona
- off:    always approve
"""
from __future__ import annotations

from typing import Any

from .models import BackendResult, GateConfig, GateDecision, GateMode


def evaluate_gate(cfg: GateConfig, verifier_results: list[BackendResult]) -> GateDecision:
    """Return a GateDecision. 'escalate' means a human must act (queued to Inbox)."""
    if cfg.mode == GateMode.OFF:
        return GateDecision(decision="approve", by="system",
                            confirmed="gate disabled (mode=off)")

    if cfg.mode == GateMode.HUMAN:
        return GateDecision(decision="escalate", by=cfg.persona,
                            confirmed="human gate — awaiting persona")

    # aggregate verifier signal
    min_conf = min((r.min_confidence() for r in verifier_results), default=1.0)
    blocking = any(r.has_blocking() for r in verifier_results)

    if cfg.mode == GateMode.AUTO:
        if blocking and cfg.escalate_on_blocking:
            return GateDecision(decision="escalate", by=cfg.persona or "system",
                                confirmed="auto gate escalated: blocking (HIGH/CRITICAL) finding")
        if blocking:
            return GateDecision(decision="reject", by="system",
                                confirmed="auto gate: blocking finding, no escalation configured")
        return GateDecision(decision="approve", by="system",
                            confirmed=f"auto gate: no blocking findings (min_conf={min_conf:.2f})")

    # HYBRID — confidence-driven routing
    if blocking and cfg.escalate_on_blocking:
        return GateDecision(decision="escalate", by=cfg.persona or "system",
                            confirmed="hybrid gate escalated: blocking finding")
    if min_conf >= cfg.high_cutoff:
        return GateDecision(decision="approve", by="system",
                            confirmed=f"hybrid auto-accept: min_conf {min_conf:.2f} >= {cfg.high_cutoff}")
    if min_conf < cfg.low_cutoff:
        return GateDecision(decision="reject", by="system",
                            confirmed=f"hybrid auto-deny: min_conf {min_conf:.2f} < {cfg.low_cutoff}")
    return GateDecision(decision="escalate", by=cfg.persona or "system",
                        confirmed=f"hybrid -> human: min_conf {min_conf:.2f} in [{cfg.low_cutoff}, {cfg.high_cutoff})")


def simulate_routing(cfg: GateConfig, confidences: list[float]) -> dict[str, list[float]]:
    """Cezar-style live preview: given candidate confidences, show how they'd route."""
    buckets: dict[str, list[float]] = {"approve": [], "escalate": [], "reject": []}
    for c in confidences:
        if c >= cfg.high_cutoff:
            buckets["approve"].append(c)
        elif c < cfg.low_cutoff:
            buckets["reject"].append(c)
        else:
            buckets["escalate"].append(c)
    return buckets
