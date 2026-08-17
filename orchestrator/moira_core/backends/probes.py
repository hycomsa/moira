"""Backend install/login probes (QW4/ADR-012).

Answers "can this backend actually run work RIGHT NOW?" before a run is
launched, instead of letting a missing or logged-out CLI surface minutes later
as a failed node after retries. Stdlib-only, like the rest of the core.

Three honesty rules:
- a probe reports only what it verified; a failed probe yields
  `authenticated=None` (unknown) — never a claimed state
- unknown NEVER blocks a launch; only definite unusability does
  (not installed, or auth status explicitly says logged out)
- every negative result carries a copy-paste `hint` command that fixes it

Results are cached with an asymmetric TTL (healthy long, broken short) so a
just-fixed login is noticed fast while a healthy setup isn't re-probed on
every /api/health poll.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable

TTL_OK = 300.0     # healthy result: re-probe rarely
TTL_BAD = 30.0     # broken result: notice the fix fast (asymmetry on purpose)
PROBE_TIMEOUT = 10  # seconds per probe subprocess

CLAUDE_BINARY = "claude"
INSTALL_HINT_CLAUDE = "npm install -g @anthropic-ai/claude-code"
LOGIN_HINT_CLAUDE = "claude auth login"
INSTALL_HINT_LITELLM = "pip install 'moira-orchestrator[backends]'"


@dataclass
class ProbeResult:
    backend: str
    installed: bool = False
    version: str = ""
    authenticated: bool | None = None   # None = unknown — never blocks a launch
    detail: str = ""
    hint: str = ""                      # copy-paste command that fixes the problem
    ts: float = 0.0

    def healthy(self) -> bool:
        return self.installed and self.authenticated is not False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_cache: dict[str, ProbeResult] = {}
_lock = threading.Lock()


def probe(name: str, force: bool = False,
          runner: Callable = subprocess.run,
          which: Callable = shutil.which,
          now: Callable[[], float] = time.time) -> ProbeResult:
    """Cached probe of one backend (see module docstring for the cache policy)."""
    with _lock:
        cached = _cache.get(name)
    if cached and not force:
        ttl = TTL_OK if cached.healthy() else TTL_BAD
        if now() - cached.ts < ttl:
            return cached
    res = _fresh(name, runner, which, now)
    with _lock:
        _cache[name] = res
    return res


def _fresh(name: str, runner: Callable, which: Callable,
           now: Callable[[], float]) -> ProbeResult:
    ts = now()
    if name == "claude_code":
        return _probe_claude(runner, which, ts)
    if name == "litellm":
        installed = importlib.util.find_spec("litellm") is not None
        return ProbeResult(
            backend=name, installed=installed, ts=ts,
            # auth is per-provider API keys — key validity can't be verified
            # without a paid call, so we never claim a login state here
            authenticated=None,
            detail="" if installed else "python package 'litellm' not importable",
            hint="" if installed else INSTALL_HINT_LITELLM)
    # mock — and any backend we have no probe for: benign by design
    # (never block what we cannot see; the run will report honestly if it fails)
    return ProbeResult(backend=name, installed=True,
                       authenticated=True if name == "mock" else None,
                       detail=("deterministic (offline)" if name == "mock"
                               else "no probe for this backend"), ts=ts)


def _probe_claude(runner: Callable, which: Callable, ts: float) -> ProbeResult:
    if not which(CLAUDE_BINARY):
        return ProbeResult(backend="claude_code", installed=False, ts=ts,
                           detail=f"'{CLAUDE_BINARY}' not found on PATH",
                           hint=INSTALL_HINT_CLAUDE)
    version = ""
    try:  # banner: "2.1.233 (Claude Code)"
        p = runner([CLAUDE_BINARY, "--version"], capture_output=True, text=True,
                   timeout=PROBE_TIMEOUT)
        out = (p.stdout or "").strip()
        version = out.split()[0] if out else ""
    except Exception:  # noqa: BLE001 — version is cosmetic, never fails the probe
        pass
    authenticated: bool | None = None
    detail, hint = "", ""
    try:  # `claude auth status` prints JSON: {"loggedIn": bool, "email": ...}
        p = runner([CLAUDE_BINARY, "auth", "status"], capture_output=True,
                   text=True, timeout=PROBE_TIMEOUT)
        info = json.loads(p.stdout or "{}")
        if isinstance(info, dict) and "loggedIn" in info:
            authenticated = bool(info["loggedIn"])
            if authenticated:
                detail = f"logged in as {info.get('email') or info.get('authMethod') or 'unknown'}"
            else:
                detail, hint = "not logged in", LOGIN_HINT_CLAUDE
    except Exception as e:  # noqa: BLE001 — unknown, not false: don't block on a broken probe
        detail = f"auth probe failed: {e}"
    return ProbeResult(backend="claude_code", installed=True, version=version,
                       authenticated=authenticated, detail=detail, hint=hint, ts=ts)


def all_probes(names: Iterable[str], **kw: Any) -> dict[str, dict[str, Any]]:
    return {n: probe(n, **kw).to_dict() for n in names}


def launch_blockers(backend_names: Iterable[str], **kw: Any) -> list[str]:
    """Human-readable reasons a launch must NOT proceed ([] = go).

    Only definite unusability blocks; `authenticated=None` (unknown) passes —
    a broken probe must never take the product down with it."""
    out: list[str] = []
    for name in sorted(set(backend_names)):
        r = probe(name, **kw)
        if not r.installed:
            out.append(f"backend '{name}' is not installed — fix: {r.hint or r.detail}")
        elif r.authenticated is False:
            out.append(f"backend '{name}' is not logged in — fix: {r.hint}")
    return out


def warm(names: Iterable[str]) -> None:
    """Fire-and-forget cache warm-up at API start (daemon thread — never blocks boot)."""
    def _run() -> None:
        for n in list(names):
            try:
                probe(n)
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=_run, daemon=True, name="moira-probe-warm").start()
