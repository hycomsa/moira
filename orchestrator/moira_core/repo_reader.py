"""AI SDLC repo reader (FR-005).

Reads the single source of truth — intents, requirements, func-specs, ADRs,
standards — from a git repo laid out per the NDSM framework
(.ai/context/...). Provides spec text + lineage to agents.

Lineage is deterministic and git-native: a node working on FUNC §x records the
trace FUNC -> REQ -> INT, which becomes the audit record's `lineage`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from . import yamlio
from .models import AgentDefinition, GateConfig, NodeType, Node, Pipeline


class AISdlcRepo:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.ctx = self.root / ".ai" / "context"

    # ---- discovery --------------------------------------------------------- #
    def exists(self) -> bool:
        return self.ctx.exists()

    def list_func_specs(self) -> list[str]:
        d = self.ctx / "func-specs"
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    def list_intents(self) -> list[str]:
        d = self.ctx / "intent-specs"
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    def list_adrs(self) -> list[str]:
        d = self.ctx / "adrs"
        if not d.exists():
            return []
        return sorted(p.name for p in d.glob("ADR-*.md"))

    # ---- reading ----------------------------------------------------------- #
    # sidecar files in a func-spec folder that are NOT the spec itself
    _NON_SPEC_MD = {"changelog.md", "test-plan.md"}

    def read_func_spec(self, func_id: str) -> Optional[str]:
        """Read a func-spec folder (concatenate its markdown), or a single .md.

        Excludes non-spec sidecars (changelog, test-plan) and puts `func-spec.md`
        first, so the artifact title/headings come from the spec — not a changelog."""
        d = self.ctx / "func-specs" / func_id
        if d.is_dir():
            files = [p for p in d.glob("*.md") if p.name not in self._NON_SPEC_MD]
            files.sort(key=lambda p: (p.name != "func-spec.md", p.name))
            parts = [p.read_text(encoding="utf-8") for p in files]
            return "\n\n".join(parts) if parts else None
        f = self.ctx / "func-specs" / f"{func_id}.md"
        return f.read_text(encoding="utf-8") if f.exists() else None

    def read_test_plan(self, func_id: str) -> Optional[str]:
        """Read a func-spec's test plan (qa@author-test-plan output), if present."""
        f = self.ctx / "func-specs" / func_id / "test-plan.md"
        return f.read_text(encoding="utf-8") if f.exists() else None

    def read_intent(self, intent_id: str) -> Optional[str]:
        f = self.ctx / "intent-specs" / intent_id / "intent.md"
        return f.read_text(encoding="utf-8") if f.exists() else None

    def read_requirement(self, req_id: str) -> Optional[str]:
        """Slice a single REQ section out of requirements/{AREA}/index.md.

        Requirements live as `### {REQ-AREA-NN} — title` sections in a per-area
        index; return that section (heading to the next `###`/`##`).
        """
        parts = req_id.split("-")
        if len(parts) < 2:
            return None
        idx = self.ctx / "requirements" / parts[1] / "index.md"
        if not idx.exists():
            return None
        lines = idx.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        capturing = False
        for ln in lines:
            if ln.startswith("### ") and req_id in ln:
                capturing = True
                out.append(ln)
                continue
            if capturing and (ln.startswith("### ") or ln.startswith("## ")):
                break
            if capturing:
                out.append(ln)
        return "\n".join(out).strip() or None

    def read_adr(self, adr_id: str) -> Optional[str]:
        matches = sorted(self.ctx.glob(f"adrs/{adr_id}*.md"))
        return matches[0].read_text(encoding="utf-8") if matches else None

    def resolve_artifact(self, art_id: str) -> Optional[dict[str, Any]]:
        """Resolve any lineage id (FUNC/REQ/INT/ADR) -> {id, type, title, text}."""
        prefix = art_id.split("-", 1)[0].upper()
        reader = {"FUNC": self.read_func_spec, "REQ": self.read_requirement,
                  "INT": self.read_intent, "ADR": self.read_adr,
                  "REG": self.read_regulation}.get(prefix)
        if reader is None:
            return None
        text = reader(art_id)
        if text is None:
            return None
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines()
                      if ln.lstrip().startswith("#")), art_id)
        # provenance: the upstream artifacts this one references (for the orbit viz)
        lineage = [x for x in self.trace_lineage(text, art_id) if x != art_id]
        return {"id": art_id, "type": prefix, "title": title, "text": text, "lineage": lineage}

    def read_standards(self, area: str = "") -> dict[str, str]:
        d = self.root / ".ai" / "standards"
        if area:
            d = d / area
        out: dict[str, str] = {}
        if d.exists():
            for p in d.rglob("*.md"):
                out[str(p.relative_to(self.root))] = p.read_text(encoding="utf-8")
        return out

    # ---- lineage ----------------------------------------------------------- #
    def trace_lineage(self, spec_text: str, func_id: str) -> list[str]:
        """Extract referenced artifact IDs (INT-/REQ-/ADR-/FUNC-/REG-) from spec text."""
        ids = set()
        ids.add(func_id)
        for m in re.finditer(r'\b(INT|REQ|ADR|FUNC|IMPL|REG)-[A-Za-z0-9-]+', spec_text or ""):
            ids.add(m.group(0))
        return sorted(ids)

    # ---- regulations / compliance corpus (.ai/standards/compliance) ------- #
    @property
    def compliance_dir(self) -> Path:
        return self.root / ".ai" / "standards" / "compliance"

    @staticmethod
    def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
        """Split `--- yaml --- body`; returns (frontmatter dict, body)."""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    return (yamlio.load(parts[1]) or {}), parts[2].lstrip("\n")
                except Exception:  # noqa: BLE001
                    return {}, text
        return {}, text

    def list_regulations(self) -> list[dict[str, Any]]:
        """List regulation reference docs (id, title, triggers) for the picker."""
        d = self.compliance_dir
        if not d.exists():
            return []
        out = []
        for f in sorted(d.glob("*.md")):
            fm, _ = self._frontmatter(f.read_text(encoding="utf-8", errors="replace"))
            rid = fm.get("id") or f.stem
            out.append({"id": rid, "title": fm.get("title", rid),
                        "triggers": fm.get("triggers") or [],
                        "severity_policy": fm.get("severity_policy", "")})
        return out

    def read_regulation(self, reg_id: str) -> Optional[str]:
        """Return a regulation doc's body (frontmatter stripped). Matches by id or filename."""
        d = self.compliance_dir
        if not d.exists():
            return None
        # direct filename hit first, then scan frontmatter ids
        cand = d / f"{reg_id}.md"
        files = [cand] if cand.exists() else list(d.glob("*.md"))
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            fm, body = self._frontmatter(text)
            if f.stem == reg_id or fm.get("id") == reg_id:
                return body or text
        return None

    # ---- skills (Cezar-style: skills live in the repo) -------------------- #
    def list_skills(self) -> list[dict[str, Any]]:
        """List skills from .agents/skills/*/SKILL.md (name + description + group)."""
        out: list[dict[str, Any]] = []
        sk = self.root / ".agents" / "skills"
        if not sk.exists():
            return out
        for d in sorted(sk.iterdir()):
            f = d / "SKILL.md"
            if not d.is_dir() or not f.exists():
                continue
            name = d.name
            group = name.split("@")[0] if "@" in name else "general"
            out.append({
                "name": name,
                "group": group,
                "description": self._skill_description(f),
                "source": "repo",
                "status": "enabled",
            })
        return out

    @staticmethod
    def _skill_description(path: Path) -> str:
        text = path.read_text(encoding="utf-8", errors="replace")[:3000]
        body = text
        # split off YAML frontmatter; prefer an explicit description field
        if text.startswith("---"):
            fm = text.split("---", 2)
            if len(fm) >= 3:
                fm_lines = fm[1].splitlines()
                for idx, line in enumerate(fm_lines):
                    if line.strip().lower().startswith("description:"):
                        d = line.split(":", 1)[1].strip().strip('"').strip("'")
                        if d and d not in (">", "|", ">-", "|-", ">+", "|+"):
                            return d[:200]
                        # YAML block scalar — collect following indented lines
                        collected = []
                        for nxt in fm_lines[idx + 1:]:
                            if nxt.strip() == "":
                                if collected:
                                    break
                                continue
                            if nxt[:1] in (" ", "\t"):
                                collected.append(nxt.strip())
                            else:
                                break
                        if collected:
                            return " ".join(collected)[:200]
                body = fm[2]  # scan the body, not the frontmatter keys
        # first meaningful prose line of the body (skip headings, blockquote/list markers)
        for raw in body.splitlines():
            s = raw.strip().lstrip(">").lstrip("-").lstrip("*").strip()
            if s and not s.startswith("#") and not s.startswith("---") and len(s) > 3:
                return s[:200]
        return ""

    # ---- agents (git-native: .ai/agents/*.yml) ---------------------------- #
    @property
    def agents_dir(self) -> Path:
        return self.ctx / "agents"

    def list_agents(self) -> list[dict[str, Any]]:
        d = self.agents_dir
        if not d.exists():
            return []
        out = []
        for f in sorted(d.glob("*.yml")):
            try:
                out.append(AgentDefinition.from_dict(yamlio.load(f.read_text("utf-8"))).to_dict())
            except Exception:  # noqa: BLE001 — skip malformed
                continue
        return out

    def get_agent(self, agent_id: str) -> Optional[dict[str, Any]]:
        f = self.agents_dir / f"{agent_id}.yml"
        if not f.exists():
            return None
        return AgentDefinition.from_dict(yamlio.load(f.read_text("utf-8"))).to_dict()

    def save_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        agent = AgentDefinition.from_dict(data)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        (self.agents_dir / f"{agent.id}.yml").write_text(yamlio.dump(agent.to_dict()), "utf-8")
        return agent.to_dict()

    def delete_agent(self, agent_id: str) -> bool:
        f = self.agents_dir / f"{agent_id}.yml"
        if f.exists():
            f.unlink()
            return True
        return False

    # ---- pipelines (git-native: .ai/pipelines/*.yml) ---------------------- #
    @property
    def pipelines_dir(self) -> Path:
        return self.ctx / "pipelines"

    def list_pipeline_defs(self) -> list[dict[str, Any]]:
        d = self.pipelines_dir
        if not d.exists():
            return []
        out = []
        for f in sorted(d.glob("*.yml")):
            try:
                out.append(yamlio.load(f.read_text("utf-8")))
            except Exception:  # noqa: BLE001
                continue
        return out

    def get_pipeline_def(self, pipeline_id: str) -> Optional[dict[str, Any]]:
        f = self.pipelines_dir / f"{pipeline_id}.yml"
        return yamlio.load(f.read_text("utf-8")) if f.exists() else None

    def save_pipeline_def(self, data: dict[str, Any]) -> dict[str, Any]:
        self.pipelines_dir.mkdir(parents=True, exist_ok=True)
        (self.pipelines_dir / f"{data['id']}.yml").write_text(yamlio.dump(data), "utf-8")
        return data

    def delete_pipeline_def(self, pipeline_id: str) -> bool:
        f = self.pipelines_dir / f"{pipeline_id}.yml"
        if f.exists():
            f.unlink()
            return True
        return False

    def build_pipeline(self, pdef: dict[str, Any], func_ref: str = "") -> Pipeline:
        """Resolve a pipeline YAML (nodes referencing agents by id) into a Pipeline.
        Gate nodes inline their gate config; producer/verifier nodes pull
        role/backend/model/type from the referenced agent definition."""
        nodes: list[Node] = []
        for nd in pdef.get("nodes", []):
            deps = nd.get("depends_on") or []
            ntype_raw = nd.get("type")
            if ntype_raw == "gate" or "gate" in nd:
                gate = GateConfig.from_dict(nd["gate"]) if nd.get("gate") else GateConfig()
                # in a DAG, a gate must run after the verifiers it consumes
                gate_deps = deps or list(gate.consumes)
                nodes.append(Node(
                    id=nd["id"], name=nd.get("name", nd["id"]), type=NodeType.GATE,
                    gate=gate, on_reject_goto=nd.get("on_reject_goto"), depends_on=gate_deps,
                ))
            elif ntype_raw == "auto_check":
                cmd = nd.get("check_cmd") or (nd.get("check") or {}).get("cmd", "")
                nodes.append(Node(
                    id=nd["id"], name=nd.get("name", nd["id"]), type=NodeType.AUTO_CHECK,
                    check_cmd=cmd, check_kind=nd.get("check_kind", ""),
                    spec_ref=nd.get("spec_ref") or func_ref,
                    depends_on=deps, max_retries=nd.get("max_retries", 0),
                ))
            elif nd.get("skill"):
                # authoring node: drive an AI SDLC framework skill (ba@*/pm@*/qa@*) headless,
                # same as Discovery — writes artifacts into the AI SDLC repo (run cwd = repo).
                nodes.append(Node(
                    id=nd["id"], name=nd.get("name", nd["skill"]), type=NodeType.PRODUCER,
                    backend="claude_code", role="ba-skill", skill=nd["skill"],
                    skill_input=nd.get("input", ""), prompt_extra=nd.get("elaboration", ""),
                    spec_ref=nd.get("spec_ref") or func_ref,
                    depends_on=deps, max_retries=nd.get("max_retries", 1),
                ))
            else:
                agent = self.get_agent(nd["agent"]) if nd.get("agent") else None
                role = (agent or {}).get("role", nd.get("agent", nd["id"]))
                ntype = NodeType((agent or {}).get("type", "producer"))
                nodes.append(Node(
                    id=nd["id"],
                    name=nd.get("name") or (agent or {}).get("name", nd["id"]),
                    type=ntype, role=role,
                    backend=nd.get("backend") or (agent or {}).get("backend", "mock"),
                    model=nd.get("model") or (agent or {}).get("model", ""),
                    spec_ref=nd.get("spec_ref") or func_ref,
                    max_retries=nd.get("max_retries", 2), depends_on=deps,
                ))
        return Pipeline(id=pdef["id"], name=pdef.get("name", pdef["id"]), nodes=nodes)

    def context_for(self, func_id: str) -> dict[str, Any]:
        """Bundle the agent context for a func-spec: text + lineage."""
        text = self.read_func_spec(func_id) or ""
        return {
            "func_id": func_id,
            "spec_text": text,
            "lineage": self.trace_lineage(text, func_id),
            "available_adrs": self.list_adrs(),
        }
