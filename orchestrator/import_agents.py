"""Import Claude Code subagents (.md) into Moira agent definitions (YAML).

The ecosystem (VoltAgent, wshobson, 0xfurai, …) ships hundreds of subagents as
Markdown with YAML frontmatter (name/description/tools/model) + a system-prompt
body. That format maps almost 1:1 onto Moira's AgentDefinition, so this importer
lets a team `git clone` any collection and pull the agents they want into a
workspace's git-native `.ai/context/agents/`.

Mapping:
  name        -> id + name
  description -> description
  model       -> model (hint)
  tools       -> tools_policy: 'coding' if Write/Edit/Bash present else 'reasoning'
  body        -> system_prompt
  type        -> 'verifier' if name/desc ~ review|audit|verify|test|scan|lint else 'producer'
  category    -> inferred from name/desc keywords (security|testing|design|...)

Usage: python3 import_agents.py <source_dir> [repo_path]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

CODING_TOOLS = {"write", "edit", "bash", "multiedit", "notebookedit"}
VERIFIER_HINTS = ("review", "audit", "verif", "test", "scan", "lint", "security", "qa")
CATEGORY_HINTS = [
    ("security", ("security", "vuln", "owasp", "secret", "pentest", "sast", "depend")),
    ("testing", ("test", "qa", "e2e", "coverage", "perf")),
    ("design", ("architect", "design", "api ", "schema", "data model", "uml")),
    ("planning", ("plan", "requirement", "research", "discover", "pattern")),
    ("generation", ("doc", "changelog", "migration", "scaffold")),
    ("implementation", ("develop", "engineer", "code", "implement", "frontend", "backend", "refactor")),
    ("quality", ("review", "quality", "debug", "lint", "accessib")),
]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "agent"


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Tolerant of files without frontmatter."""
    fm: dict[str, str] = {}
    body = text
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line and not line.strip().startswith("#"):
                    k, v = line.split(":", 1)
                    fm[k.strip().lower()] = v.strip().strip('"').strip("'")
            body = parts[2].strip()
    return fm, body


def _infer_category(name: str, desc: str) -> str:
    hay = f"{name} {desc}".lower()
    for cat, kws in CATEGORY_HINTS:
        if any(k in hay for k in kws):
            return cat
    return "general"


def _infer_type(name: str, desc: str) -> str:
    hay = f"{name} {desc}".lower()
    return "verifier" if any(h in hay for h in VERIFIER_HINTS) else "producer"


def convert_md(text: str) -> dict[str, Any]:
    fm, body = _split_frontmatter(text)
    name = fm.get("name") or "imported-agent"
    desc = fm.get("description", "")
    tools = fm.get("tools", "")
    tool_set = {t.strip().lower() for t in re.split(r"[,\s]+", tools) if t.strip()}
    tools_policy = "coding" if (tool_set & CODING_TOOLS) else "reasoning"
    return {
        "id": _slug(name),
        "name": name.replace("-", " ").title() if "-" in name and " " not in name else name,
        "type": _infer_type(name, desc),
        "category": _infer_category(name, desc),
        "role": _slug(name),
        "backend": "mock",
        "model": fm.get("model", ""),
        "description": desc[:200],
        "tools_policy": tools_policy,
        "system_prompt": body[:4000],
        "skill_refs": [],
    }


def import_dir(repo_path: str, source_dir: str) -> list[str]:
    from moira_core.repo_reader import AISdlcRepo
    repo = AISdlcRepo(repo_path)
    ids: list[str] = []
    for md in sorted(Path(source_dir).rglob("*.md")):
        try:
            text = md.read_text("utf-8", errors="replace")
            if "name:" not in text.lower() and not text.lstrip().startswith("---"):
                continue  # not a subagent file
            saved = repo.save_agent(convert_md(text))
            ids.append(saved["id"])
        except Exception:  # noqa: BLE001 — skip malformed files
            continue
    return ids


def main() -> int:
    sys.path.insert(0, str(Path(__file__).parent))
    if len(sys.argv) < 2:
        print(__doc__); return 2
    source = sys.argv[1]
    repo = sys.argv[2] if len(sys.argv) > 2 else "../../ai-sdlc"
    ids = import_dir(repo, source)
    print(f"Imported {len(ids)} agents from {source} -> {repo}")
    for i in ids:
        print(f"  {i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
