"""Governance packs — versioned, executable compliance policy (must-fix #5).

A pack turns "governance" from curated prose + an LLM opinion into an enforceable
bundle: deterministic checks (blocking), LLM checks (labeled qualitative second
opinion), a required human/auto gate, required evidence, and an override policy.

Packs are JSON, project-owned, and read ONLY from the repo
(`.ai/standards/compliance/packs/*.json` via `repo_reader`). The orchestrator
ships no built-in packs. `validate_pack` is the schema gate; `compile_pack` turns
a pack into pipeline nodes the existing engine/durable-runner already executes.

Deterministic-first is deliberate: an LLM saying "looks GDPR-compliant" is
qualitative evidence, not a control. Only deterministic findings block a gate.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import GateConfig, GateMode, Node, NodeType, Severity

# deterministic checks the engine knows how to run as a built-in (check_kind)
KNOWN_BUILTIN_CHECKS = {"ac_coverage", "test_exec", "log_hygiene"}
_SEVERITIES = {s.value for s in Severity}
_GATE_MODES = {m.value for m in GateMode}


def validate_pack(pack: dict[str, Any]) -> list[str]:
    """Return human-readable schema errors (empty == valid)."""
    errors: list[str] = []
    if not isinstance(pack, dict):
        return ["pack must be a JSON object"]
    if not str(pack.get("id") or "").strip():
        errors.append("pack id is required")
    if not str(pack.get("version") or "").strip():
        errors.append("pack version is required")

    checks = pack.get("checks") or {}
    deterministic = checks.get("deterministic") or []
    llm = checks.get("llm") or []
    if not deterministic and not llm:
        errors.append("pack must define at least one check (deterministic or llm)")

    seen_ids: set[str] = set()
    for c in deterministic:
        cid = c.get("id")
        if not cid:
            errors.append("deterministic check missing id")
        elif cid in seen_ids:
            errors.append(f"duplicate check id: {cid}")
        seen_ids.add(cid)
        if not c.get("built_in") and not c.get("command"):
            errors.append(f"deterministic check '{cid}' needs built_in or command")
        if c.get("built_in") and c["built_in"] not in KNOWN_BUILTIN_CHECKS:
            errors.append(f"unknown built_in check '{c['built_in']}' "
                          f"(known: {sorted(KNOWN_BUILTIN_CHECKS)})")
        sev = c.get("severity_on_fail")
        if sev not in _SEVERITIES:
            errors.append(f"check '{cid}' has invalid severity_on_fail '{sev}'")

    for c in llm:
        cid = c.get("id")
        if not cid:
            errors.append("llm check missing id")
        elif cid in seen_ids:
            errors.append(f"duplicate check id: {cid}")
        seen_ids.add(cid)
        if not isinstance(c.get("references", []), list):
            errors.append(f"llm check '{cid}' references must be a list")

    gate = pack.get("gate") or {}
    mode = gate.get("mode")
    if mode not in _GATE_MODES:
        errors.append(f"gate.mode '{mode}' invalid (must be one of {sorted(_GATE_MODES)})")
    for s in gate.get("blocks_on", []):
        if s not in _SEVERITIES:
            errors.append(f"gate.blocks_on contains invalid severity '{s}'")

    override = pack.get("override") or {}
    if "allowed_personas" in override and not isinstance(override["allowed_personas"], list):
        errors.append("override.allowed_personas must be a list")
    return errors


def compile_pack(pack: dict[str, Any], after: list[str] | None = None,
                 model: str = "", effort: str = "") -> list[Node]:
    """Compile a pack into pipeline nodes the engine/durable-runner already executes:

    - each deterministic check -> an AUTO_CHECK node (built_in -> check_kind,
      else command -> check_cmd). These emit blocking Findings.
    - each LLM check -> a PRODUCER node (role=compliance-verifier), a *qualitative*
      second opinion that does NOT block the gate by itself.
    - one governance GATE node (pack.gate mode/persona) that runs after every check.

    `after` = host-pipeline node ids the checks should run after (so governance runs
    once the work is done). Node ids are namespaced `gov-<pack id>-<check id>`.

    Note: the engine gate blocks on HIGH/CRITICAL findings (`escalate_on_blocking`);
    `pack.gate.blocks_on` is recorded in the policy result. Custom block thresholds
    below HIGH are advisory in this MVP.
    """
    prefix = f"gov-{pack['id']}"
    after = list(after or [])
    nodes: list[Node] = []
    check_ids: list[str] = []
    checks = pack.get("checks") or {}

    for c in checks.get("deterministic", []):
        nid = f"{prefix}-{c['id']}"
        common = dict(id=nid, name=c.get("name", c["id"]), type=NodeType.AUTO_CHECK,
                      depends_on=list(after), max_retries=0)
        if c.get("built_in"):
            nodes.append(Node(check_kind=c["built_in"], **common))
        else:
            nodes.append(Node(check_cmd=c.get("command", ""), **common))
        check_ids.append(nid)

    for c in checks.get("llm", []):
        nid = f"{prefix}-{c['id']}"
        refs = c.get("references") or []
        nodes.append(Node(
            id=nid, name=c.get("name", c["id"]), type=NodeType.PRODUCER,
            backend="claude_code", model=model or c.get("model", ""), effort=effort,
            role="compliance-verifier", spec_ref=", ".join(refs),
            depends_on=list(after), max_retries=1))
        check_ids.append(nid)

    g = pack.get("gate") or {}
    gcfg = GateConfig(mode=GateMode(g.get("mode", "human")), persona=g.get("persona", "compliance"),
                      consumes=list(check_ids))
    nodes.append(Node(id=f"{prefix}-gate", name=f"Governance · {pack['id']}",
                      type=NodeType.GATE, gate=gcfg, depends_on=list(check_ids)))
    return nodes


def attach_pack(pipeline, pack: dict[str, Any], model: str = "", effort: str = "") -> list[str]:
    """Append a pack's compiled nodes so governance runs AFTER all existing work.

    The host pipeline may rely on implicit linear ordering (no explicit depends_on).
    Appending nodes that DO declare depends_on flips the whole DAG to explicit mode,
    which would strip the host's implicit order — so we first materialize the host's
    inferred deps, then hang the governance nodes off the host's terminal node(s).
    Returns the appended node ids.
    """
    deps = pipeline.dep_map()
    for n in pipeline.nodes:
        n.depends_on = list(deps.get(n.id, []))
    referenced = {d for preds in deps.values() for d in preds}
    terminals = [n.id for n in pipeline.nodes if n.id not in referenced]
    nodes = compile_pack(pack, after=terminals, model=model, effort=effort)
    pipeline.nodes.extend(nodes)
    return [n.id for n in nodes]


def applied_marker(pack: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    """The `governance: applied` audit output stamped at attach time. Records exactly
    which pack version + content applied and which node ids carry each check, so the
    report/verifier can reconstruct policy coverage from the sealed audit alone."""
    pid = pack["id"]
    checks = []
    for c in (pack.get("checks") or {}).get("deterministic", []):
        checks.append({"id": c["id"], "node_id": f"gov-{pid}-{c['id']}", "kind": "deterministic"})
    for c in (pack.get("checks") or {}).get("llm", []):
        checks.append({"id": c["id"], "node_id": f"gov-{pid}-{c['id']}", "kind": "llm"})
    return {"governance": "applied", "pack": pid, "version": pack.get("version"),
            "fingerprint": pack_fingerprint(pack),
            "required_evidence": pack.get("required_evidence", []),
            "gate_node": f"gov-{pid}-gate", "checks": checks,
            "override": override}


def _check_status(kind: str, rec: dict[str, Any] | None) -> str:
    """Map a check's audit record to a policy status."""
    if kind == "llm":
        return "advisory"  # qualitative second opinion, never a deterministic verdict
    if rec is None:
        return "pending"
    out = rec.get("output") or {}
    if out.get("status") == "not_applicable":
        return "na"
    return "passed" if out.get("passed") else "failed"


