"""Verify the REAL ClaudeCodeBackend through the actual engine (ADR-004).

Drives a 2-node pipeline (analyze via claude CLI -> auto gate) on a real
func-spec. Proves: engine -> backend -> `claude` CLI -> parse -> audit record,
with REAL model output and REAL cost captured. Run with an authenticated
`claude` CLI on PATH.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from moira_core import (
    AISdlcRepo, BackendRegistry, Engine, GateConfig, GateMode, Node, NodeType,
    Pipeline, Store,
)
from moira_core.backends import ClaudeCodeBackend


def main() -> int:
    repo_path = sys.argv[1] if len(sys.argv) > 1 else "../../ai-sdlc"
    func_id = sys.argv[2] if len(sys.argv) > 2 else "FUNC-MOIRA-audit-record"

    backend = ClaudeCodeBackend()
    if not backend.available():
        print("claude CLI not on PATH — cannot run real-backend verification")
        return 2

    reg = BackendRegistry()
    reg.register(backend)

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    store = Store(tmp.name)
    engine = Engine(store, reg, owner="tomasz.skonieczny")

    pipe = Pipeline(id="real-backend-check", name="Real backend check", nodes=[
        Node(id="analyze", name="Requirements Analyst (REAL claude)",
             type=NodeType.PRODUCER, role="requirements-analyst",
             backend="claude_code", spec_ref=func_id, max_retries=1),
        Node(id="gate", name="Gate: Analysis", type=NodeType.GATE,
             gate=GateConfig(mode=GateMode.AUTO)),
    ])

    repo = AISdlcRepo(repo_path)
    ctx = repo.context_for(func_id) if repo.exists() else \
        {"func_id": func_id, "spec_text": f"Spec {func_id}", "lineage": [func_id]}
    # keep the prompt small/cheap for the probe
    ctx["spec_text"] = ctx.get("spec_text", "")[:1500]

    print(f"Running analyze via REAL claude CLI on {func_id} …")
    res = engine.start(pipe, ctx)
    print(f"  status: {res.status.value}")

    recs = store.audit_records(res.run_id)
    analyze = next((r for r in recs if r["node_id"] == "analyze"), None)
    if not analyze:
        print("  no analyze audit record")
        return 1

    print("\n=== REAL audit record (analyze) ===")
    print(f"  owner:     {analyze['owner']}")
    print(f"  status:    {analyze['status']}")
    print(f"  tools:     {analyze['tools']}")
    print(f"  decisions: {analyze['decisions']}")
    print(f"  cost:      {analyze['cost']}   (REAL $ from claude)")
    print(f"  lineage:   {' -> '.join(analyze['lineage'])}")
    print(f"  output:    {str(analyze['output'])[:400]}")

    real_cost = (analyze.get("cost") or {}).get("usd", 0)
    ok = analyze["status"] == "succeeded" and real_cost > 0
    print(f"\n  REAL DELEGATION {'VERIFIED' if ok else 'INCONCLUSIVE'} "
          f"(status={analyze['status']}, cost=${real_cost})")
    store.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
