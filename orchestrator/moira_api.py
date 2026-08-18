"""Moira HTTP API — the sidecar the cockpit (React/Tauri) talks to.

Zero-dependency (stdlib http.server) on purpose, consistent with the core.
Exposes the same operations as the CLI so the cockpit renders real run data:

  GET  /api/ready                -> public readiness probe (no auth, no secrets)
  GET  /api/health               -> backend/runner status (sensitive)
  GET  /api/runs | /api/runs/{id}-> run list / {run, events, audit, cost}
  GET  /api/inbox                -> gates awaiting a human
  GET  /api/runs/{id}/verify     -> audit hash-chain verification
  GET  /api/runner               -> durable runner: workers + recent jobs
  GET  /api/governance/packs[/{id}] -> governance packs (repo-owned)
  POST /api/runs                 {func_id, owner?, governance_packs?, ...}
  POST /api/runs/{id}/approve    {confirm?}   (approver = authenticated principal)
  POST /api/runs/{id}/reject     {feedback?}
  POST /api/runs/{id}/retry      {feedback?}  (failed node only — ADR-013)
  POST /api/runs/{id}/cancel     -> request mid-drive cancellation
  POST /api/eval                 -> quality/conformance/compliance scorecard
  GET  /                         -> serves the cockpit frontend (static dir)

Auth (ADR-008): when MOIRA_AUTH_MODE != off, all /api/* except /api/ready require a
Bearer JWT; default-deny RBAC (5 roles) authorizes each route + gate persona.

Run:  python3 moira_api.py [--port 8765] [--repo ../../ai-sdlc] [--static ../cockpit/dist]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from moira_core import (  # noqa: E402
    AISdlcRepo, BackendRegistry, Engine, GateConfig, GateDecision, GateMode,
    MockBackend, Node, NodeType, Pipeline, Store, available_pipelines, client_gated_pipeline,
    default_sdlc_pipeline, make_run_store, DurableRunner, Event, new_id,
    validate_pipeline, AuditRecord, validate_pack, attach_pack, applied_marker,
)
from moira_core.gates import simulate_routing  # noqa: E402
from moira_core.backends import ClaudeCodeBackend, LiteLLMBackend, probes  # noqa: E402
from moira_core import tasks as task_model  # noqa: E402
from moira_core import authn, authz  # noqa: E402

DB = os.environ.get("MOIRA_DB", ".moira/moira.sqlite")
REPO = None
STATIC = None
LOG_PATH = None
log = logging.getLogger("moira")
RUNNER_THREAD = None
RUNNER = None
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


def ensure_embedded_runner() -> None:
    """Start the local durable runner once.

    This replaces product use of request-scoped daemon execution: the thread is
    now only a host for ADR-006 jobs/leases, not the source of truth.
    """
    global RUNNER_THREAD, RUNNER
    mode = os.environ.get("MOIRA_RUNNER_MODE", "embedded").lower()
    if mode in ("off", "external"):
        return
    if RUNNER_THREAD and RUNNER_THREAD.is_alive():
        return
    lease = int(os.environ.get("MOIRA_RUNNER_LEASE_SECONDS", "300"))
    RUNNER = DurableRunner(open_store, registry, mode="embedded", lease_seconds=lease)
    RUNNER_THREAD = threading.Thread(target=RUNNER.run_forever, daemon=True, name="moira-runner")
    RUNNER_THREAD.start()
    log.info("embedded durable runner started: %s", RUNNER.worker_id)


def recover_orphans() -> None:
    """Recover durable runner leases from a previous process.

    ADR-006 replaces the old "mark every running run failed" behavior: the job
    table is now the execution source of truth, so startup only releases expired
    leases and lets queued work be claimed again.
    """
    store = open_store()
    try:
        n = store.release_expired_leases()
        if n:
            log.info("released %d expired runner lease(s)", n)
    finally:
        store.close()


def enqueue_run_job(store, run_id: str, ws_id: str, kind: str, payload: dict) -> dict:
    job = store.enqueue_job({
        "job_id": new_id("job-"),
        "run_id": run_id,
        "workspace_id": ws_id,
        "kind": kind,
        "status": "queued",
        "payload": payload,
        "max_attempts": int(os.environ.get("MOIRA_JOB_MAX_ATTEMPTS", "3")),
    })
    store.append_event(Event(run_id=run_id, kind="job.queued",
                             message=f"Queued durable job {job['job_id']} ({kind})"))
    ensure_embedded_runner()
    return job


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
    effort = body.get("effort", "")        # run-level reasoning-effort override ("" = leave per-node)
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
                    if effort:
                        n.effort = effort
                    if not n.spec_ref:
                        n.spec_ref = func_id
            return pipe
    if body.get("pipeline") == "client-gated":
        pipe = client_gated_pipeline(func_ref=func_id, backend=backend)
    else:
        pipe = default_sdlc_pipeline(
            func_ref=func_id,
            analysis_gate=GateMode(body.get("analysis_gate", "auto")),
            impl_gate=GateMode(body.get("impl_gate", "hybrid")),
            backend=backend,
        )
    if effort:
        for n in pipe.nodes:
            if n.type.value != "gate":
                n.effort = effort
    return pipe


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
    func = ""
    labels: collections.Counter = collections.Counter()
    for r in store.audit_records(run_id):
        c = r.get("cost") or {}
        ti += c.get("tokens_in", 0) or 0
        to += c.get("tokens_out", 0) or 0
        usd += c.get("usd", 0) or 0
        dur += r.get("duration", 0) or 0
        inp = r.get("input") or {}
        if not func and inp.get("spec_ref"):  # the run's human identity (UX)
            func = inp["spec_ref"]
        m, be = inp.get("model"), inp.get("backend")
        # Only execution steps carry a backend (gates don't) — skip the rest so a human
        # gate can't dilute the leading model. A "model" that is really a backend name or a
        # placeholder ("mock"/"claude_code"/"(default)") is not a model — fall back to the
        # backend, so a claude_code skill node isn't mislabelled "mock".
        if not be:
            continue
        label = m if (m and m not in ("(default)", "mock", "claude_code", "litellm")) else be
        if label:
            labels[label] += 1
    return {"usd": round(usd, 4), "tokens": ti + to, "duration": round(dur, 1),
            "func": func,
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
    # server-enforced budgets (ST4/ADR-017) — None when unset
    budget = {}
    for key, name in (("budget_month_usd", "month_usd"), ("budget_run_usd", "run_usd")):
        v = store.get_setting(f"workspace:{ws_id}", key)
        budget[name] = float(v) if v else None  # "" = cleared, absent = never set
    return {
        "total_usd": round(total, 4), "runs": runs_n, "month": month,
        "month_usd": round(month_total, 4), "budget": budget,
        "by_model": [{"label": k, "usd": round(v, 4)} for k, v in by_model.most_common()],
        "by_owner": [{"label": k, "usd": round(v, 4)} for k, v in by_owner.most_common()],
    }


def _summarize_check(rec: dict) -> str:
    o = rec.get("output") or {}
    if isinstance(o.get("summary"), str) and o["summary"]:   # test_exec / ac_coverage carry a summary
        return o["summary"]
    if isinstance(o.get("passed"), bool):
        return "check passed" if o["passed"] else "check FAILED"
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
            w = next((e for e in reversed(evs)
                      if e["kind"] in ("gate.wait", "node.escalate", "budget.wait")), None)
            out.append({"run_id": r["run_id"], "workspace": ws["name"], "pipeline": r["pipeline_id"],
                        "persona": g_in.get("persona", ""), "message": w["message"] if w else "",
                        "kind": ("failed_node" if w and w["kind"] == "node.escalate"
                                 else "budget" if w and w["kind"] == "budget.wait"
                                 else "gate"),
                        "checks": checks, "changed_files": files,
                        "gate_review": gate_review_for(store, r["run_id"], ws["id"]),
                        **run_metrics(store, r["run_id"])})
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
        conf = last_conformance(store, ws_id, fid)
        out.append({"id": fid, "title": title, "lineage": repo.trace_lineage(text, fid),
                    "runs": runs_by_func.get(fid, []),
                    "completeness": task_model.completeness(repo, fid),
                    "conformance": conf and {"overall": conf["overall"]}})
    return out


def last_conformance(store: Store, ws_id: str, func_id: str | None) -> dict | None:
    """Most recent conformance scorecard (LLM judge) for a FUNC, if any was ever run."""
    if not func_id:
        return None
    from moira_core.evals import normalize_scorecard
    for r in store.list_runs(ws_id):  # DESC → first match is newest
        if r.get("pipeline_id") != "eval-conformance":
            continue
        rec = next((a for a in store.audit_records(r["run_id"]) if a.get("node_id") == "eval"), None)
        if not rec or rec.get("input", {}).get("spec_ref") != func_id:
            continue
        sc = normalize_scorecard(rec.get("output", {}), "conformance")
        return {"run_id": r["run_id"], "overall": sc.get("overall"), "summary": sc.get("summary"),
                "criteria": sc.get("criteria", []), "missing": sc.get("missing", []),
                "parsed": sc.get("parsed")}
    return None


def gate_review_for(store: Store, run_id: str, fallback_ws: str) -> dict | None:
    """Decision-ready summary for a pending gate: AC coverage/completeness + last LLM conformance."""
    fid = func_id_for_run(store, run_id)
    if not fid:
        return None
    ws = (store.get_run(run_id) or {}).get("workspace_id") or fallback_ws
    rp = ws_repo(store, ws)
    repo = AISdlcRepo(rp) if rp else None
    cov = task_model.completeness(repo, fid) if (repo and repo.exists()) else None
    conf = last_conformance(store, ws, fid)
    if not cov and not conf:
        return None
    return {"func_id": fid,
            "coverage": cov and {"level": cov["level"], "ac": cov["ac"], "tasks": cov["tasks"]},
            "conformance": conf and {"overall": conf["overall"]}}


def func_id_for_run(store: Store, run_id: str) -> str | None:
    """The FUNC a run targeted: its audit lineage's first FUNC, else a spec_ref/authored FUNC."""
    recs = store.audit_records(run_id)
    for a in recs:
        for x in (a.get("lineage") or []):
            if x.startswith("FUNC"):
                return x
    for a in recs:
        sr = a.get("input", {}).get("spec_ref")
        if sr and sr.startswith("FUNC"):
            return sr
        art = (a.get("output") or {}).get("artifact")
        if isinstance(art, str) and art.startswith("FUNC"):
            return art
    return None


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

    # ---- auth (must-fix #2) ----------------------------------------------- #
    def _principal(self):
        auth = self.headers.get("Authorization", "") or ""
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else None
        try:
            return authn.principal_from_token(token)
        except Exception:  # noqa: BLE001 — misconfig/verification error => unauthenticated
            return None

    def _enforce(self, method: str, path: str) -> bool:
        """Set self.principal and authorize the request. Returns True to proceed.

        No-op (full local-admin access) when MOIRA_AUTH_MODE=off, so the app keeps
        working until auth is turned on. Otherwise default-deny: 401/403 via authz.
        """
        self.principal = self._principal()
        if authn.auth_mode() == "off":
            return True
        verdict = authz.authorize_request(method, path, self.principal)
        if verdict is not None:
            self._send(verdict[0], verdict[1])
            return False
        return True

    def do_OPTIONS(self):
        self._send(204)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._enforce("DELETE", path):
            return
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
        if path == "/api/ready":
            # public readiness — no paths/secrets (auth-independent liveness probe)
            return self._send(200, {"ready": True, "auth_mode": authn.auth_mode()})
        if not self._enforce("GET", path):
            return
        ws_id = parse_qs(parsed.query).get("ws", ["default"])[0]
        store = open_store()
        ensure_default_workspace(store)
        try:
            if path == "/api/health":
                primary = os.environ.get("MOIRA_PRIMARY", "sqlite")
                git_on = os.environ.get("MOIRA_GIT_EXPORT", "0") not in ("", "0", "false", "False")
                persistence = primary + (" + git" if git_on else "")
                cc = ClaudeCodeBackend()
                workers = store.workers()
                jobs = store.jobs()
                by_job_status = {}
                for j in jobs:
                    by_job_status[j["status"]] = by_job_status.get(j["status"], 0) + 1
                return self._send(200, {"ok": True, "backends": registry().available(),
                                        "repo": REPO, "persistence": persistence, "log": LOG_PATH,
                                        "claude": cc.available(), "version": "0.1",
                                        # install/login probes per backend (QW4/ADR-012;
                                        # cached, asymmetric TTL — cheap to poll)
                                        "probes": probes.all_probes(("claude_code", "litellm", "mock")),
                                        "runner": {"mode": os.environ.get("MOIRA_RUNNER_MODE", "embedded"),
                                                   "embedded_alive": bool(RUNNER_THREAD and RUNNER_THREAD.is_alive()),
                                                   "workers": workers, "jobs": by_job_status},
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
            if path == "/api/browse":
                # localhost-only directory listing for the in-app folder picker (web mode +
                # desktop fallback). Lists subdirectories only; no file contents.
                q = parse_qs(parsed.query).get("path", [""])[0]
                base = os.path.abspath(os.path.expanduser(q)) if q else os.path.expanduser("~")
                if not os.path.isdir(base):
                    base = os.path.expanduser("~")
                dirs = []
                try:
                    for e in os.scandir(base):
                        if e.name.startswith(".") or not e.is_dir(follow_symlinks=False):
                            continue
                        dirs.append({"name": e.name, "path": os.path.join(base, e.name)})
                except OSError:
                    pass
                dirs.sort(key=lambda d: d["name"].lower())
                parent = os.path.dirname(base.rstrip(os.sep)) or base
                # mark folders that look like an AI SDLC repo (have .ai/context) to guide the pick
                for d in dirs:
                    d["is_repo"] = os.path.isdir(os.path.join(d["path"], ".ai", "context"))
                return self._send(200, {"path": base, "parent": parent if parent != base else None,
                                        "dirs": dirs,
                                        "is_repo": os.path.isdir(os.path.join(base, ".ai", "context"))})
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
                              if e["kind"] in ("gate.wait", "node.escalate", "budget.wait")), None)
                    # surface the artifact the persona must review (client gate wedge)
                    gate_rec = next((a for a in reversed(store.audit_records(r["run_id"]))
                                     if a.get("status") == "waiting_gate"), None)
                    g_in = (gate_rec or {}).get("input", {})
                    items.append({"run_id": r["run_id"], "owner": r["owner"],
                                  "message": w["message"] if w else "",
                                  "node_id": w["node_id"] if w else "",
                                  "since": w["ts"] if w else r.get("updated_at"),
                                  # gate = re-evaluate (approve/reject); failed_node and budget
                                  # also offer retry (ADR-013; budget after raising it — ADR-017)
                                  "kind": ("failed_node" if w and w["kind"] == "node.escalate"
                                           else "budget" if w and w["kind"] == "budget.wait"
                                           else "gate"),
                                  "persona": g_in.get("persona", ""),
                                  "audience": g_in.get("audience", "technical"),
                                  "consumes": g_in.get("consumes", []),
                                  "review": g_in.get("review", {}),
                                  "gate_review": gate_review_for(store, r["run_id"], ws_id)})
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
            if path == "/api/governance/packs":
                rp = ws_repo(store, ws_id)
                repo = AISdlcRepo(rp) if rp else None
                packs = repo.list_packs() if repo and repo.exists() else []
                return self._send(200, {"packs": packs})
            if path.startswith("/api/governance/packs/"):
                rp = ws_repo(store, ws_id)
                repo = AISdlcRepo(rp) if rp else None
                pack = repo.get_pack(path.split("/api/governance/packs/", 1)[1]) if repo else None
                if not pack:
                    return self._send(404, {"error": "pack not found"})
                return self._send(200, {"pack": pack, "errors": validate_pack(pack)})
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
            if path == "/api/runner":
                jobs = store.jobs()
                by = {}
                for j in jobs:
                    by[j["status"]] = by.get(j["status"], 0) + 1
                return self._send(200, {"workers": store.workers(), "jobs": jobs[-100:],
                                        "job_counts": by})
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
                # parse ALL lines once: the incremental slice feeds the event log, but the
                # token counters must come from the full buffer — otherwise a poll window with
                # no usage record would make the live token count flicker back to 0.
                all_events = []
                for ln in lines:
                    try:
                        all_events.append(json.loads(ln))
                    except json.JSONDecodeError:
                        pass
                events = all_events[frm:]
                run = store.get_run(run_id)
                state = store.get_run_state(run_id) or {}
                active = next((nid for nid, s in state.items() if s == "running"), None)
                last = next((e for e in reversed(all_events) if e.get("tokens_in") or e.get("tokens_out")), {})
                status = run["status"] if run else "?"
                elapsed = round(_t.time() - run["created_at"]) if (run and status == "running") else 0
                return self._send(200, {"events": events, "next": len(all_events),
                                        "tokens_in": last.get("tokens_in", 0), "tokens_out": last.get("tokens_out", 0),
                                        "elapsed": elapsed, "active_node": active, "status": status})
            if path.startswith("/api/runs/") and path.endswith("/report"):
                from moira_core.report import render_run_report
                run_id = path[len("/api/runs/"):-len("/report")]
                payload = run_payload(store, run_id)
                if not payload:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"markdown": render_run_report(payload)})
            if path.startswith("/api/runs/") and path.endswith("/traceability"):
                # deterministic Spec ↔ Tests ↔ Tasks ↔ Lineage trace for the FUNC this run targeted
                run_id = path[len("/api/runs/"):-len("/traceability")]
                run = store.get_run(run_id)
                if not run:
                    return self._send(404, {"error": "not found"})
                # resolve the repo from the RUN's workspace, not the query param
                rp = ws_repo(store, run.get("workspace_id") or ws_id)
                repo = AISdlcRepo(rp) if rp else None
                if not (repo and repo.exists()):
                    return self._send(200, {"func_id": None, "available": False})
                fid = func_id_for_run(store, run_id)
                lineage = next((a.get("lineage") for a in store.audit_records(run_id)
                                if a.get("lineage")), [])
                res = task_model.traceability(repo, fid, lineage)
                res["conformance"] = last_conformance(store, run.get("workspace_id") or ws_id, fid)
                return self._send(200, {"available": True, **res})
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
        # When auth is on, the server owns the session token: mint a short-lived local
        # token for the local user and inject it + a fetch() wrapper that attaches it as
        # a bearer header. The web cockpit then authenticates without any IdP or rebuild.
        if ctype == "text/html" and authn.auth_mode() == "local":
            token = authn.mint_local_token("local", ["admin"], ttl_seconds=43200)
            inject = (
                "<script>window.__MOIRA_TOKEN__=" + json.dumps(token) + ";"
                "(function(){var t=window.__MOIRA_TOKEN__;if(!t)return;"
                "var f=window.fetch.bind(window);window.fetch=function(i,o){o=o||{};"
                "var h=new Headers((o&&o.headers)||(typeof i!=='string'&&i&&i.headers)||{});"
                "if(!h.has('Authorization'))h.set('Authorization','Bearer '+t);"
                "var n={};for(var k in o)n[k]=o[k];n.headers=h;return f(i,n);};})();</script>"
            ).encode("utf-8")
            lower = data.lower()
            idx = lower.find(b"</head>")
            data = (data[:idx] + inject + data[idx:]) if idx != -1 else inject + data
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- POST ------------------------------------------------------------- #
    def do_POST(self):
        path = urlparse(self.path).path
        if not self._enforce("POST", path):
            return
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
            if path.startswith("/api/workspaces/") and path.endswith("/budget"):
                # cost budgets (ST4/ADR-017): governed configuration (RBAC: configure).
                # month_usd caps the workspace's calendar-month spend; run_usd is the
                # default per-run cap applied at launch. null/absent value = clear.
                ws = path[len("/api/workspaces/"):-len("/budget")]
                if not store.get_workspace(ws):
                    return self._send(404, {"error": "unknown workspace", "workspace": ws})
                out = {}
                for body_key, setting in (("month_usd", "budget_month_usd"),
                                          ("run_usd", "budget_run_usd")):
                    if body_key in body:
                        v = body[body_key]
                        if v is None or v == "":
                            store.set_setting(f"workspace:{ws}", setting, "")
                            out[body_key] = None
                        else:
                            try:
                                store.set_setting(f"workspace:{ws}", setting, str(float(v)))
                                out[body_key] = float(v)
                            except (TypeError, ValueError):
                                return self._send(400, {"error": f"{body_key} must be a number"})
                log.info("budget set ws=%s %s by %s", ws, out,
                         getattr(getattr(self, "principal", None), "subject", "local"))
                return self._send(200, {"workspace": ws, "budget": out})
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
                repo = AISdlcRepo(ws_repo(store, ws_id))
                # schema-validate before persisting: build the def (resolves agent refs)
                # then check structure, so a malformed pipeline never reaches a run.
                try:
                    built = repo.build_pipeline(body)
                except Exception as e:  # noqa: BLE001 — build failure == invalid def
                    return self._send(400, {"error": "invalid pipeline", "detail": str(e)})
                errs = validate_pipeline(built)
                if errs:
                    return self._send(400, {"error": "invalid pipeline", "errors": errs})
                saved = repo.save_pipeline_def(body)
                return self._send(201, saved)
            if path == "/api/runs":
                func_id = body.get("func_id", "FUNC-DEMO")
                owner = body.get("owner", "tomasz.skonieczny")
                ws_id = body.get("workspace_id", "default")
                pipe = load_pipeline(store, ws_id, body, func_id)
                rp = ws_repo(store, ws_id)
                # governance packs: resolve from the repo, validate, attach (runs AFTER
                # the work). Deterministic checks block the gate; LLM checks are advisory.
                applied_packs = []
                gov_packs = body.get("governance_packs") or []
                if gov_packs:
                    grepo = AISdlcRepo(rp) if rp else None
                    for pid in gov_packs:
                        pack = grepo.get_pack(pid) if grepo else None
                        if not pack:
                            return self._send(400, {"error": "unknown governance pack", "pack": pid})
                        perrs = validate_pack(pack)
                        if perrs:
                            return self._send(400, {"error": "invalid governance pack",
                                                    "pack": pid, "errors": perrs})
                        attach_pack(pipe, pack, model=body.get("model", ""),
                                    effort=body.get("effort", ""))
                        applied_packs.append(pack)
                errs = validate_pipeline(pipe)
                if errs:
                    return self._send(400, {"error": "invalid pipeline", "errors": errs})
                # launch gate (QW4/ADR-012): fail fast on a backend that definitely
                # cannot run (not installed / logged out) — with a copy-paste fix —
                # instead of surfacing it minutes later as a failed node after retries.
                exec_backends = {n.backend for n in pipe.nodes
                                 if n.type not in (NodeType.GATE, NodeType.AUTO_CHECK)}
                blockers = probes.launch_blockers(exec_backends)
                if blockers:
                    return self._send(503, {"error": "backend not ready", "blockers": blockers})
                ctx = context_for(func_id, rp)
                code = ws_code_path(store, ws_id)  # real coding: agents write here (cwd)
                authoring = any(getattr(n, "skill", "") for n in pipe.nodes)
                if authoring and rp:
                    ctx["cwd"] = rp        # authoring pipeline: write specs INTO the AI SDLC repo
                    first = next((n for n in pipe.nodes if getattr(n, "skill", "")), None)
                    if first and not first.skill_input:
                        first.skill_input = func_id   # seed the chain with the run's topic
                elif code:
                    ctx["cwd"] = code
                if rp:  # where the git-native task backlog lives (may differ from the code cwd)
                    ctx["backlog_dir"] = os.path.join(rp, task_model.project_config(Path(rp))["tickets_root"])
                run_id = Engine(store, registry(), owner=owner).create(pipe, ctx, workspace_id=ws_id)
                # per-run cost budget (ST4/ADR-017): explicit on the request, else the
                # workspace default; persisted as a setting so it survives resume
                run_budget = body.get("budget_usd") or store.get_setting(
                    f"workspace:{ws_id}", "budget_run_usd")
                if run_budget:
                    try:
                        store.set_setting(f"run:{run_id}", "budget_usd", str(float(run_budget)))
                    except (TypeError, ValueError):
                        pass  # a junk value must not block the launch — no budget then
                ctx["live_path"] = live_path_for(run_id)
                # stamp which governance pack(s) applied into the sealed audit (id+version+hash)
                for pack in applied_packs:
                    store.save_audit(AuditRecord(
                        step_id=new_id("step-"), run_id=run_id, node_id=f"gov-{pack['id']}",
                        node_name=f"Governance · {pack['id']}", owner=owner,
                        output=applied_marker(pack), status="succeeded"))
                log.info("launch run %s func=%s pipeline=%s backend=%s owner=%s",
                         run_id, func_id, pipe.id, body.get("backend", "mock"), owner)
                job = enqueue_run_job(store, run_id, ws_id, "drive_run", {"context": ctx})
                return self._send(201, {"run_id": run_id, "status": "queued",
                                        "job_id": job["job_id"], "waiting_node": None})
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
                blockers = probes.launch_blockers({"claude_code"})  # evals run on claude_code
                if blockers:
                    return self._send(503, {"error": "backend not ready", "blockers": blockers})
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
            if path.startswith("/api/runs/") and path.endswith("/cancel"):
                run_id = path.split("/api/runs/", 1)[1].rsplit("/", 1)[0]
                run = store.get_run(run_id)
                if not run:
                    return self._send(404, {"error": "not found"})
                by = body.get("by", "human")
                reason = body.get("reason", "cancelled via cockpit")
                store.request_cancellation(run_id, by, reason)
                store.append_event(Event(run_id=run_id, kind="run.cancel.requested",
                                         message=f"Cancellation requested by {by}: {reason}"))
                if run.get("status") in ("waiting_gate", "pending"):
                    # a PARKED run has no active job, so nothing would ever honor the
                    # request — cancel it terminally right here (audited via events);
                    # this is how stale gate decisions finally leave the Inbox
                    store.update_run_status(run_id, "cancelled")
                    store.honor_cancellation(run_id)
                    store.append_event(Event(run_id=run_id, kind="run.cancel",
                                             message=f"Run cancelled while parked ({run.get('status')}) by {by}"))
                    return self._send(200, {"run_id": run_id, "status": "cancelled",
                                            "cancellation_requested": True})
                ensure_embedded_runner()
                return self._send(200, {"run_id": run_id, "status": run.get("status"),
                                        "cancellation_requested": True})
            if path == "/api/discovery":
                # Drive AI SDLC framework skill(s) to author/refine artifacts in the
                # AI SDLC repo (cwd=repo_path), each gated by a human review. Accepts
                # a single skill OR a chained sequence of steps (A3 discovery pipeline).
                import re as _re
                blockers = probes.launch_blockers({"claude_code"})  # skills run on claude_code
                if blockers:
                    return self._send(503, {"error": "backend not ready", "blockers": blockers})
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
                    persona = s.get("persona", "ba")
                    nodes.append(Node(id=aid, name=s["skill"], type=NodeType.PRODUCER,
                                      backend="claude_code", role="ba-skill", skill=s["skill"],
                                      skill_input=s.get("input", ""), prompt_extra=s.get("elaboration", ""),
                                      spec_ref=s.get("input", ""), max_retries=SKILL_RETRIES,
                                      depends_on=[prev_gate] if prev_gate else []))
                    review_dep = aid
                    if s["skill"] == "pm@decompose-func":
                        # deterministic AC-coverage check → AUTO gate: 100% auto-approves and flows to the
                        # human quality gate; < 100% escalates to a human with the failing finding.
                        cid, covg = f"check{i}", f"cov{i}"
                        nodes.append(Node(id=cid, name=f"AC coverage · {s.get('input','')}",
                                          type=NodeType.AUTO_CHECK, check_kind="ac_coverage",
                                          spec_ref=s.get("input", ""), depends_on=[aid]))
                        nodes.append(Node(id=covg, name="AC coverage gate", type=NodeType.GATE,
                                          gate=GateConfig(mode=GateMode.AUTO, persona=persona, consumes=[cid]),
                                          depends_on=[cid], on_reject_goto=aid))
                        review_dep = covg
                    nodes.append(Node(id=gid, name=f"Review · {s['skill']}", type=NodeType.GATE,
                                      gate=GateConfig(mode=GateMode.HUMAN, persona=persona, consumes=[aid]),
                                      depends_on=[review_dep], on_reject_goto=aid))
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
                job = enqueue_run_job(store, run_id, ws_id, "drive_run", {"context": ctx})
                return self._send(201, {"run_id": run_id, "status": "queued",
                                        "job_id": job["job_id"], "waiting_node": None})
            if path.endswith("/approve") or path.endswith("/reject") or path.endswith("/retry"):
                run_id = path.split("/api/runs/", 1)[1].rsplit("/", 1)[0]
                run = store.get_run(run_id)
                if not run:
                    return self._send(404, {"error": "not found"})
                if run.get("status") != "waiting_gate":
                    # Idempotent against double-clicks / duplicate requests: only a run actually
                    # parked at a gate can be decided. A late duplicate must NOT spawn a second
                    # resume thread — that crashed and failed an already-running run.
                    return self._send(409, {"error": "run is not waiting at a gate",
                                            "run_id": run_id, "status": run.get("status")})
                pipe = Pipeline.from_dict(json.loads(run["pipeline"]))
                # the persona this gate requires — enforce the authenticated principal may act for it
                state = store.get_run_state(run_id) or {}
                waiting_nid = next((nid for nid, s in state.items() if s == "waiting_gate"), None)
                gate_persona = ""
                if waiting_nid:
                    try:
                        gnode = pipe.by_id(waiting_nid)
                        gate_persona = (gnode.gate.persona if gnode.gate else "") or ""
                    except KeyError:
                        gate_persona = ""
                principal = getattr(self, "principal", None)
                if (authn.auth_mode() != "off" and gate_persona
                        and not authz.can_approve(principal, gate_persona)):
                    return self._send(403, {"error": "not authorized to decide this gate persona",
                                            "persona": gate_persona,
                                            "subject": getattr(principal, "subject", None)})
                if path.endswith("/retry"):
                    # retry re-EXECUTES work — valid only on an escalated failed node;
                    # a run parked at a real gate re-evaluates via approve/reject (ADR-013)
                    wnode = None
                    if waiting_nid:
                        try:
                            wnode = pipe.by_id(waiting_nid)
                        except KeyError:
                            wnode = None
                        if wnode is not None and wnode.type == NodeType.GATE:
                            return self._send(409, {"error": "retry applies to a failed step, "
                                                             "not a gate — use approve/reject",
                                                    "run_id": run_id, "node_id": waiting_nid})
                # approver identity comes from the authenticated principal, NOT the request body
                approver = getattr(principal, "subject", None) or body.get("by", "human")
                src = getattr(principal, "auth_source", "request")
                if path.endswith("/approve"):
                    dec = GateDecision(decision="approve", by=approver,
                                       confirmed=f"{body.get('confirm', 'approved')} (via {src})")
                elif path.endswith("/retry"):
                    dec = GateDecision(decision="retry", by=approver,
                                       feedback=body.get("feedback", ""),
                                       confirmed=f"human retry (via {src})")
                else:
                    dec = GateDecision(decision="reject", by=approver,
                                       feedback=body.get("feedback", "rejected"),
                                       confirmed=f"rejected (via {src})")
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
                store.update_run_status(run_id, "running")
                job = enqueue_run_job(store, run_id, run_ws, "resume_run",
                                      {"context": ctx, "decision": dec.to_dict()})
                return self._send(200, {"run_id": run_id, "status": "queued",
                                        "job_id": job["job_id"], "waiting_node": None})
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
                job = enqueue_run_job(store, new_id, run_ws, "drive_run", {"context": ctx})
                return self._send(201, {"run_id": new_id, "status": "queued",
                                        "job_id": job["job_id"], "waiting_node": None})
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
    ensure_embedded_runner()
    probes.warm(("claude_code", "litellm"))  # QW4: pre-fill the probe cache off-thread
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
