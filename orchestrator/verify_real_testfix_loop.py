#!/usr/bin/env python3
"""Dogfood of the closed, bounded test-fix loop on the REAL claude CLI (A1).

Spends real money. Exercises ADR-009/010/011/014 end-to-end:
  implement (claude_code) -> test_exec (real unittest run) -> AUTO gate
  (escalation off, max_loop=3, rework_check_output=ON) -> reject with findings
  digest + raw failing-test output -> informed rework -> green or forced
  human escalation.

The scenario is designed so the FIRST attempt should fail honestly: the spec
handed to the producer is deliberately incomplete — two acceptance details
(the exact empty-basket error message, ROUND_HALF_UP money rounding) live only
in the QA-owned tests, which the spec forbids the agent to read or modify.
The loop's thesis is precisely that the deterministic check's raw output
surfaces what the spec missed, and the informed rework converges.

Everything runs in a throwaway temp workspace — no existing repo is touched.

Run:  python3 verify_real_testfix_loop.py [--max-loop 3]
Out:  JSON metrics on stdout + full event/audit dump next to the temp workspace.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from moira_core import BackendRegistry, Engine, GateConfig, GateMode, Store  # noqa: E402
from moira_core.backends import ClaudeCodeBackend  # noqa: E402
from moira_core.models import Node, NodeType, Pipeline  # noqa: E402
from moira_core import integrity  # noqa: E402

FUNC_ID = "FUNC-DOG-basket-total"

SPEC = """\
# FUNC-DOG-basket-total — Basket total with discount

Implement `basket_total(items, discount_percent)` in `basket.py` (repo root).

Functional contract:
- `items` is a list of (name, unit_price, quantity) tuples; the total is the
  sum of unit_price * quantity over all items.
- `discount_percent` (0..100) reduces the total by that percentage.
- The returned total is MONEY: a float rounded to exactly 2 decimal places
  using commercial (merchant) rounding conventions.
- An empty basket must be rejected.
- A discount outside 0..100 must raise ValueError("invalid discount").

Constraints:
- The acceptance tests in tests/ are the contract OWNED BY QA. Do NOT read,
  open, or modify anything under tests/ — implement from this spec only.
  (If your work fails the acceptance run, you will receive the failing output
  and may fix the implementation.)
- Only edit basket.py. Do not add dependencies. Do not run the tests yourself.
"""

TESTS = """\
import unittest

from basket import basket_total


class TestBasketTotal(unittest.TestCase):
    def test_simple_total(self):
        self.assertEqual(basket_total([("apple", 2.50, 2), ("bread", 4.00, 1)], 0), 9.00)

    def test_discount_applied(self):
        self.assertEqual(basket_total([("apple", 10.00, 1)], 25), 7.50)

    def test_money_rounding_is_half_up(self):
        # 5.35 * 1 with 50% discount = 2.675 -> merchant (HALF_UP) rounding = 2.68
        # (naive float round() gives 2.67 — banker's rounding on the float repr)
        self.assertEqual(basket_total([("candle", 5.35, 1)], 50), 2.68)

    def test_empty_basket_raises_with_exact_message(self):
        with self.assertRaises(ValueError) as ctx:
            basket_total([], 0)
        self.assertEqual(str(ctx.exception), "empty basket")

    def test_invalid_discount(self):
        with self.assertRaises(ValueError) as ctx:
            basket_total([("apple", 1.00, 1)], 101)
        self.assertEqual(str(ctx.exception), "invalid discount")


if __name__ == "__main__":
    unittest.main()
"""

STUB = """\
def basket_total(items, discount_percent):
    raise NotImplementedError
"""

# Scenario B ("rework"): the QA contract encodes decisions the spec genuinely
# does not contain and a competent agent cannot guess — an exotic exception
# type/message for the empty basket and a zero-quantity rule the spec is silent
# about. This forces >=1 honest first-attempt failure, so the loop itself
# (reject -> findings digest + FAILING CHECK OUTPUT -> informed rework) is what
# gets measured. Scenario A ("oneshot") keeps the guessable contract as the
# happy-path/cost baseline.
TESTS_B = TESTS.replace(
    """    def test_empty_basket_raises_with_exact_message(self):
        with self.assertRaises(ValueError) as ctx:
            basket_total([], 0)
        self.assertEqual(str(ctx.exception), "empty basket")
