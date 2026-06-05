"""Run the `sdlc-full-crosschecked` pipeline for REAL via claude_code, showing
cross-model execution (planner/reviewer/security on a stronger model) + costs.

Loads the seeded pipeline + agents from the AI SDLC repo, forces non-gate nodes
onto the claude_code backend (per-node `model` preserved → cross-model), runs
into a target code dir, and reports per-node model/cost/time. Tiny inline spec
to bound cost. Needs an authenticated `claude` CLI.

Usage: python3 verify_real_crosschecked.py [repo_path] [target_dir]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from moira_core import BackendRegistry, Engine, NodeType, Store
from moira_core.backends import ClaudeCodeBackend, MockBackend
from moira_core.repo_reader import AISdlcRepo

TINY_SPEC = """# FUNC-DEMO-slugify: slugify(text) utility
## Requirements
- `slugify(text: str) -> str` in `slugify.py`: lowercase, non-alphanumeric runs -> single hyphen, strip edge hyphens, empty -> "".
## Acceptance
- slugify("Hello, World!") == "hello-world"; slugify("  A__B  ") == "a-b"; slugify("") == ""
"""


def main() -> int:
    repo_path = sys.argv[1] if len(sys.argv) > 1 else "../../ai-sdlc"
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/moira-xcheck-code")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=target, check=False)

    backend = ClaudeCodeBackend()
    if not backend.available():
        print("claude CLI not on PATH — cannot run.")
        return 2

    repo = AISdlcRepo(repo_path)
    pdef = repo.get_pipeline_def("sdlc-full-crosschecked")
    if not pdef:
        print("sdlc-full-crosschecked not found — run seed_definitions.py first.")
        return 1
    pipe = repo.build_pipeline(pdef, func_ref="FUNC-DEMO-slugify")
    # force real backend on every non-gate node; per-node model is preserved
    for n in pipe.nodes:
        if n.type != NodeType.GATE and n.type != NodeType.AUTO_CHECK:
            n.backend = "claude_code"

    print("Cross-model plan (node -> model):")
    for n in pipe.nodes:
        if n.type not in (NodeType.GATE, NodeType.AUTO_CHECK):
            print(f"  {n.id:<14} {n.model or '(default)'}")

    reg = BackendRegistry(); reg.register(backend); reg.register(MockBackend())
    store = Store(tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False).name)
    eng = Engine(store, reg, owner="tomasz.skonieczny")
    ctx = {"func_id": "FUNC-DEMO-slugify", "spec_text": TINY_SPEC,
           "lineage": ["FUNC-DEMO-slugify"], "cwd": str(target)}

    print("\nRunning FULL cross-checked pipeline via REAL claude …\n")
    res = eng.start(pipe, ctx)
    print(f"  status: {res.status.value}\n")

    print("  === per-node (model · cost · time · status) ===")
    total = 0.0
    for rec in store.audit_records(res.run_id):
        c = rec.get("cost") or {}
        usd = c.get("usd", 0) or 0
        total += usd
        model = (rec.get("input") or {}).get("model", "-")
        tag = "GATE" if rec["node_id"].startswith("gate") else rec["node_id"]
        print(f"   {tag:<14} {str(model):<10} ${usd:<8.4f} {rec.get('duration',0):>5.1f}s  {rec['status']}")
    print(f"\n  TOTAL real cost: ${total:.4f}")

    files = [p.relative_to(target) for p in target.rglob("*") if p.is_file() and ".git" not in p.parts]
    print(f"  files written: {[str(f) for f in files]}")
    store.close()
    return 0 if res.status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
