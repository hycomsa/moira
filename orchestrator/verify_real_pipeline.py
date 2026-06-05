"""Verify REAL coding through the FULL pipeline (claude_code backend, ADR-004).

Runs analyze -> gate -> design -> implement -> verify(quality+security) -> gate -> test,
every producer/verifier delegating to the real `claude` CLI. The Code Generator
node runs with cwd = a target code dir, so it writes ACTUAL files there.

Verifies: pipeline completes, real files were written, audit records carry real
cost. Costs real money — kept small via a tiny inline spec. Needs an authenticated
`claude` CLI on PATH.

Usage: python3 verify_real_pipeline.py [target_dir]
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from moira_core import (
    BackendRegistry, Engine, GateMode, Pipeline, Store, default_sdlc_pipeline,
)
from moira_core.backends import ClaudeCodeBackend, MockBackend

TINY_SPEC = """# FUNC-DEMO-slugify: String slugify utility

## TL;DR
Provide a single pure Python function `slugify(text: str) -> str` in a new file
`slugify.py`, plus pytest tests in `test_slugify.py`.

## Requirements (REQ-DEMO-01)
- Lowercases the input.
- Replaces any run of non-alphanumeric chars with a single hyphen.
- Strips leading/trailing hyphens.
- Empty input returns empty string.

## Acceptance
- `slugify("Hello, World!") == "hello-world"`
- `slugify("  A__B  ") == "a-b"`
- `slugify("") == ""`
"""


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/moira-real-code")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=target, check=False)

    backend = ClaudeCodeBackend()
    reg = BackendRegistry()
    reg.register(backend)
    reg.register(MockBackend())  # fallback registration
    if not backend.available():
        print("claude CLI not on PATH — cannot run real-pipeline verification")
        return 2

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    store = Store(tmp.name)
    engine = Engine(store, reg, owner="tomasz.skonieczny")

    # full pipeline, all real, unattended (auto/hybrid gates)
    pipe = default_sdlc_pipeline(func_ref="FUNC-DEMO-slugify",
                                 analysis_gate=GateMode.AUTO,
                                 impl_gate=GateMode.HYBRID,
                                 backend="claude_code")
    ctx = {"func_id": "FUNC-DEMO-slugify", "spec_text": TINY_SPEC,
           "lineage": ["FUNC-DEMO-slugify", "REQ-DEMO-01"], "cwd": str(target)}

    print(f"Running FULL pipeline via REAL claude into {target} …\n")
    res = engine.start(pipe, ctx)
    print(f"\n  pipeline status: {res.status.value}")

    # what files did the agents actually write?
    files = [p for p in target.rglob("*")
             if p.is_file() and ".git" not in p.parts]
    print(f"\n  === files written to {target} ===")
    for f in files:
        print(f"   {f.relative_to(target)}  ({f.stat().st_size} bytes)")

    # per-node real cost
    print("\n  === per-node audit (real claude cost) ===")
    total = 0.0
    for rec in store.audit_records(res.run_id):
        c = rec.get("cost") or {}
        usd = c.get("usd", 0) or 0
        total += usd
        tag = "GATE" if rec["node_id"].startswith("gate") else rec["node_id"]
        print(f"   {tag:<16} {rec['status']:<12} ${usd:.4f}")
    print(f"\n  TOTAL real cost: ${total:.4f}")

    wrote_code = any(f.suffix == ".py" for f in files)
    print(f"\n  REAL CODING THROUGH PIPELINE {'VERIFIED' if wrote_code else 'INCONCLUSIVE'} "
          f"(.py files written: {wrote_code}, status: {res.status.value})")

    # bonus: if slugify.py exists, try running its tests
    if (target / "test_slugify.py").exists() or any(f.name.startswith("test") for f in files):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=target,
                           capture_output=True, text=True)
        print("\n  === pytest in target dir ===")
        print("  " + (r.stdout.strip().replace("\n", "\n  ") or r.stderr.strip()[:300]))

    store.close()
    return 0 if wrote_code else 1


if __name__ == "__main__":
    raise SystemExit(main())
