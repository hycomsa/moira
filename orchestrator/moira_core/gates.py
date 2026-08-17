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

from .models import BackendResult, GateConfig, GateDecision, GateMode, Severity

# Rework feedback (QW1): how many findings a system reject serializes into
# GateDecision.feedback, and how much of each finding's detail survives.
FEEDBACK_MAX_FINDINGS = 5
FEEDBACK_DETAIL_CAP = 500

_SEV_RANK = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
             Severity.HIGH: 3, Severity.CRITICAL: 4}


def findings_feedback(verifier_results: list[BackendResult],
                      limit: int = FEEDBACK_MAX_FINDINGS) -> str:
    """Serialize the findings behind a system reject into producer feedback.

    Deterministic check results (BackendResult.output["check"] — test_exec,
    ac_coverage, log_hygiene, shell) come before LLM verifier findings: command
    output is ground truth, LLM findings are advisory. INFO findings are passes,
    not defects — skipped. The list is capped and details truncated so the
    feedback stays a digest, not a transcript."""
    items: list[tuple[int, int, float, Any]] = []
    for r in verifier_results:
        deterministic = bool((r.output or {}).get("check"))
        for f in r.findings:
            if f.severity == Severity.INFO:
                continue
            items.append((0 if deterministic else 1, -_SEV_RANK[f.severity],
                          f.confidence, f))
    if not items:
        return ""
    items.sort(key=lambda t: t[:3])
    lines = []
    for _, _, _, f in items[:limit]:
        detail = (f.detail or "").strip()
        if len(detail) > FEEDBACK_DETAIL_CAP:
            detail = detail[:FEEDBACK_DETAIL_CAP] + "…"
        lines.append(f"- [{f.severity.value}] {f.title}" + (f" — {detail}" if detail else ""))
    hidden = len(items) - min(len(items), limit)
    if hidden:
        lines.append(f"(+{hidden} more finding(s) not shown)")
    return "Gate rejected — address these findings before the next attempt:\n" + "\n".join(lines)


def _loop_exhausted(cfg: GateConfig, system_rejects: int) -> GateDecision:
    """QW2/ADR-010: the gate spent its automatic-reject budget — the would-be
    reject becomes a human escalation. Never auto-approve, never fail: pausing
    keeps the governed stance (a human decides what to do with a non-converging
    rework loop)."""
    return GateDecision(
        decision="escalate", by=cfg.persona or "system",
        confirmed=(f"rework budget exhausted: {system_rejects}/{cfg.max_loop} "
                   "automatic rejects — human decision required"))


def evaluate_gate(cfg: GateConfig, verifier_results: list[BackendResult],
                  system_rejects: int = 0) -> GateDecision:
    """Return a GateDecision. 'escalate' means a human must act (queued to Inbox).

    `system_rejects` = how many rejects THIS gate already issued with by="system"
    (the caller derives it from the audit trail, so it survives resume/restart —
    ADR-010). At `cfg.max_loop` the next system reject escalates instead."""
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
            if system_rejects >= cfg.max_loop:
                return _loop_exhausted(cfg, system_rejects)
            return GateDecision(decision="reject", by="system",
                                confirmed="auto gate: blocking finding, no escalation configured",
                                feedback=findings_feedback(verifier_results))
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
        if system_rejects >= cfg.max_loop:
            return _loop_exhausted(cfg, system_rejects)
        return GateDecision(decision="reject", by="system",
                            confirmed=f"hybrid auto-deny: min_conf {min_conf:.2f} < {cfg.low_cutoff}",
                            feedback=findings_feedback(verifier_results))
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
