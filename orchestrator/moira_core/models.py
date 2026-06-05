"""Core data models for the Moira orchestration engine.

These map directly to the design in the AI SDLC repo:
- agent-and-gate-model.md  (producer/verifier agents, gate modes, confidence routing)
- operating-model.md       (audit record fields, event log)
- ADR-004                  (execution delegated to pluggable backends)

The audit record fields are intentionally exactly:
input, output, tools, decisions, approvals, cost, time, owner.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class NodeType(str, Enum):
    PRODUCER = "producer"      # creates artifacts (analyst, architect, coder, tester)
    VERIFIER = "verifier"      # assesses artifacts -> findings + verdict (LLM)
    AUTO_CHECK = "auto_check"  # runs a real command (pytest/lint/SAST) -> pass/fail
    GATE = "gate"              # human/auto checkpoint


class GateMode(str, Enum):
    AUTO = "auto"              # verdict decides, no human
    HYBRID = "hybrid"          # confidence-driven: high->accept, medium->human, low->deny
    HUMAN = "human"            # named persona must approve
    OFF = "off"                # no gate


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WAITING_GATE = "waiting_gate"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# --------------------------------------------------------------------------- #
# Verifier findings
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    id: str
    title: str
    severity: Severity = Severity.LOW
    confidence: float = 1.0           # 0..1 — drives hybrid gate routing
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


# --------------------------------------------------------------------------- #
# Backend result (what an agent backend returns for one node)
# --------------------------------------------------------------------------- #
@dataclass
class Cost:
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackendResult:
    """Returned by every AgentBackend.run() call."""
    output: dict[str, Any] = field(default_factory=dict)      # artifacts produced
    tools_used: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)        # choices the agent made
    findings: list[Finding] = field(default_factory=list)     # verifier output
    cost: Cost = field(default_factory=Cost)
    ok: bool = True
    error: str = ""

    def min_confidence(self) -> float:
        """Lowest finding confidence — used by hybrid gate routing."""
        if not self.findings:
            return 1.0
        return min(f.confidence for f in self.findings)

    def has_blocking(self) -> bool:
        """Any HIGH/CRITICAL finding blocks an auto gate."""
        return any(f.severity in (Severity.HIGH, Severity.CRITICAL) for f in self.findings)


# --------------------------------------------------------------------------- #
# Gate configuration + decision
# --------------------------------------------------------------------------- #
@dataclass
class GateConfig:
    mode: GateMode = GateMode.AUTO
    persona: str = "none"                 # lead-dev | architect | ciso | compliance | client | none
    consumes: list[str] = field(default_factory=list)  # which verifier node ids feed this gate
    reviews: list[str] = field(default_factory=list)    # producer node ids whose OUTPUT the persona reviews
    audience: str = "technical"           # "technical" | "client" — controls how the artifact is framed
    high_cutoff: float = 0.85             # >= -> auto-accept (hybrid)
    low_cutoff: float = 0.50              # <  -> auto-deny (hybrid)
    escalate_on_blocking: bool = True     # HIGH/CRITICAL finding always escalates to human

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GateConfig":
        d = dict(d)
        d["mode"] = GateMode(d["mode"])
        return cls(**d)


@dataclass
class GateDecision:
    decision: str                          # approve | reject | escalate
    by: str                                # owner / persona / "system"
    confirmed: str = ""                    # WHAT the reviewer confirmed (real audit, not a stamp)
    feedback: str = ""                     # on reject -> goes back to producer
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Pipeline definition
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    id: str
    name: str
    type: NodeType
    backend: str = "mock"                  # which AgentBackend
    model: str = "mock"                    # model hint for the backend
    role: str = ""                         # e.g. requirements-analyst, code-generator
    spec_ref: str = ""                     # FUNC/INT/REQ section this node works on
    gate: Optional[GateConfig] = None      # only for GATE nodes
    on_reject_goto: Optional[str] = None   # node id to return to on REJECT
    max_retries: int = 2                   # retry-N-then-gate
    depends_on: list[str] = field(default_factory=list)  # DAG predecessors (empty -> linear by order)
    check_cmd: str = ""                    # AUTO_CHECK: shell command to run in cwd
    # Discovery/BA: drive an AI SDLC framework skill (e.g. ba@shape-func-spec) to
    # author/refine an artifact in the repo, optionally specialized by prompt_extra.
    skill: str = ""                        # skill id, e.g. "ba@shape-func-spec"
    skill_input: str = ""                  # the skill's argument (topic / REQ-ID / notes path)
    prompt_extra: str = ""                 # user's elaboration appended to the skill invocation

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "type": self.type.value,
            "backend": self.backend, "model": self.model, "role": self.role,
            "spec_ref": self.spec_ref,
            "gate": self.gate.to_dict() if self.gate else None,
            "on_reject_goto": self.on_reject_goto, "max_retries": self.max_retries,
            "depends_on": self.depends_on, "check_cmd": self.check_cmd,
            "skill": self.skill, "skill_input": self.skill_input, "prompt_extra": self.prompt_extra,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Node":
        return cls(
            id=d["id"], name=d["name"], type=NodeType(d["type"]),
            backend=d.get("backend", "mock"), model=d.get("model", "mock"),
            role=d.get("role", ""), spec_ref=d.get("spec_ref", ""),
            gate=GateConfig.from_dict(d["gate"]) if d.get("gate") else None,
            on_reject_goto=d.get("on_reject_goto"), max_retries=d.get("max_retries", 2),
            depends_on=d.get("depends_on") or [], check_cmd=d.get("check_cmd", ""),
            skill=d.get("skill", ""), skill_input=d.get("skill_input", ""),
            prompt_extra=d.get("prompt_extra", ""),
        )


@dataclass
class AgentDefinition:
    """A user-defined agent (git-native: .ai/agents/<id>.yml). Resolved into a
    pipeline Node when a pipeline references it by id."""
    id: str
    name: str
    type: str = "producer"            # producer | verifier
    category: str = "general"         # analysis|design|implementation|generation|security|testing
    role: str = ""                    # backend role key (defaults to id)
    backend: str = "claude_code"      # default backend (frontier; overridable per node/run)
    model: str = ""                   # optional model hint ("" = backend default)
    description: str = ""
    tools_policy: str = "reasoning"   # reasoning (tool-light) | coding (full tools)
    system_prompt: str = ""
    skill_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentDefinition":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        clean = {k: v for k, v in d.items() if k in known}
        clean.setdefault("role", clean.get("id", ""))
        if clean.get("skill_refs") is None:
            clean["skill_refs"] = []
        return cls(**clean)


@dataclass
class Pipeline:
    id: str
    name: str
    nodes: list[Node]

    def index_of(self, node_id: str) -> int:
        for i, n in enumerate(self.nodes):
            if n.id == node_id:
                return i
        raise KeyError(node_id)

    def by_id(self, node_id: str) -> "Node":
        return self.nodes[self.index_of(node_id)]

    def dep_map(self) -> dict[str, list[str]]:
        """Predecessor map. If any node declares depends_on, use the explicit DAG;
        otherwise infer a linear chain (node[i] depends on node[i-1]) — preserving
        the original sequential behaviour for pipelines that don't define edges."""
        if any(n.depends_on for n in self.nodes):
            return {n.id: list(n.depends_on) for n in self.nodes}
        return {n.id: ([self.nodes[i - 1].id] if i > 0 else []) for i, n in enumerate(self.nodes)}

    def descendants(self, node_id: str, deps: dict[str, list[str]]) -> set[str]:
        """All nodes reachable downstream of node_id (for reject -> rework reset)."""
        children: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for nid, preds in deps.items():
            for p in preds:
                children.setdefault(p, []).append(nid)
        out, stack = set(), [node_id]
        while stack:
            cur = stack.pop()
            for c in children.get(cur, []):
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name,
                "nodes": [n.to_dict() for n in self.nodes]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Pipeline":
        return cls(id=d["id"], name=d["name"],
                   nodes=[Node.from_dict(n) for n in d["nodes"]])


# --------------------------------------------------------------------------- #
# Audit record — the defensible core (operating-model.md)
# --------------------------------------------------------------------------- #
@dataclass
class AuditRecord:
    """One per executed step. Fields are exactly the agreed schema."""
    step_id: str
    run_id: str
    node_id: str
    node_name: str
    owner: str                                   # accountable human / persona
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    time_start: float = 0.0
    time_end: float = 0.0
    # lineage: what spec artifacts this step traces to
    lineage: list[str] = field(default_factory=list)
    status: str = Status.PENDING.value

    @property
    def duration(self) -> float:
        return max(0.0, self.time_end - self.time_start)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration"] = self.duration
        return d


@dataclass
class Event:
    """Append-only event log entry (operating-model.md pillar 4)."""
    run_id: str
    kind: str                 # run.start, node.start, node.end, gate.wait, gate.decision, retry, run.end
    message: str
    ts: float = field(default_factory=time.time)
    node_id: str = ""


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"
