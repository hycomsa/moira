"""Capture what files a step changed in the dev repo — side-effect-free.

Used to attribute the diff a PRODUCER (coding) node produced in `context["cwd"]`,
so the cockpit can show "files changed by this step".

Mechanism: snapshot the whole working tree into a *throwaway* git index
(GIT_INDEX_FILE points at a temp file — the repo's real index is never touched),
`write-tree` to get a tree hash, do that before and after the node runs, then
`git diff <before> <after>`. No commits, no staging side effects, respects
.gitignore (so node_modules etc. are excluded).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

GIT_TIMEOUT = 15
MAX_PATCH_BYTES = 32000  # cap the unified diff so a huge change can't bloat the audit
CONTEXT_LINES = 8        # captured context; the cockpit trims this down (default 3) client-side


def _git(cwd: str, args: list[str], env: dict | None = None) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                              text=True, timeout=GIT_TIMEOUT, env=env)
    except Exception:  # noqa: BLE001 — git missing/slow degrades the feature, never the run
        return None


def is_git_repo(cwd: str | None) -> bool:
    return bool(cwd) and (Path(cwd) / ".git").exists()


def tree_snapshot(cwd: str | None) -> Optional[str]:
    """Hash of the current working tree, via a throwaway index. None on failure."""
    if not is_git_repo(cwd):
        return None
    idx = tempfile.NamedTemporaryFile(prefix="moira-idx-", delete=False)
    idx.close()
    os.unlink(idx.name)  # git creates a fresh index here; a 0-byte file breaks `add`
    try:
        env = {**os.environ, "GIT_INDEX_FILE": idx.name}
        add = _git(cwd, ["add", "-A"], env=env)
        if add is None or add.returncode != 0:
            return None
        wt = _git(cwd, ["write-tree"], env=env)
        if wt is None or wt.returncode != 0:
            return None
        tree = wt.stdout.strip()
        return tree or None
    finally:
        try:
            os.unlink(idx.name)
        except OSError:
            pass


def diff_between(cwd: str, tree_a: str, tree_b: str) -> dict[str, Any]:
    """{files:[{path,status,additions,deletions}], patch, truncated} between two trees."""
    files: list[dict[str, Any]] = []
    numstat = _git(cwd, ["diff", "--numstat", tree_a, tree_b])
    status = _git(cwd, ["diff", "--name-status", tree_a, tree_b])
    status_by_path: dict[str, str] = {}
    if status and status.returncode == 0:
        for line in status.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status_by_path[parts[-1]] = parts[0]
    if numstat and numstat.returncode == 0:
        for line in numstat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                add, dele, path = parts[0], parts[1], parts[2]
                files.append({
                    "path": path,
                    "status": status_by_path.get(path, "M"),
                    "additions": None if add == "-" else int(add),  # '-' = binary
                    "deletions": None if dele == "-" else int(dele),
                })
    # capture with generous context so the cockpit can expand around changes;
    # it defaults to showing 3 lines and lets the reviewer widen up to this.
    patch_proc = _git(cwd, ["diff", f"-U{CONTEXT_LINES}", tree_a, tree_b])
    patch = patch_proc.stdout if patch_proc and patch_proc.returncode == 0 else ""
    truncated = len(patch) > MAX_PATCH_BYTES
    if truncated:
        patch = patch[:MAX_PATCH_BYTES] + "\n… (diff truncated)\n"
    return {"files": files, "patch": patch, "truncated": truncated}


def artifact_id(files: list[dict[str, Any]]) -> Optional[str]:
    """Derive the AI SDLC artifact id authored/changed by a skill, from its file
    paths (intent-specs/{ID}/…, func-specs/{ID}/…, adrs/{ID}.md). None if none."""
    import re
    for f in files:
        p = f.get("path", "")
        m = re.search(r"\.ai/context/(?:intent-specs|func-specs)/([^/]+)/", p) \
            or re.search(r"(?:^|/)(?:intent-specs|func-specs)/([^/]+)/", p)
        if m:
            return m.group(1)
        m = re.search(r"(?:^|/)adrs/(ADR-[^/]+)\.md$", p)
        if m:
            return m.group(1)
    return None


def artifact_id_from_changes(changes: dict[str, Any]) -> Optional[str]:
    """Best artifact id for the auto-chain: prefer a path-derived id (intent/func/adr);
    otherwise, when a requirements file changed, pick the first NEW requirement id added
    in the patch (e.g. `+### REQ-APP-12 — …`) so a discover→func-spec chain can hand the
    fresh REQ-ID to ba@shape-func-spec."""
    import re
    files = changes.get("files") or []
    by_path = artifact_id(files)
    if by_path:
        return by_path
    touches_reqs = any("requirements" in (f.get("path", "")) for f in files)
    if touches_reqs:
        patch = changes.get("patch") or ""
        for line in patch.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            m = re.search(r"\b(REQ-[A-Z0-9]+-[0-9]+)\b", line)
            if m:
                return m.group(1)
    return None


def changes_in(cwd: str | None, before: Optional[str], after: Optional[str]) -> Optional[dict[str, Any]]:
    """Convenience: diff of what changed between two snapshots; None if nothing/failed."""
    if not cwd or not before or not after or before == after:
        return None
    d = diff_between(cwd, before, after)
    return d if d["files"] else None
