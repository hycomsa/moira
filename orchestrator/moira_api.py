"""Moira HTTP API — the sidecar the cockpit (React/Tauri) talks to.

Zero-dependency (stdlib http.server) on purpose, consistent with the core.
Exposes the same operations as the CLI so the cockpit renders real run data:

  GET  /api/health
  GET  /api/runs
  GET  /api/runs/{id}            -> {run, events, audit, cost}
  GET  /api/inbox
  POST /api/runs                 {func_id, repo?, owner?, analysis_gate?, impl_gate?}
  POST /api/runs/{id}/approve    {by?, confirm?}
  POST /api/runs/{id}/reject     {by?, feedback?}
  GET  /                         -> serves the cockpit frontend (static dir)

Run:  python3 moira_api.py [--port 8765] [--repo ../../ai-sdlc] [--static ../cockpit/dist]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from moira_core import (  # noqa: E402
    AISdlcRepo, BackendRegistry, Engine, GateConfig, GateDecision, GateMode,
    MockBackend, Node, NodeType, Pipeline, Store, available_pipelines, client_gated_pipeline,
    default_sdlc_pipeline, make_run_store,
)
from moira_core.gates import simulate_routing  # noqa: E402
from moira_core.backends import ClaudeCodeBackend, LiteLLMBackend  # noqa: E402

DB = os.environ.get("MOIRA_DB", ".moira/moira.sqlite")
REPO = None
STATIC = None
LOG_PATH = None
log = logging.getLogger("moira")
# how many times to retry a discovery skill node before escalating to a human gate
# (1 = 2 attempts). With the short skill timeout this bounds the "stuck" window.
try:
    SKILL_RETRIES = int(os.environ.get("MOIRA_SKILL_RETRIES", "1"))
except (TypeError, ValueError):
    SKILL_RETRIES = 1


def setup_logging() -> None:
    """Log to a file (MOIRA_LOG, default next to the DB) AND stdout, so the desktop
    app has a logfile and `run-cockpit.sh` shows live events in the terminal."""
    global LOG_PATH
    LOG_PATH = os.environ.get("MOIRA_LOG") or str(Path(DB).resolve().parent / "moira.log")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    try:
        Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_PATH)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        LOG_PATH = None
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(sh)


def background(owner: str, run_id: str, fn) -> None:
    """Drive a run OFF the request thread so the HTTP call returns immediately
    (the cockpit then polls). The thread uses its OWN Store + Engine (SQLite is
    per-thread). A crash marks the run failed (not stuck 'running') and is logged."""
    def _run():
        store = open_store()
        try:
            res = fn(Engine(store, registry(), owner=owner))
            log.info("run %s -> %s", run_id, getattr(getattr(res, "status", None), "value", res))
        except Exception:  # noqa: BLE001
            log.error("background run %s failed:\n%s", run_id, traceback.format_exc())
            try:
                from moira_core.models import Event
                store.update_run_status(run_id, "failed")
                store.append_event(Event(run_id=run_id, kind="run.end",
                                         message="Run failed (background error) — see Activity → Sidecar logs"))
            except Exception:  # noqa: BLE001
                pass
        finally:
            store.close()
    threading.Thread(target=_run, daemon=True).start()


def recover_orphans() -> None:
    """On startup, any run still 'running' is an orphan from a previous process
    (background drive threads don't survive a restart) → mark it failed so it
    doesn't sit stuck 'running' forever."""
    store = open_store()
    try:
        from moira_core.models import Event
        n = 0
        for r in store.list_runs():
            if r["status"] == "running":
                store.update_run_status(r["run_id"], "failed")
                store.append_event(Event(run_id=r["run_id"], kind="run.end",
                                         message="Run interrupted (sidecar restarted)"))
                n += 1
        if n:
            log.info("recovered %d orphaned running run(s) -> failed", n)
    finally:
        store.close()


def live_path_for(run_id: str) -> str | None:
    """Path to a run's live-stream buffer (<dir of DB>/live/<run_id>.jsonl). The
    claude backend appends reasoning/tool/usage records here; /api/runs/{id}/live tails it."""
    try:
        d = Path(DB).resolve().parent / "live"
        d.mkdir(parents=True, exist_ok=True)
        return str(d / f"{run_id}.jsonl")
    except OSError:
        return None


def open_store():
    """Build the configured run store (primary + any export sinks).

    Honors MOIRA_PRIMARY / MOIRA_PG_DSN / MOIRA_GIT_EXPORT (see persistence.py).
    REPO is the git-sink fallback repo; the sink resolves each run's actual
    workspace repo_path at write time.
    """
    return make_run_store(DB, repo_path=REPO)


def registry() -> BackendRegistry:
    reg = BackendRegistry()
    reg.register(MockBackend())
    reg.register(ClaudeCodeBackend())
    reg.register(LiteLLMBackend())
    return reg


def ensure_default_workspace(store: Store) -> None:
    if not store.get_workspace("default"):
        store.create_workspace("default", "Default", REPO or ".", None)


def ws_repo(store: Store, ws_id: str) -> str:
    ws = store.get_workspace(ws_id)
    return (ws["repo_path"] if ws else REPO) or REPO or "."


def ws_code_path(store: Store, ws_id: str) -> str | None:
    ws = store.get_workspace(ws_id)
    return ws.get("code_path") if ws else None


# ---- read-only file browsing (sandboxed to the workspace roots) ----------- #
FILE_MAX = 200_000  # bytes — refuse to stream larger files


def _file_root(store: Store, ws_id: str, which: str) -> str | None:
    if which == "repo":
        return ws_repo(store, ws_id)
    return ws_code_path(store, ws_id) or ws_repo(store, ws_id)


def _safe_path(root: str, rel: str) -> str | None:
    """Resolve `rel` under `root`; None if it escapes the root (path traversal)."""
    base = os.path.realpath(root)
    tgt = os.path.realpath(os.path.join(base, rel or ""))
    return tgt if (tgt == base or tgt.startswith(base + os.sep)) else None


def list_dir(store: Store, ws_id: str, rel: str, which: str) -> dict | None:
    root = _file_root(store, ws_id, which)
    if not root:
        return None
    tgt = _safe_path(root, rel)
    if not tgt or not os.path.isdir(tgt):
        return None
    entries = []
    for name in sorted(os.listdir(tgt)):
        if name == ".git":
            continue
        p = os.path.join(tgt, name)
        is_dir = os.path.isdir(p)
        entries.append({"name": name, "type": "dir" if is_dir else "file",
                        "size": 0 if is_dir else os.path.getsize(p)})
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"root": root, "path": rel, "entries": entries}


