"""Deterministic, git-native FUNC completeness from the Zdzira-compatible task backlog.

Reads epic + task markdown files (one markdown per task) under the repo's `tickets_root` and computes
how complete a func-spec is: tasks done/total and which acceptance criteria are decomposed / done /
tested. Pure and dependency-free — NO LLM. The join key across spec, test-plan and tasks is the
acceptance-criterion id `AC-*`.

See `.ai/standards/pm/task-epic-conventions.md` for the on-disk format (compatible with Zdzira PM).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

AC_RE = re.compile(r"\bAC-[A-Z0-9]+(?:-[0-9]+)+\b")
DONE_STATUSES = {"done", "closed"}


def func_slug(func_id: str) -> str:
    """FUNC-APP-onboarding -> func-app-onboarding (kebab-case folder name)."""
    return re.sub(r"[^a-z0-9]+", "-", func_id.lower()).strip("-")


def _value(v: str) -> Any:
    """Parse a frontmatter scalar or inline flow list ([a, b])."""
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [x.strip().strip("\"'") for x in inner.split(",") if x.strip()] if inner else []
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        return v[1:-1]
    return v


def _frontmatter(text: str) -> dict[str, Any]:
    """Parse the TOP-LEVEL scalar / flow-list keys of a `---`-delimited frontmatter block.

    Nested mappings (jira:, history:, comments:) and block sequences are skipped — the task/epic schema
    only needs flat fields (id, type, status, parent_epic_id, source_func, acceptance_criteria, …)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, Any] = {}
    for ln in text[3:end].splitlines():
        if not ln.strip() or ln[:1] in (" ", "\t") or ln.lstrip().startswith(("- ", "#")):
            continue
        if ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        k = k.strip()
        if k:
            out[k] = _value(v)
    return out


def _toplevel_scalars(text: str) -> dict[str, Any]:
    """Top-level `key: value` lines of a plain YAML file (skips nested/indented lines)."""
    out: dict[str, Any] = {}
    for ln in text.splitlines():
        if not ln or ln[:1] in (" ", "\t") or ln.lstrip().startswith(("- ", "#")):
            continue
        if ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        if k.strip():
            out[k.strip()] = _value(v)
    return out


def project_config(root: Path) -> dict[str, Any]:
    """Read project_key + tickets_root from .project-config.md (preferred) or .yaml (legacy)."""
    for name in (".project-config.md", ".project-config.yaml"):
        f = root / name
        if f.exists():
            txt = f.read_text(encoding="utf-8", errors="replace")
            cfg = _frontmatter(txt) if name.endswith(".md") else _toplevel_scalars(txt)
            return {"project_key": cfg.get("project_key") or "PROJ",
                    "tickets_root": cfg.get("tickets_root") or "backlog"}
    return {"project_key": "PROJ", "tickets_root": "backlog"}


def _backlog_dir(repo) -> Optional[Path]:
    d = Path(repo.root) / project_config(Path(repo.root))["tickets_root"]
    return d if d.exists() else None


def epic_dir_for_func(repo, func_id: str) -> Optional[Path]:
    """The epic folder for a FUNC: the one whose epic-*.md has source_func == func_id; else the
    <func-slug> folder if it exists."""
    base = _backlog_dir(repo)
    if not base:
        return None
    for epic in sorted(base.glob("*/epic-*.md")):
        fm = _frontmatter(epic.read_text(encoding="utf-8", errors="replace"))
        if fm.get("source_func") == func_id:
            return epic.parent
    slug = base / func_slug(func_id)
    return slug if slug.is_dir() else None


def list_tasks(repo, func_id: str) -> list[dict]:
    """Parsed task files for a FUNC's epic (excludes the epic file itself)."""
    d = epic_dir_for_func(repo, func_id)
    if not d:
        return []
    tasks: list[dict] = []
    for f in sorted(d.glob("*.md")):
        if f.name.startswith("epic-"):
            continue
        fm = _frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        if fm.get("type") == "epic":
            continue
        acs = fm.get("acceptance_criteria")
        tasks.append({
            "id": fm.get("id") or f.stem,
            "title": fm.get("title") or "",
            "status": (fm.get("status") or "todo"),
            "acceptance_criteria": acs if isinstance(acs, list) else ([acs] if acs else []),
        })
    return tasks


def _spec_text(repo, func_id: str) -> str:
    main = Path(repo.ctx) / "func-specs" / func_id / "func-spec.md"
    if main.exists():
        return main.read_text(encoding="utf-8", errors="replace")
    return repo.read_func_spec(func_id) or ""


def completeness(repo, func_id: str) -> dict:
    """Deterministic FUNC completeness from the backlog + spec + test-plan."""
    ac_total = set(AC_RE.findall(_spec_text(repo, func_id)))
    ac_tested = set(AC_RE.findall(repo.read_test_plan(func_id) or "")) & ac_total

    tasks = list_tasks(repo, func_id)
    by_status: dict[str, int] = {}
    ac_in_tasks: set[str] = set()
    ac_done: set[str] = set()
    done = 0
    for t in tasks:
        st = t["status"]
        by_status[st] = by_status.get(st, 0) + 1
        ac_in_tasks.update(t["acceptance_criteria"])
        if st in DONE_STATUSES:
            done += 1
            ac_done.update(t["acceptance_criteria"])
    total = len(tasks)

    if total and done == total and ac_total <= ac_done:
        level = "complete"
    elif done or ac_in_tasks:
        level = "partial"
    else:
        level = "none"

    return {
        "func_id": func_id,
        "has_epic": epic_dir_for_func(repo, func_id) is not None,
        "tasks": {"total": total, "done": done, "by_status": by_status},
        "ac": {
            "total": len(ac_total),
            "in_tasks": len(ac_total & ac_in_tasks),
            "done": len(ac_total & ac_done),
            "tested": len(ac_tested),
        },
        "build_pct": round(done / total, 3) if total else 0.0,
        "level": level,
    }


def traceability(repo, func_id: str, lineage: Optional[list[str]] = None) -> dict:
    """Assemble the deterministic Spec ↔ Tests ↔ Tasks ↔ Lineage trace for a FUNC."""
    spec_art = repo.resolve_artifact(func_id) if func_id else None
    if spec_art:
        ups = [x for x in spec_art.get("lineage", []) if not x.startswith("FUNC")]
    elif lineage and func_id:
        ups = [x for x in lineage if x != func_id and not x.startswith("FUNC")]
    else:
        ups = []
    resolved = sum(1 for u in ups if repo.resolve_artifact(u))

    comp = completeness(repo, func_id) if func_id else None
    has_tests = bool(repo.read_test_plan(func_id)) if func_id else False
    return {
        "func_id": func_id,
        "spec": {"present": spec_art is not None,
                 "title": spec_art.get("title") if spec_art else None},
        "tests": {"present": has_tests,
                  "ac_covered": comp["ac"]["tested"] if comp else 0,
                  "ac_total": comp["ac"]["total"] if comp else 0},
        "tasks": comp,
        "lineage": {"present": bool(ups), "refs": ups, "resolved": resolved},
    }