def summarize_governance(audit_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct a PolicyResult per applied pack purely from the sealed audit.

    Looks for `governance: applied` marker records (stamped at attach time) and
    resolves each declared check's status from its own audit record. Fully
    git-native: the report rebuilds policy coverage from the mirrored audit alone.
    """
    by_node = {r.get("node_id"): r for r in audit_records}
    out: list[dict[str, Any]] = []
    for a in audit_records:
        marker = a.get("output") or {}
        if marker.get("governance") != "applied":
            continue
        checks = []
        for c in marker.get("checks", []):
            checks.append({"id": c["id"], "kind": c["kind"], "node_id": c["node_id"],
                           "status": _check_status(c["kind"], by_node.get(c["node_id"]))})
        gate_rec = by_node.get(marker.get("gate_node"))
        gate_decision = None
        if gate_rec:
            approvals = gate_rec.get("approvals") or []
            gate_decision = approvals[0].get("decision") if approvals else None
        out.append({
            "pack": marker.get("pack"), "version": marker.get("version"),
            "fingerprint": marker.get("fingerprint"),
            "required_evidence": marker.get("required_evidence", []),
            "checks": checks, "gate_decision": gate_decision,
            "override": marker.get("override"),
        })
    return out


def pack_fingerprint(pack: dict[str, Any]) -> str:
    """Stable sha256 over the pack content — stamped into the sealed audit so a run
    records exactly which policy applied (reproducible regardless of later edits)."""
    return hashlib.sha256(
        json.dumps(pack, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