def read_file(store: Store, ws_id: str, rel: str, which: str) -> dict | None:
    root = _file_root(store, ws_id, which)
    if not root:
        return None
    tgt = _safe_path(root, rel)
    if not tgt or not os.path.isfile(tgt):
        return None
    size = os.path.getsize(tgt)
    raw = open(tgt, "rb").read(FILE_MAX)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel, "abs": tgt, "binary": True, "text": "", "truncated": False}
    return {"path": rel, "abs": tgt, "binary": False, "text": text,
            "truncated": size > FILE_MAX}


def repo_pipelines(store: Store, ws_id: str) -> list[dict]:
    """Built pipelines (resolved nodes) from the repo; fallback to built-ins."""
    rp = ws_repo(store, ws_id)
    repo = AISdlcRepo(rp) if rp else None
    out = []
    if repo and repo.exists():
        for pdef in repo.list_pipeline_defs():
            try:
                out.append(repo.build_pipeline(pdef).to_dict())
            except Exception:  # noqa: BLE001
                continue
    if not out:
        out = [p.to_dict() for p in available_pipelines()]
    return out


def load_pipeline(store: Store, ws_id: str, body: dict, func_id: str):
    """Resolve the pipeline to run from the request body (repo pipeline_id,
    'client-gated' shortcut, or the default), applying a backend override."""
    backend = body.get("backend", "mock")
    pid = body.get("pipeline_id")
    rp = ws_repo(store, ws_id)
    repo = AISdlcRepo(rp) if rp else None
    if pid and repo and repo.exists():
        pdef = repo.get_pipeline_def(pid)
        if pdef:
            pipe = repo.build_pipeline(pdef, func_ref=func_id)
            for n in pipe.nodes:
                if n.type.value != "gate":
                    n.backend = backend
                    if not n.spec_ref:
                        n.spec_ref = func_id
            return pipe
    if body.get("pipeline") == "client-gated":
        return client_gated_pipeline(func_ref=func_id, backend=backend)
    return default_sdlc_pipeline(
        func_ref=func_id,
        analysis_gate=GateMode(body.get("analysis_gate", "auto")),
        impl_gate=GateMode(body.get("impl_gate", "hybrid")),
        backend=backend,
    )


def context_for(func_id: str, repo_path: str | None) -> dict:
    if repo_path:
        repo = AISdlcRepo(repo_path)
        if repo.exists():
            ctx = repo.context_for(func_id)
            if not ctx.get("spec_text"):
                ctx["spec_text"] = f"(no spec text for {func_id})"
            return ctx
    return {"func_id": func_id, "spec_text": f"Demo spec for {func_id}", "lineage": [func_id]}


def run_metrics(store: Store, run_id: str) -> dict:
    """Per-run rollup for summaries: cost, tokens, total node time, leading model.

    'Leading model' = the most-used effective label across steps (the explicit
    per-node model when set, else the backend) — so cross-model runs surface e.g.
    'opus' while plain ones show 'claude_code'/'mock'.
    """
    import collections
    ti = to = 0
    usd = dur = 0.0
    labels: collections.Counter = collections.Counter()
    for r in store.audit_records(run_id):
        c = r.get("cost") or {}
        ti += c.get("tokens_in", 0) or 0
        to += c.get("tokens_out", 0) or 0
        usd += c.get("usd", 0) or 0
        dur += r.get("duration", 0) or 0
        inp = r.get("input") or {}
        m, be = inp.get("model"), inp.get("backend")
        label = m if (m and m != "(default)") else be
        if label:
            labels[label] += 1
    return {"usd": round(usd, 4), "tokens": ti + to, "duration": round(dur, 1),
            "model": labels.most_common(1)[0][0] if labels else "—"}


