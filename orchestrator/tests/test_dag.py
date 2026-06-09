"""DAG engine: parallel branches, AUTO_CHECK nodes, depends_on ordering."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    BackendRegistry, Engine, GateConfig, GateMode, Node, NodeType, Pipeline,
    Status, Store,
)
from moira_core.backends.base import AgentBackend  # noqa: E402
from moira_core.backends.mock import MockBackend  # noqa: E402
from moira_core.models import BackendResult, Cost  # noqa: E402


def engine(backend=None):
    store = Store(tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name)
    reg = BackendRegistry(); reg.register(backend or MockBackend())
    return Engine(store, reg, owner="t"), store


class SlowBackend:
    """Records overlap to prove parallel execution."""
    name = "mock"
    def __init__(self):
        self.active = 0; self.max_active = 0; self.lock_n = 0
    def run(self, node: Node, context) -> BackendResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(0.15)
        self.active -= 1
        return BackendResult(output={"n": node.id}, ok=True, cost=Cost())


class TestAcCoverageCheck(unittest.TestCase):
    """AUTO_CHECK check_kind='ac_coverage' — deterministic AC-coverage gate after decompose."""

    def _repo(self, task_acs):
        from pathlib import Path
        root = Path(tempfile.mkdtemp())
        fs = root / ".ai" / "context" / "func-specs" / "FUNC-X"
        fs.mkdir(parents=True)
        (fs / "func-spec.md").write_text(
            "# FUNC-X\n**AC-X-01-01:** a\n**AC-X-01-02:** b\n**AC-X-02-01:** c\n", encoding="utf-8")
        bl = root / "backlog" / "func-x"
        bl.mkdir(parents=True)
        (bl / "epic-func-x.md").write_text(
            "---\nid: epic-func-x\ntype: epic\nstatus: todo\nsource_func: FUNC-X\n---\n", encoding="utf-8")
        for i, acs in enumerate(task_acs, 1):
            (bl / f"PROJ-{i}.md").write_text(
                f"---\nid: PROJ-{i}\ntype: task\nstatus: todo\nparent_epic_id: epic-func-x\n"
                f"acceptance_criteria: [{', '.join(acs)}]\n---\n", encoding="utf-8")
        return str(root)

    def _check(self, cwd):
        eng, _ = engine()
        node = Node(id="cov", name="cov", type=NodeType.AUTO_CHECK, check_kind="ac_coverage", spec_ref="FUNC-X")
        return eng._run_ac_coverage_check(node, {"cwd": cwd})

    def test_full_coverage_passes(self):
        res = self._check(self._repo([["AC-X-01-01", "AC-X-01-02"], ["AC-X-02-01"]]))  # 3/3
        self.assertTrue(res.output["passed"])
        self.assertFalse(res.has_blocking())     # INFO, not blocking

    def test_partial_coverage_fails_blocking(self):
        res = self._check(self._repo([["AC-X-01-01"]]))  # 1/3
        self.assertFalse(res.output["passed"])
        self.assertTrue(res.has_blocking())       # HIGH -> downstream AUTO gate escalates

    def test_no_acceptance_criteria_fails(self):
        from pathlib import Path
        root = Path(tempfile.mkdtemp())
        (root / ".ai" / "context" / "func-specs").mkdir(parents=True)
        res = self._check(str(root))
        self.assertFalse(res.output["passed"])

    def _pipe(self):
        # mirrors the discovery builder for pm@decompose-func: author → check → AUTO cov-gate → HUMAN review
        return Pipeline(id="dec", name="dec", nodes=[
            Node(id="impl", name="impl", type=NodeType.PRODUCER, backend="mock", spec_ref="FUNC-X"),
            Node(id="check", name="AC coverage", type=NodeType.AUTO_CHECK, check_kind="ac_coverage",
                 spec_ref="FUNC-X", depends_on=["impl"]),
            Node(id="cov", name="AC coverage gate", type=NodeType.GATE, depends_on=["check"],
                 on_reject_goto="impl", gate=GateConfig(mode=GateMode.AUTO, consumes=["check"])),
            Node(id="review", name="review", type=NodeType.GATE, depends_on=["cov"],
                 on_reject_goto="impl", gate=GateConfig(mode=GateMode.HUMAN, consumes=["impl"])),
        ])

    def _run(self, task_acs):
        eng, _ = engine()
        ctx = {"cwd": self._repo(task_acs), "spec_text": "x", "lineage": [], "func_id": "FUNC-X"}
        return eng.start(self._pipe(), ctx)

    def test_full_coverage_flows_to_human_review(self):
        res = self._run([["AC-X-01-01", "AC-X-01-02"], ["AC-X-02-01"]])   # 3/3
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "review")   # cov auto-approved → human reviews quality

    def test_partial_coverage_escalates_at_cov_gate(self):
        res = self._run([["AC-X-01-01"]])              # 1/3
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "cov")      # incomplete → escalate before the human gate


class TestTestExecCheck(unittest.TestCase):
    """AUTO_CHECK check_kind='test_exec' — run the project's test suite (green vs a mere test-plan)."""

    def _check(self, cmd, cwd=None):
        eng, _ = engine()
        n = Node(id="t", name="t", type=NodeType.AUTO_CHECK, check_kind="test_exec", check_cmd=cmd)
        return eng._run_test_exec_check(n, {"cwd": cwd})

    def test_passing_parses_jest_summary(self):
        res = self._check("python3 -c \"print('Tests: 12 passed, 12 total')\"")
        self.assertTrue(res.output["passed"])
        self.assertIn("12 passed", res.output["summary"])
        self.assertFalse(res.has_blocking())            # INFO

    def test_failing_blocks(self):
        res = self._check('python3 -c "import sys; sys.exit(1)"')
        self.assertFalse(res.output["passed"])
        self.assertTrue(res.has_blocking())             # HIGH → downstream gate escalates

    def test_parse_pytest_counts(self):
        self.assertIn("10 passed", Engine._parse_test_counts("==== 10 passed, 2 skipped in 1.2s ===="))

    def test_detect_npm(self):
        import tempfile, os, json
        d = tempfile.mkdtemp()
        json.dump({"scripts": {"test": "jest"}}, open(os.path.join(d, "package.json"), "w"))
        self.assertEqual(Engine._detect_test_cmd(d), "npm test --silent")

    def test_detect_pytest(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        open(os.path.join(d, "pyproject.toml"), "w").close()
        self.assertEqual(Engine._detect_test_cmd(d), "pytest -q")

    def test_no_runner_passes_without_blocking(self):
        import tempfile
        res = self._check("", cwd=tempfile.mkdtemp())   # nothing to run / detect
        self.assertTrue(res.output["passed"])
        self.assertFalse(res.has_blocking())


class TestSkillPipelineNode(unittest.TestCase):
    """build_pipeline supports `skill:` nodes → authoring pipelines (Discovery as a pipeline)."""

    def test_skill_node_builds_authoring_producer(self):
        from moira_core.repo_reader import AISdlcRepo
        repo = AISdlcRepo("/tmp/does-not-exist")
        pdef = {"id": "p", "name": "p", "nodes": [
            {"id": "intent", "skill": "ba@shape-intent-spec"},
            {"id": "g", "type": "gate", "depends_on": ["intent"], "gate": {"mode": "human", "persona": "po"}},
        ]}
        pipe = repo.build_pipeline(pdef, func_ref="INT-X")
        n = pipe.nodes[0]
        self.assertEqual(n.skill, "ba@shape-intent-spec")
        self.assertEqual(n.role, "ba-skill")
        self.assertEqual(n.backend, "claude_code")
        self.assertEqual(n.type, NodeType.PRODUCER)
        self.assertEqual(pipe.nodes[1].type, NodeType.GATE)


class TestParallel(unittest.TestCase):
    def test_independent_nodes_run_concurrently(self):
        be = SlowBackend()
        eng, store = engine(be)
        # root -> {a, b, c} all depend on root, then join gate
        nodes = [
            Node(id="root", name="root", type=NodeType.PRODUCER, backend="mock"),
            Node(id="a", name="a", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
            Node(id="b", name="b", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
            Node(id="c", name="c", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
        ]
        res = eng.start(Pipeline(id="p", name="p", nodes=nodes), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)
        self.assertGreaterEqual(be.max_active, 2, "a,b,c should have run in parallel")

    def test_dag_join_waits_for_all(self):
        eng, store = engine()
        nodes = [
            Node(id="root", name="root", type=NodeType.PRODUCER, backend="mock"),
            Node(id="a", name="a", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
            Node(id="b", name="b", type=NodeType.VERIFIER, backend="mock", depends_on=["root"]),
            Node(id="gate", name="gate", type=NodeType.GATE, depends_on=["a", "b"],
                 gate=GateConfig(mode=GateMode.AUTO, consumes=["a", "b"])),
        ]
        res = eng.start(Pipeline(id="p", name="p", nodes=nodes), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED)
        recs = {r["node_id"] for r in store.audit_records(res.run_id)}
        self.assertTrue({"root", "a", "b", "gate"}.issubset(recs))


class TestAutoCheck(unittest.TestCase):
    def _pipe(self, cmd):
        return Pipeline(id="ac", name="ac", nodes=[
            Node(id="impl", name="impl", type=NodeType.PRODUCER, backend="mock"),
            Node(id="check", name="check", type=NodeType.AUTO_CHECK, check_cmd=cmd, depends_on=["impl"]),
            Node(id="gate", name="gate", type=NodeType.GATE, depends_on=["check"],
                 gate=GateConfig(mode=GateMode.AUTO, consumes=["check"])),
        ])

    def test_passing_check_auto_approves(self):
        eng, store = engine()
        res = eng.start(self._pipe('python3 -c "print(1)"'), {"spec_text": "x", "lineage": []})
        self.assertEqual(res.status, Status.SUCCEEDED, msg=str(res))

    def test_failing_check_escalates_gate(self):
        eng, store = engine()
        res = eng.start(self._pipe('python3 -c "import sys; sys.exit(1)"'),
                        {"spec_text": "x", "lineage": []})
        # auto gate escalates on the HIGH finding from a failed check
        self.assertEqual(res.status, Status.WAITING_GATE)
        self.assertEqual(res.waiting_node, "gate")

    def test_check_records_real_result(self):
        eng, store = engine()
        res = eng.start(self._pipe('python3 -c "print(1)"'), {"spec_text": "x", "lineage": []})
        check = next(r for r in store.audit_records(res.run_id) if r["node_id"] == "check")
        self.assertTrue(check["output"]["passed"])
        self.assertTrue(any(t.startswith("shell:") for t in check["tools"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