""",
    """    def test_empty_basket_raises_lookup_error(self):
        # QA decision not present in the spec: LookupError, exact message
        with self.assertRaises(LookupError) as ctx:
            basket_total([], 0)
        self.assertEqual(str(ctx.exception), "no items")

    def test_zero_quantity_is_rejected(self):
        # QA decision the spec is silent about
        with self.assertRaises(ValueError) as ctx:
            basket_total([("apple", 1.00, 0)], 0)
        self.assertEqual(str(ctx.exception), "invalid quantity")
""")


def make_code_repo(root: Path, tests: str) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "basket.py").write_text(STUB, encoding="utf-8")
    (root / "tests" / "test_basket.py").write_text(tests, encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=dogfood@moira", "-c", "user.name=dogfood",
                 "commit", "-qm", "seed: stub + QA acceptance tests"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)


def build_pipeline(max_loop: int) -> Pipeline:
    return Pipeline(id="dogfood-testfix", name="Dogfood — closed test-fix loop", nodes=[
        Node(id="implement", name="Implement basket_total", type=NodeType.PRODUCER,
             backend="claude_code", role="code-generator", spec_ref=FUNC_ID,
             timeout=300, max_turns=30, max_retries=1),
        Node(id="tests", name="Acceptance tests", type=NodeType.AUTO_CHECK,
             check_kind="test_exec",
             check_cmd="python3 -m unittest discover -s tests -v",
             depends_on=["implement"]),
        Node(id="gate", name="Quality gate", type=NodeType.GATE,
             gate=GateConfig(mode=GateMode.AUTO, escalate_on_blocking=False,
                             consumes=["tests"], persona="lead-dev",
                             max_loop=max_loop, rework_check_output=True),
             on_reject_goto="implement"),
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-loop", type=int, default=3)
    ap.add_argument("--scenario", choices=("oneshot", "rework"), default="rework")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="moira-dogfood-"))
    code = work / "code"
    make_code_repo(code, TESTS if args.scenario == "oneshot" else TESTS_B)
    db = work / "moira.sqlite"
    live = work / "live.jsonl"
    print(f"workspace: {work}", file=sys.stderr)

    store = Store(str(db))
    reg = BackendRegistry()
    reg.register(ClaudeCodeBackend())
    engine = Engine(store, reg, owner="dogfood")

    ctx = {"func_id": FUNC_ID, "spec_text": SPEC,
           "lineage": [FUNC_ID, "REQ-DOG-01"],
           "cwd": str(code), "live_path": str(live)}

    t0 = time.time()
    res = engine.start(build_pipeline(args.max_loop), ctx)
    wall = time.time() - t0

    # ---- metrics from the audit trail (the product's own source of truth) ---- #
    recs = store.audit_records(res.run_id)
    events = store.events(res.run_id)
    impl = [r for r in recs if r["node_id"] == "implement"]
    checks = [r for r in recs if r["node_id"] == "tests"]
    rejects = [ap_ for r in recs if r["node_id"] == "gate"
               for ap_ in r.get("approvals", [])
               if ap_.get("decision") == "reject" and ap_.get("by") == "system"]
    cost = store.run_cost(res.run_id)
    chain = integrity.verify_chain(recs)

    # independent confirmation: do the acceptance tests actually pass now?
    proc = subprocess.run(["python3", "-m", "unittest", "discover", "-s", "tests"],
                          cwd=code, capture_output=True, text=True)
    tests_green = proc.returncode == 0

    (work / "audit.json").write_text(json.dumps(recs, indent=2, default=str), encoding="utf-8")
    (work / "events.json").write_text(json.dumps(events, indent=2, default=str), encoding="utf-8")

    summary = {
        "run_id": res.run_id,
        "status": res.status.value,
        "unattended": res.status.value == "succeeded",
        "wall_seconds": round(wall, 1),
        "implement_iterations": len(impl),
        "system_rejects": len(rejects),
        "check_runs": len(checks),
        "checks_passed_per_run": [bool((r.get("output") or {}).get("passed")) for r in checks],
        "rework_had_check_output": [bool((r.get("input") or {}).get("check_output")) for r in impl],
        "cost_usd": round(cost["usd"], 4),
        "tokens_in": cost["tokens_in"], "tokens_out": cost["tokens_out"],
        "audit_chain": chain,
        "tests_green_independently": tests_green,
        "workspace": str(work),
    }
    print(json.dumps(summary, indent=2))
    return 0 if (tests_green and res.status.value == "succeeded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