def eval_target_for_run(store: Store, run_id: str) -> tuple[str, str, list]:
    """Assemble a quality-eval target from a run's produced outputs.
    Returns (target_text, spec_ref, lineage)."""
    parts: list[str] = []
    spec_ref, lineage = "", []
    for a in store.audit_records(run_id):
        inp = a.get("input") or {}
        if inp.get("spec_ref") and not spec_ref:
            spec_ref = inp["spec_ref"]
        if a.get("lineage") and not lineage:
            lineage = a["lineage"]
        out = a.get("output") or {}
        if not out:
            continue
        seg: list[str] = []
        for k in ("result", "summary"):
            if isinstance(out.get(k), str) and out[k].strip():
                seg.append(out[k].strip())
        for d in (a.get("decisions") or [])[:8]:
            seg.append(f"- {d}")
        if isinstance(out.get("patch"), str) and out["patch"].strip():
            seg.append("changed files:\n" + out["patch"][:4000])
        if seg:
            parts.append(f"## {a.get('node_name', a.get('node_id', '?'))}\n" + "\n".join(seg))
    return "\n\n".join(parts)[:12000], spec_ref, (lineage or ([spec_ref] if spec_ref else []))


def spend_rollup(store: Store, ws_id: str) -> dict:
    """Aggregate run_metrics across the workspace's runs, by model and by owner,
    plus this month's total — feeds the Overview Spend panel. The per-workspace
    budget lives client-side (localStorage); the cockpit compares month_usd to it."""
    import collections
    import time as _time
    by_model: collections.Counter = collections.Counter()
    by_owner: collections.Counter = collections.Counter()
    total = month_total = 0.0
    runs_n = 0
    month = _time.strftime("%Y-%m")
    for r in store.list_runs(ws_id):
        m = run_metrics(store, r["run_id"])
        usd = m["usd"] or 0.0
        total += usd
        runs_n += 1
        by_model[m["model"]] += usd
        by_owner[r.get("owner", "—")] += usd
        created = r.get("created_at") or 0
        try:
            if created and _time.strftime("%Y-%m", _time.localtime(created)) == month:
                month_total += usd
        except Exception:  # noqa: BLE001
            pass
    return {
        "total_usd": round(total, 4), "runs": runs_n, "month": month,
        "month_usd": round(month_total, 4),
        "by_model": [{"label": k, "usd": round(v, 4)} for k, v in by_model.most_common()],
        "by_owner": [{"label": k, "usd": round(v, 4)} for k, v in by_owner.most_common()],
    }


def _summarize_check(rec: dict) -> str:
    o = rec.get("output") or {}
    if isinstance(o.get("passed"), bool):
        return "check passed" if o["passed"] else "check FAILED"
    if isinstance(o.get("summary"), str):
        return o["summary"]
    return (rec.get("decisions") or [rec.get("status", "")])[0]


def mobile_inbox(store: Store) -> list[dict]:
    """All runs waiting at a gate across every workspace, with the evidence a
    reviewer needs to decide from a phone (checks + changed-file count + metrics)."""
    out = []
    for ws in store.list_workspaces():
        for r in store.list_runs(ws["id"]):
            if r["status"] != "waiting_gate":
                continue
            recs = store.audit_records(r["run_id"])
            gate = next((a for a in reversed(recs) if a.get("status") == "waiting_gate"), None)
            g_in = (gate or {}).get("input", {})
            checks = []
            for cid in g_in.get("consumes", []):
                rec = next((a for a in recs if a["node_id"] == cid), None)
                if rec:
                    checks.append({"name": rec["node_name"], "status": rec["status"],
                                   "summary": _summarize_check(rec)})
            files = sum(len((a.get("output") or {}).get("files", [])) for a in recs)
            evs = store.events(r["run_id"])
            w = next((e for e in reversed(evs) if e["kind"] in ("gate.wait", "node.escalate")), None)
            out.append({"run_id": r["run_id"], "workspace": ws["name"], "pipeline": r["pipeline_id"],
                        "persona": g_in.get("persona", ""), "message": w["message"] if w else "",
                        "checks": checks, "changed_files": files, **run_metrics(store, r["run_id"])})
    return out


def traceability(store: Store, ws_id: str) -> list[dict]:
    """Per func-spec: its lineage chain + the runs that targeted it (both directions)."""
    rp = ws_repo(store, ws_id)
    repo = AISdlcRepo(rp) if rp else None
    if not (repo and repo.exists()):
        return []
    runs_by_func: dict[str, list] = {}
    for r in store.list_runs(ws_id):
        recs = store.audit_records(r["run_id"])
        fid = next((a.get("input", {}).get("spec_ref") for a in recs
                    if a.get("input", {}).get("spec_ref")), None)
        if fid:
            runs_by_func.setdefault(fid, []).append(
                {"run_id": r["run_id"], "status": r["status"], **run_metrics(store, r["run_id"])})
    out = []
    for fid in repo.list_func_specs():
        text = repo.read_func_spec(fid) or ""
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines()
                      if ln.startswith("#")), fid)
        out.append({"id": fid, "title": title, "lineage": repo.trace_lineage(text, fid),
                    "runs": runs_by_func.get(fid, [])})
    return out


