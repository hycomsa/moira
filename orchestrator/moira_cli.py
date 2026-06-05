"""Moira CLI — headless cockpit for the v0.1 spike.

Commands:
  run     <func-id> [--repo PATH] [--owner NAME] [--analysis-gate MODE] [--impl-gate MODE]
  inbox                         list runs waiting at a human gate
  approve <run-id> [--confirm "what you verified"]
  reject  <run-id> [--feedback "why"]
  show    <run-id>              execution plan + events (the 3-column cockpit, in text)
  audit   <run-id>             per-step audit records (the defensible core)
  runs                          list all runs

This is the headless proving ground (kill-test #3). The Tauri cockpit later
renders the same data; this CLI is the source of truth for the engine.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from moira_core import (
    AISdlcRepo, BackendRegistry, Engine, GateDecision, GateMode, MockBackend,
    Pipeline, Store, default_sdlc_pipeline,
)
from moira_core.backends import ClaudeCodeBackend, LiteLLMBackend

DB = ".moira/moira.sqlite"


def _registry() -> BackendRegistry:
    reg = BackendRegistry()
    reg.register(MockBackend())
    reg.register(ClaudeCodeBackend())
    reg.register(LiteLLMBackend())
    return reg


def _context(func_id: str, repo_path: str | None) -> dict:
    if repo_path:
        repo = AISdlcRepo(repo_path)
        if repo.exists():
            ctx = repo.context_for(func_id)
            if not ctx["spec_text"]:
                ctx["spec_text"] = f"(no spec text found for {func_id}; running with id only)"
            return ctx
    return {"func_id": func_id, "spec_text": f"Demo spec for {func_id}", "lineage": [func_id]}


def cmd_run(args) -> int:
    store = Store(DB)
    engine = Engine(store, _registry(), owner=args.owner)
    pipeline = default_sdlc_pipeline(
        func_ref=args.func_id,
        analysis_gate=GateMode(args.analysis_gate),
        impl_gate=GateMode(args.impl_gate),
    )
    ctx = _context(args.func_id, args.repo)
    result = engine.start(pipeline, ctx)
    print(f"\n  run: {result.run_id}")
    print(f"  status: {result.status.value}")
    if result.waiting_node:
        print(f"  WAITING at gate: {result.waiting_node}")
        print(f"  -> moira inbox / moira approve {result.run_id}")
    cost = store.run_cost(result.run_id)
    print(f"  cost: {cost['usd']} USD ({cost['tokens_in']}+{cost['tokens_out']} tok)")
    store.close()
    return 0


def cmd_inbox(args) -> int:
    store = Store(DB)
    waiting = [r for r in store.list_runs() if r["status"] == "waiting_gate"]
    if not waiting:
        print("  Inbox empty — no pending decisions.")
    else:
        print(f"  Inbox — {len(waiting)} pending decision(s):\n")
        for r in waiting:
            evs = store.events(r["run_id"])
            wait = next((e for e in reversed(evs) if e["kind"] in ("gate.wait", "node.escalate")), None)
            msg = wait["message"] if wait else ""
            print(f"  [{r['run_id']}] {msg}")
            print(f"      owner={r['owner']}  ->  approve / reject")
    store.close()
    return 0


def _resume(args, decision: GateDecision) -> int:
    store = Store(DB)
    run = store.get_run(args.run_id)
    if not run:
        print(f"  no such run: {args.run_id}", file=sys.stderr)
        return 1
    # reconstruct the EXACT pipeline definition that was persisted at run start
    pipeline = Pipeline.from_dict(json.loads(run["pipeline"]))
    engine = Engine(store, _registry(), owner=run["owner"])
    ctx = {"func_id": "resumed", "spec_text": "", "lineage": []}
    result = engine.resume(args.run_id, pipeline, ctx, decision)
    print(f"  run {args.run_id}: {result.status.value}")
    if result.waiting_node:
        print(f"  WAITING again at: {result.waiting_node}")
    store.close()
    return 0


def cmd_approve(args) -> int:
    return _resume(args, GateDecision(decision="approve", by=args.by or "human",
                                      confirmed=args.confirm or "approved via CLI"))


def cmd_reject(args) -> int:
    return _resume(args, GateDecision(decision="reject", by=args.by or "human",
                                      feedback=args.feedback or "rejected via CLI",
                                      confirmed="rejected via CLI"))


def cmd_show(args) -> int:
    store = Store(DB)
    run = store.get_run(args.run_id)
    if not run:
        print(f"  no such run: {args.run_id}", file=sys.stderr)
        return 1
    print(f"\n  RUN {run['run_id']}  [{run['status']}]  owner={run['owner']}")
    print(f"  pipeline: {json.loads(run['pipeline'])['name']}")
    print("\n  ── Activity log ──")
    for ev in store.events(args.run_id):
        node = f" ({ev['node_id']})" if ev["node_id"] else ""
        print(f"   {ev['seq']:>3}  {ev['kind']:<14}{node:<22} {ev['message']}")
    cost = store.run_cost(args.run_id)
    print(f"\n  cost: {cost['usd']} USD  ({cost['tokens_in']} in + {cost['tokens_out']} out tokens)")
    store.close()
    return 0


def cmd_audit(args) -> int:
    store = Store(DB)
    recs = store.audit_records(args.run_id)
    if not recs:
        print(f"  no audit records for {args.run_id}", file=sys.stderr)
        return 1
    print(f"\n  AUDIT RECORDS — run {args.run_id}  ({len(recs)} steps)\n")
    for r in recs:
        print(f"  ● {r['node_name']}  [{r['status']}]  owner={r['owner']}")
        if r.get("decisions"):
            for d in r["decisions"]:
                print(f"      decision: {d}")
        if r.get("tools"):
            print(f"      tools: {', '.join(r['tools'])}")
        if r.get("approvals"):
            for a in r["approvals"]:
                print(f"      approval: {a['decision']} by {a['by']} — {a.get('confirmed','')}")
        if r.get("lineage"):
            print(f"      lineage: {' -> '.join(r['lineage'])}")
        c = r.get("cost") or {}
        if c:
            print(f"      cost: {c.get('usd',0)} USD   time: {r.get('duration',0):.2f}s")
        print()
    store.close()
    return 0


def cmd_runs(args) -> int:
    store = Store(DB)
    rows = store.list_runs()
    if not rows:
        print("  no runs yet.")
    for r in rows:
        print(f"  {r['run_id']}  {r['status']:<13} {r['pipeline_id']}  owner={r['owner']}")
    store.close()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="moira", description="Moira orchestration CLI (v0.1 spike)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run a pipeline on a func-spec")
    pr.add_argument("func_id")
    pr.add_argument("--repo", default=None, help="path to AI SDLC repo")
    pr.add_argument("--owner", default="tomasz.skonieczny")
    pr.add_argument("--analysis-gate", default="auto", choices=[m.value for m in GateMode])
    pr.add_argument("--impl-gate", default="hybrid", choices=[m.value for m in GateMode])
    pr.set_defaults(func=cmd_run)

    pi = sub.add_parser("inbox", help="pending human-gate decisions")
    pi.set_defaults(func=cmd_inbox)

    pa = sub.add_parser("approve", help="approve a waiting gate")
    pa.add_argument("run_id")
    pa.add_argument("--confirm", default="", help="what you verified (real audit, not a stamp)")
    pa.add_argument("--by", default="")
    pa.set_defaults(func=cmd_approve)

    prj = sub.add_parser("reject", help="reject a waiting gate (sends feedback to producer)")
    prj.add_argument("run_id")
    prj.add_argument("--feedback", default="")
    prj.add_argument("--by", default="")
    prj.set_defaults(func=cmd_reject)

    ps = sub.add_parser("show", help="execution plan + activity log")
    ps.add_argument("run_id")
    ps.set_defaults(func=cmd_show)

    pau = sub.add_parser("audit", help="per-step audit records")
    pau.add_argument("run_id")
    pau.set_defaults(func=cmd_audit)

    prn = sub.add_parser("runs", help="list all runs")
    prn.set_defaults(func=cmd_runs)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