def run_payload(store: Store, run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        return {}
    return {
        "run": run,
        "pipeline": json.loads(run["pipeline"]),
        "events": store.events(run_id),
        "audit": store.audit_records(run_id),
        "cost": store.run_cost(run_id),
        "state": store.get_run_state(run_id),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MoiraAPI/0.1"

    # ---- helpers ---------------------------------------------------------- #
    def _send(self, code: int, body: dict | list | None = None, ctype="application/json"):
        data = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _send_text(self, code: int, text: str, ctype="text/html; charset=utf-8"):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, *a):  # quieter
        pass

    def do_OPTIONS(self):
        self._send(204)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        ws_id = parse_qs(parsed.query).get("ws", ["default"])[0]
        store = open_store()
        ensure_default_workspace(store)
        try:
            repo = AISdlcRepo(ws_repo(store, ws_id))
            if path.startswith("/api/agents/"):
                ok = repo.delete_agent(path.split("/api/agents/", 1)[1])
                return self._send(200 if ok else 404, {"deleted": ok})
            if path.startswith("/api/pipelines/"):
                ok = repo.delete_pipeline_def(path.split("/api/pipelines/", 1)[1])
                return self._send(200 if ok else 404, {"deleted": ok})
            return self._send(404, {"error": "unknown endpoint"})
        finally:
            store.close()

    # ---- GET -------------------------------------------------------------- #
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        ws_id = parse_qs(parsed.query).get("ws", ["default"])[0]
        store = open_store()
        ensure_default_workspace(store)
        try:
            if path == "/api/health":
                primary = os.environ.get("MOIRA_PRIMARY", "sqlite")
                git_on = os.environ.get("MOIRA_GIT_EXPORT", "0") not in ("", "0", "false", "False")
                persistence = primary + (" + git" if git_on else "")
                cc = ClaudeCodeBackend()
                return self._send(200, {"ok": True, "backends": registry().available(),
                                        "repo": REPO, "persistence": persistence, "log": LOG_PATH,
                                        "claude": cc.available(), "version": "0.1",
                                        "config": {"skill_timeout": cc.skill_timeout, "skill_max_turns": cc.skill_max_turns,
                                                   "skill_retries": SKILL_RETRIES, "claude_timeout": cc.timeout,
                                                   "heavy_timeout": cc.heavy_timeout,
                                                   "debug": os.environ.get("MOIRA_DEBUG") not in (None, "", "0", "false", "False")}})
            if path == "/api/logs":
                n = int((parse_qs(parsed.query).get("tail", ["200"])[0]) or 200)
                text = ""
                try:
                    if LOG_PATH and os.path.exists(LOG_PATH):
                        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                            text = "".join(f.readlines()[-n:])
                except OSError:
                    pass
                return self._send(200, {"path": LOG_PATH, "log": text})
            if path == "/api/workspaces":
                return self._send(200, {"workspaces": store.list_workspaces()})
            if path == "/api/runs":
                runs = store.list_runs(ws_id)
                for r in runs:
                    r.update(run_metrics(store, r["run_id"]))
                return self._send(200, {"runs": runs})
            if path == "/api/inbox":
                waiting = [r for r in store.list_runs(ws_id) if r["status"] == "waiting_gate"]
                items = []
                for r in waiting:
                    evs = store.events(r["run_id"])
                    w = next((e for e in reversed(evs)
                              if e["kind"] in ("gate.wait", "node.escalate")), None)
                    # surface the artifact the persona must review (client gate wedge)
                    gate_rec = next((a for a in reversed(store.audit_records(r["run_id"]))
                                     if a.get("status") == "waiting_gate"), None)
                    g_in = (gate_rec or {}).get("input", {})
                    items.append({"run_id": r["run_id"], "owner": r["owner"],
                                  "message": w["message"] if w else "",
                                  "node_id": w["node_id"] if w else "",
                                  "persona": g_in.get("persona", ""),
                                  "audience": g_in.get("audience", "technical"),
                                  "consumes": g_in.get("consumes", []),
                                  "review": g_in.get("review", {})})
                return self._send(200, {"inbox": items})
            if path == "/api/stats":
                runs = store.list_runs(ws_id)
                by = {}
                for r in runs:
                    by[r["status"]] = by.get(r["status"], 0) + 1
                total_cost = sum(store.run_cost(r["run_id"])["usd"] for r in runs)
                return self._send(200, {
                    "total": len(runs),
                    "succeeded": by.get("succeeded", 0),
                    "waiting_gate": by.get("waiting_gate", 0),
                    "failed": by.get("failed", 0) + by.get("rejected", 0),
                    "running": by.get("running", 0),
                    "total_cost_usd": round(total_cost, 4),
                })
            if path == "/api/spend":
                return self._send(200, spend_rollup(store, ws_id))
            if path == "/api/regulations":
                rp = ws_repo(store, ws_id)
                repo = AISdlcRepo(rp) if rp else None
                regs = repo.list_regulations() if repo and repo.exists() else []
                return self._send(200, {"regulations": regs})
            if path == "/api/skills":
                rp = ws_repo(store, ws_id)
                repo = AISdlcRepo(rp) if rp else None
                skills = repo.list_skills() if repo and repo.exists() else []
                return self._send(200, {"skills": skills})
            if path == "/api/agents":
                rp = ws_repo(store, ws_id)
                repo = AISdlcRepo(rp) if rp else None
                agents = repo.list_agents() if repo and repo.exists() else []
                return self._send(200, {"agents": agents})
            if path.startswith("/api/agents/"):
                rp = ws_repo(store, ws_id)
                repo = AISdlcRepo(rp)
                agent = repo.get_agent(path.split("/api/agents/", 1)[1])
                return self._send(200 if agent else 404, agent or {"error": "not found"})
            if path == "/api/mobile/inbox":
                return self._send(200, {"inbox": mobile_inbox(store)})
            if path == "/api/funcs":
                # git-native func-specs from the repo (the grounded "what to build")
                rp = ws_repo(store, ws_id)
                repo = AISdlcRepo(rp) if rp else None
                funcs = []
                if repo and repo.exists():
                    for fid in repo.list_func_specs():
                        text = repo.read_func_spec(fid) or ""
                        title = next((ln.lstrip("# ").strip() for ln in text.splitlines()
                                      if ln.startswith("#")), fid)
                        funcs.append({"id": fid, "title": title,
                                      "lineage": repo.trace_lineage(text, fid)})
                return self._send(200, {"funcs": funcs})
            if path == "/api/traceability":
                return self._send(200, {"funcs": traceability(store, ws_id)})
            if path == "/api/pipelines":
                pipes = repo_pipelines(store, ws_id)
                return self._send(200, {"pipelines": pipes})
            if path.startswith("/api/pipelines/"):
                # raw YAML def (for the builder)
                rp = ws_repo(store, ws_id)
                pdef = AISdlcRepo(rp).get_pipeline_def(path.split("/api/pipelines/", 1)[1])
                return self._send(200 if pdef else 404, pdef or {"error": "not found"})
            if path == "/api/activity":
                # event feed across the workspace's runs (Cezar Activity)
                events = []
                for r in store.list_runs(ws_id):
                    for e in store.events(r["run_id"]):
                        events.append({**e, "run_id": r["run_id"]})
                events.sort(key=lambda e: e["ts"], reverse=True)
                return self._send(200, {"activity": events[:100]})
            if path.startswith("/api/runs/") and path.endswith("/verify"):
                from moira_core.integrity import verify_chain
                run_id = path[len("/api/runs/"):-len("/verify")]
                return self._send(200, verify_chain(store.audit_records(run_id)))
            if path.startswith("/api/runs/") and path.endswith("/live"):
                # live stream of the active claude node: reasoning text, tool calls, tokens
                import time as _t
                run_id = path[len("/api/runs/"):-len("/live")]
                frm = int((parse_qs(parsed.query).get("from", ["0"])[0]) or 0)
                lp = live_path_for(run_id)
                lines = []
                if lp and os.path.exists(lp):
                    try:
                        with open(lp, "r", encoding="utf-8", errors="replace") as f:
                            lines = f.readlines()
                    except OSError:
                        lines = []
                events = []
                for ln in lines[frm:]:
                    try:
                        events.append(json.loads(ln))
                    except json.JSONDecodeError:
                        pass
                run = store.get_run(run_id)
                state = store.get_run_state(run_id) or {}
                active = next((nid for nid, s in state.items() if s == "running"), None)
                last = next((e for e in reversed(events) if e.get("tokens_in") or e.get("tokens_out")), {})
                status = run["status"] if run else "?"
                elapsed = round(_t.time() - run["created_at"]) if (run and status == "running") else 0
                return self._send(200, {"events": events, "next": len(lines),
                                        "tokens_in": last.get("tokens_in", 0), "tokens_out": last.get("tokens_out", 0),
                                        "elapsed": elapsed, "active_node": active, "status": status})
            if path.startswith("/api/runs/") and path.endswith("/report"):
                from moira_core.report import render_run_report
                run_id = path[len("/api/runs/"):-len("/report")]
                payload = run_payload(store, run_id)
                if not payload:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"markdown": render_run_report(payload)})
            if path.startswith("/api/runs/") and path.endswith("/debug"):
                # one-shot reproducibility bundle: run payload + live stream (incl. the exact
                # command/prompt when MOIRA_DEBUG=1) + the slice of the sidecar log for this run.
                import time as _t
                run_id = path[len("/api/runs/"):-len("/debug")]
                payload = run_payload(store, run_id)
                if not payload:
                    return self._send(404, {"error": "not found"})
                live = []
                lp = live_path_for(run_id)
                if lp and os.path.exists(lp):
                    try:
                        with open(lp, "r", encoding="utf-8", errors="replace") as f:
                            for ln in f:
                                try:
                                    live.append(json.loads(ln))
                                except json.JSONDecodeError:
                                    pass
                    except OSError:
                        pass
                log_tail = []
                if LOG_PATH and os.path.exists(LOG_PATH):
                    try:
                        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                            log_tail = [ln.rstrip("\n") for ln in f.readlines()[-2000:]
                                        if run_id[:10] in ln]
                    except OSError:
                        pass
                cc = ClaudeCodeBackend()
                bundle = {"generated_at": round(_t.time()), "run_id": run_id,
                          "config": {"skill_timeout": cc.skill_timeout, "claude_timeout": cc.timeout,
                                     "heavy_timeout": cc.heavy_timeout, "skill_retries": SKILL_RETRIES,
                                     "debug": os.environ.get("MOIRA_DEBUG") not in (None, "", "0", "false", "False")},
                          **payload, "live": live, "log": log_tail}
                return self._send(200, bundle)
            if path.startswith("/api/artifact/"):
                art_id = path.split("/api/artifact/", 1)[1]
                rp = ws_repo(store, ws_id)
                repo = AISdlcRepo(rp) if rp else None
                art = repo.resolve_artifact(art_id) if repo and repo.exists() else None
                return self._send(200 if art else 404, art or {"error": "not found"})
            if path == "/api/files":
                q = parse_qs(parsed.query)
                listing = list_dir(store, ws_id, q.get("path", [""])[0], q.get("root", ["code"])[0])
                return self._send(200 if listing else 404, listing or {"error": "no such dir"})
            if path == "/api/file":
                q = parse_qs(parsed.query)
                f = read_file(store, ws_id, q.get("path", [""])[0], q.get("root", ["code"])[0])
                return self._send(200 if f else 404, f or {"error": "no such file"})
            if path.startswith("/api/runs/"):
                run_id = path.split("/api/runs/", 1)[1]
                payload = run_payload(store, run_id)
                return self._send(200 if payload else 404, payload or {"error": "not found"})
            if path in ("/m", "/m/", "/mobile", "/mobile/"):
                mf = Path(__file__).parent / "mobile.html"
                if mf.exists():
                    return self._send_text(200, mf.read_text("utf-8"))
                return self._send(404, {"error": "mobile.html missing"})
            # static frontend
            return self._serve_static(path)
        finally:
            store.close()

    def _serve_static(self, path: str):
        if not STATIC:
            return self._send(404, {"error": "no static dir; run the API with --static"})
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        fp = Path(STATIC) / rel
        if not fp.exists() or not fp.is_file():
            fp = Path(STATIC) / "index.html"  # SPA fallback
        if not fp.exists():
            return self._send(404, {"error": "frontend not built"})
        ctype = {"html": "text/html", "js": "application/javascript",
                 "css": "text/css", "json": "application/json",
                 "svg": "image/svg+xml"}.get(fp.suffix.lstrip("."), "application/octet-stream")
        data = fp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- POST ------------------------------------------------------------- #
    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        store = open_store()
        ensure_default_workspace(store)
        try:
            if path == "/api/workspaces":
                import re as _re
                name = body.get("name", "Workspace")
                ws_id = body.get("id") or _re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "ws"
                store.create_workspace(ws_id, name, body.get("repo", "."), body.get("code"))
                return self._send(201, {"workspace": store.get_workspace(ws_id)})
            if path == "/api/workspaces/clone":
                import re as _re
                import subprocess as _sp
                name = body.get("name", "Workspace")
                url, dest = body.get("url", ""), body.get("dest", "")
                if not url or not dest:
                    return self._send(400, {"error": "url and dest are required"})
                try:
                    _sp.run(["git", "clone", url, dest], check=True, capture_output=True,
                            text=True, timeout=300)
                except Exception as e:  # noqa: BLE001
                    return self._send(500, {"error": f"git clone failed: {e}"})
                ws_id = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "ws"
                store.create_workspace(ws_id, name, dest, None)
                return self._send(201, {"workspace": store.get_workspace(ws_id)})
            if path == "/api/agents/import":
                from import_agents import import_dir
                ws_id = body.get("workspace_id", "default")
                src = body.get("source_dir", "")
                if not src:
                    return self._send(400, {"error": "source_dir is required"})
                ids = import_dir(ws_repo(store, ws_id), src)
                return self._send(201, {"imported": len(ids), "ids": ids})
            if path == "/api/agents":
                import re as _re
                ws_id = body.get("workspace_id", "default")
                if not body.get("id"):
                    body["id"] = _re.sub(r"[^a-z0-9]+", "-", body.get("name", "agent").lower()).strip("-") or "agent"
                saved = AISdlcRepo(ws_repo(store, ws_id)).save_agent(body)
                return self._send(201, saved)
            if path == "/api/pipelines":
                ws_id = body.get("workspace_id", "default")
                saved = AISdlcRepo(ws_repo(store, ws_id)).save_pipeline_def(body)
                return self._send(201, saved)
            if path == "/api/runs":
                func_id = body.get("func_id", "FUNC-DEMO")
                owner = body.get("owner", "tomasz.skonieczny")
                ws_id = body.get("workspace_id", "default")
                pipe = load_pipeline(store, ws_id, body, func_id)
                ctx = context_for(func_id, ws_repo(store, ws_id))
                code = ws_code_path(store, ws_id)  # real coding: agents write here (cwd)
                if code:
                    ctx["cwd"] = code
                run_id = Engine(store, registry(), owner=owner).create(pipe, ctx, workspace_id=ws_id)
                ctx["live_path"] = live_path_for(run_id)
                log.info("launch run %s func=%s pipeline=%s backend=%s owner=%s",
                         run_id, func_id, pipe.id, body.get("backend", "mock"), owner)
                background(owner, run_id, lambda e, p=pipe, c=ctx, r=run_id: e.drive_existing(r, p, c))
                return self._send(201, {"run_id": run_id, "status": "running", "waiting_node": None})
            if path.startswith("/api/runs/") and path.endswith("/report"):
                from moira_core.report import render_run_report
                from moira_core.git_sink import GitExportSink
                run_id = path[len("/api/runs/"):-len("/report")]
                payload = run_payload(store, run_id)
                if not payload:
                    return self._send(404, {"error": "not found"})
                md = render_run_report(payload)
                ws_run = (payload["run"] or {}).get("workspace_id", "default")
                repo = ws_repo(store, ws_run)
                committed, rel = False, None
                if repo:
                    try:
                        rel = GitExportSink().write_report(repo, run_id, md)
                        committed = True
                    except Exception:  # noqa: BLE001
                        committed = False
                return self._send(200, {"markdown": md, "committed": committed, "path": rel})
            if path == "/api/eval":
                # An evaluation is a tiny one-node run: an evaluator judges a TARGET
                # against CRITERIA and returns a scorecard. kind=quality judges a run's
                # outputs; kind=conformance judges code in code_path vs a FUNC spec.
                from moira_core.evals import normalize_scorecard
                ws_id = body.get("workspace_id", "default")
                owner = body.get("owner", "tomasz.skonieczny")
                kind = body.get("kind", "quality")
                model = body.get("model") or ""
                criteria = body.get("criteria") if isinstance(body.get("criteria"), list) else None
                repo_path = ws_repo(store, ws_id)
                if kind == "conformance":
                    func_id = body.get("func_id")
                    if not func_id:
                        return self._send(400, {"error": "func_id is required for conformance"})
                    repo = AISdlcRepo(repo_path)
                    spec = (repo.read_func_spec(func_id) if repo.exists() else "") or f"(no spec for {func_id})"
                    target = f"FUNC: {func_id}\n\n{spec}"
                    role, spec_ref = "spec-conformance-verifier", func_id
                    lineage = repo.trace_lineage(spec, func_id) if repo.exists() else [func_id]
                    cwd = ws_code_path(store, ws_id) or repo_path
                elif kind == "compliance":
                    refs = body.get("references") or ([body["reference"]] if body.get("reference") else [])
                    if not refs:
                        return self._send(400, {"error": "references (regulation ids) required for compliance"})
                    repo = AISdlcRepo(repo_path)
                    texts = []
                    for rid in refs:
                        t = repo.read_regulation(rid) if repo.exists() else None
                        if t:
                            texts.append(f"### {rid}\n{t}")
                    if not texts:
                        return self._send(400, {"error": f"no regulation found for {refs}"})
                    target = "\n\n".join(texts)[:24000]
                    func_id = body.get("func_id")
                    lineage = list(refs)
                    if func_id and repo.exists():
                        spec = repo.read_func_spec(func_id) or ""
                        if spec:
                            target = f"(Kontekst func-spec {func_id})\n{spec[:4000]}\n\n" + target
                        lineage = [func_id] + lineage
                    role = "compliance-verifier"
                    spec_ref = func_id or refs[0]
                    cwd = ws_code_path(store, ws_id) or repo_path
                else:
                    run_id = body.get("run_id")
                    if not run_id or not store.get_run(run_id):
                        return self._send(400, {"error": "a valid run_id is required for quality eval"})
                    target, spec_ref, lineage = eval_target_for_run(store, run_id)
                    role, cwd = "evaluator", repo_path
                node = Node(id="eval", name=f"Evaluate · {kind}", type=NodeType.PRODUCER,
                            backend="claude_code", model=model, role=role, spec_ref=spec_ref)
                pipe = Pipeline(id=f"eval-{kind}", name=f"Evaluation · {kind}", nodes=[node])
                ctx = {"eval_kind": kind, "eval_target": target, "eval_criteria": criteria,
                       "spec_text": target, "func_id": spec_ref, "lineage": lineage}
                if cwd:
                    ctx["cwd"] = cwd
                # synchronous (returns the scorecard) but split so it streams live too
                engine = Engine(store, registry(), owner=owner)
                run_id = engine.create(pipe, ctx, workspace_id=ws_id)
                ctx["live_path"] = live_path_for(run_id)
                res = engine.drive_existing(run_id, pipe, ctx)
                rec = next((a for a in store.audit_records(res.run_id) if a.get("node_id") == "eval"), None)
                scorecard = normalize_scorecard((rec or {}).get("output", {}), kind)
                return self._send(201, {"run_id": res.run_id, "status": res.status.value,
                                        "kind": kind, "scorecard": scorecard})
            if path == "/api/discovery":
                # Drive AI SDLC framework skill(s) to author/refine artifacts in the
                # AI SDLC repo (cwd=repo_path), each gated by a human review. Accepts
                # a single skill OR a chained sequence of steps (A3 discovery pipeline).
                import re as _re
                ws_id = body.get("workspace_id", "default")
                owner = body.get("owner", "tomasz.skonieczny")
                steps = body.get("steps")
                if not steps:  # single-skill form
                    if not body.get("skill"):
                        return self._send(400, {"error": "skill (or steps) is required"})
                    steps = [{"skill": body.get("skill"), "input": body.get("input", ""),
                              "elaboration": body.get("elaboration", ""),
                              "persona": body.get("persona", "ba")}]
                nodes = []
                prev_gate = None
                for i, s in enumerate(steps):
                    aid, gid = f"author{i}", f"review{i}"
                    nodes.append(Node(id=aid, name=s["skill"], type=NodeType.PRODUCER,
                                      backend="claude_code", role="ba-skill", skill=s["skill"],
                                      skill_input=s.get("input", ""), prompt_extra=s.get("elaboration", ""),
                                      spec_ref=s.get("input", ""), max_retries=SKILL_RETRIES,
                                      depends_on=[prev_gate] if prev_gate else []))
                    nodes.append(Node(id=gid, name=f"Review · {s['skill']}", type=NodeType.GATE,
                                      gate=GateConfig(mode=GateMode.HUMAN, persona=s.get("persona", "ba"),
                                                      consumes=[aid]),
                                      depends_on=[aid], on_reject_goto=aid))
                    prev_gate = gid
                name = (body.get("name") or ("Discovery · " + " → ".join(s["skill"] for s in steps)))[:80]
                pid = "discovery-" + _re.sub(r"[^a-z0-9]+", "-", "-".join(s["skill"] for s in steps).lower()).strip("-")[:60]
                pipe = Pipeline(id=pid, name=name, nodes=nodes)
                ctx = context_for(steps[0].get("input", ""), ws_repo(store, ws_id))
                ctx["cwd"] = ws_repo(store, ws_id)  # author INTO the AI SDLC repo, not code
                run_id = Engine(store, registry(), owner=owner).create(pipe, ctx, workspace_id=ws_id)
                ctx["live_path"] = live_path_for(run_id)
                log.info("launch discovery %s steps=%s owner=%s",
                         run_id, [s["skill"] for s in steps], owner)
                background(owner, run_id, lambda e, p=pipe, c=ctx, r=run_id: e.drive_existing(r, p, c))
                return self._send(201, {"run_id": run_id, "status": "running", "waiting_node": None})
            if path.endswith("/approve") or path.endswith("/reject"):
                run_id = path.split("/api/runs/", 1)[1].rsplit("/", 1)[0]
                run = store.get_run(run_id)
                if not run:
                    return self._send(404, {"error": "not found"})
                pipe = Pipeline.from_dict(json.loads(run["pipeline"]))
                if path.endswith("/approve"):
                    dec = GateDecision(decision="approve", by=body.get("by", "human"),
                                       confirmed=body.get("confirm", "approved via cockpit"))
                else:
                    dec = GateDecision(decision="reject", by=body.get("by", "human"),
                                       feedback=body.get("feedback", "rejected via cockpit"),
                                       confirmed="rejected via cockpit")
                # rebuild run context so post-gate nodes (e.g. docs) keep spec + cwd
                run_ws = run.get("workspace_id", "default")
                func_id = next((a.get("input", {}).get("spec_ref")
                                for a in store.audit_records(run_id)
                                if a.get("input", {}).get("spec_ref")), "")
                ctx = context_for(func_id, ws_repo(store, run_ws))
                code = ws_code_path(store, run_ws)
                if code:
                    ctx["cwd"] = code
                log.info("gate %s %s by %s", run_id, dec.decision, dec.by)
                background(run["owner"], run_id, lambda e, p=pipe, c=ctx, d=dec, r=run_id: e.resume(r, p, c, d))
                return self._send(200, {"run_id": run_id, "status": "running", "waiting_node": None})
            if path.endswith("/rerun"):
                old_id = path.split("/api/runs/", 1)[1].rsplit("/", 1)[0]
                run = store.get_run(old_id)
                if not run:
                    return self._send(404, {"error": "not found"})
                pipe = Pipeline.from_dict(json.loads(run["pipeline"]))
                run_ws = run.get("workspace_id", "default")
                owner = body.get("owner") or run["owner"]
                # re-launch the SAME pipeline as a fresh run, reconstructing the context
                # exactly as the original launch: discovery (skill nodes) authors into the
                # AI SDLC repo; SDLC runs write code into code_path.
                is_discovery = any(n.skill for n in pipe.nodes)
                func_id = next((a.get("input", {}).get("spec_ref")
                                for a in store.audit_records(old_id)
                                if a.get("input", {}).get("spec_ref")), "")
                ctx = context_for(func_id, ws_repo(store, run_ws))
                if is_discovery:
                    ctx["cwd"] = ws_repo(store, run_ws)
                else:
                    code = ws_code_path(store, run_ws)
                    if code:
                        ctx["cwd"] = code
                new_id = Engine(store, registry(), owner=owner).create(pipe, ctx, workspace_id=run_ws)
                ctx["live_path"] = live_path_for(new_id)
                log.info("rerun %s -> new run %s", old_id, new_id)
                background(owner, new_id, lambda e, p=pipe, c=ctx, r=new_id: e.drive_existing(r, p, c))
                return self._send(201, {"run_id": new_id, "status": "running", "waiting_node": None})
            if path == "/api/gate/simulate":
                cfg = GateConfig(mode=GateMode.HYBRID,
                                 high_cutoff=float(body.get("high_cutoff", 0.85)),
                                 low_cutoff=float(body.get("low_cutoff", 0.50)))
                confs = [float(c) for c in body.get("confidences",
                         [0.97, 0.93, 0.88, 0.82, 0.74, 0.66, 0.58, 0.49, 0.41, 0.32])]
                buckets = simulate_routing(cfg, confs)
                return self._send(200, {"buckets": buckets,
                                        "high_cutoff": cfg.high_cutoff,
                                        "low_cutoff": cfg.low_cutoff})
            return self._send(404, {"error": "unknown endpoint"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})
        finally:
            store.close()


def main(argv=None) -> int:
    global REPO, STATIC
    # When frozen by PyInstaller (the bundled desktop sidecar) there is no source
    # tree: serve the cockpit dist embedded in the bundle and don't assume a repo.
    frozen = getattr(sys, "frozen", False)
    default_static = None
    if frozen:
        cand = Path(getattr(sys, "_MEIPASS", ".")) / "cockpit_dist"
        if cand.is_dir():
            default_static = str(cand)
    default_repo = "" if frozen else str(Path(__file__).parent.parent.parent / "ai-sdlc")
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--repo", default=default_repo)
    p.add_argument("--static", default=default_static)
    args = p.parse_args(argv)
    REPO = args.repo
    STATIC = args.static
    setup_logging()
    recover_orphans()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log.info("Moira API on http://127.0.0.1:%s  repo=%s  static=%s  log=%s",
             args.port, REPO, STATIC, LOG_PATH)
    print(f"Moira API on http://127.0.0.1:{args.port}  repo={REPO}  static={STATIC}  log={LOG_PATH}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
