"""Governance packs — schema validation, compile-to-nodes, log_hygiene check."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core.governance import validate_pack, compile_pack  # noqa: E402
from moira_core import (  # noqa: E402
    Engine, GateMode, Node, NodeType, Pipeline, Severity, validate_pipeline,
)


def _valid_pack():
    return {
        "id": "gdpr-basic",
        "version": "0.1.0",
        "domain": "data-protection",
        "jurisdiction": "EU",
        "applies_when": {"tags": ["personal-data"]},
        "required_evidence": ["data-flow-summary"],
        "checks": {
            "deterministic": [
                {"id": "secrets-scan", "command": "gitleaks detect --no-git", "severity_on_fail": "critical"},
                {"id": "test-exec", "built_in": "test_exec", "severity_on_fail": "high"},
            ],
            "llm": [
                {"id": "gdpr-review", "references": ["REG-GDPR"], "model_policy": "approved-frontier"},
            ],
        },
        "gate": {"mode": "human", "persona": "compliance", "blocks_on": ["high", "critical"]},
        "allowed_models": [],
        "override": {"allowed_personas": ["compliance-lead"], "requires_reason": True},
        "retention": {"audit_days": 3650},
    }


class TestValidatePack(unittest.TestCase):
    def test_valid_pack_has_no_errors(self):
        self.assertEqual(validate_pack(_valid_pack()), [])

    def test_missing_id_or_version(self):
        p = _valid_pack(); p.pop("id"); p.pop("version")
        errs = validate_pack(p)
        self.assertTrue(any("id" in e.lower() for e in errs))
        self.assertTrue(any("version" in e.lower() for e in errs))

    def test_deterministic_check_needs_built_in_or_command(self):
        p = _valid_pack()
        p["checks"]["deterministic"][0] = {"id": "x", "severity_on_fail": "high"}
        self.assertTrue(any("built_in" in e or "command" in e for e in validate_pack(p)))

    def test_unknown_built_in_check(self):
        p = _valid_pack()
        p["checks"]["deterministic"][1]["built_in"] = "does_not_exist"
        self.assertTrue(any("does_not_exist" in e for e in validate_pack(p)))

    def test_bad_severity_on_fail(self):
        p = _valid_pack()
        p["checks"]["deterministic"][0]["severity_on_fail"] = "catastrophic"
        self.assertTrue(any("catastrophic" in e for e in validate_pack(p)))

    def test_bad_gate_mode(self):
        p = _valid_pack(); p["gate"]["mode"] = "magic"
        self.assertTrue(any("magic" in e for e in validate_pack(p)))

    def test_blocks_on_must_be_subset_of_severities(self):
        p = _valid_pack(); p["gate"]["blocks_on"] = ["high", "nonsense"]
        self.assertTrue(any("nonsense" in e for e in validate_pack(p)))

    def test_duplicate_check_ids(self):
        p = _valid_pack()
        p["checks"]["deterministic"][1]["id"] = "secrets-scan"  # dup of [0]
        self.assertTrue(any("duplicate" in e.lower() and "secrets-scan" in e for e in validate_pack(p)))

    def test_no_checks_is_an_error(self):
        p = _valid_pack(); p["checks"] = {"deterministic": [], "llm": []}
        self.assertTrue(validate_pack(p))


class TestCompilePack(unittest.TestCase):
    def test_compiles_checks_and_gate(self):
        nodes = compile_pack(_valid_pack(), after=["prev"])
        by_id = {n.id: n for n in nodes}
        # 2 deterministic + 1 llm + 1 gate
        self.assertEqual(len(nodes), 4)
        det = by_id["gov-gdpr-basic-secrets-scan"]
        self.assertEqual(det.type, NodeType.AUTO_CHECK)
        self.assertEqual(det.check_cmd, "gitleaks detect --no-git")  # command -> check_cmd
        self.assertEqual(det.depends_on, ["prev"])                   # runs AFTER the host pipeline
        builtin = by_id["gov-gdpr-basic-test-exec"]
        self.assertEqual(builtin.check_kind, "test_exec")            # built_in -> check_kind
        llm = by_id["gov-gdpr-basic-gdpr-review"]
        self.assertEqual(llm.type, NodeType.PRODUCER)                # llm = qualitative producer
        gate = by_id["gov-gdpr-basic-gate"]
        self.assertEqual(gate.type, NodeType.GATE)
        self.assertEqual(gate.gate.mode, GateMode.HUMAN)
        self.assertEqual(gate.gate.persona, "compliance")
        # gate runs after every check node
        for cid in ("gov-gdpr-basic-secrets-scan", "gov-gdpr-basic-test-exec",
                    "gov-gdpr-basic-gdpr-review"):
            self.assertIn(cid, gate.depends_on)

    def test_effort_threads_to_llm_node_only(self):
        nodes = compile_pack(_valid_pack(), after=["prev"], effort="high")
        by_id = {n.id: n for n in nodes}
        self.assertEqual(by_id["gov-gdpr-basic-gdpr-review"].effort, "high")  # advisory LLM node
        self.assertEqual(by_id["gov-gdpr-basic-secrets-scan"].effort, "")     # deterministic check
        self.assertEqual(by_id["gov-gdpr-basic-gate"].effort, "")             # gate

    def test_attach_pack_threads_effort(self):
        from moira_core.governance import attach_pack
        host = Pipeline(id="p", name="P", nodes=[
            Node(id="work", name="work", type=NodeType.PRODUCER, role="x", backend="mock")])
        attach_pack(host, _valid_pack(), effort="xhigh")
        llm = next(n for n in host.nodes if n.id == "gov-gdpr-basic-gdpr-review")
        self.assertEqual(llm.effort, "xhigh")

    def test_compiled_pipeline_validates(self):
        host = Node(id="prev", name="work", type=NodeType.PRODUCER, role="x")
        pipe = Pipeline(id="p", name="P", nodes=[host] + compile_pack(_valid_pack(), after=["prev"]))
        self.assertEqual(validate_pipeline(pipe), [])


class TestRepoPacks(unittest.TestCase):
    def test_list_and_get_packs(self):
        from moira_core import AISdlcRepo
        root = tempfile.mkdtemp()
        pd = Path(root, ".ai", "standards", "compliance", "packs")
        pd.mkdir(parents=True)
        (pd / "demo.json").write_text(json.dumps({
            "id": "demo", "version": "0.2.0",
            "checks": {"deterministic": [{"id": "x", "built_in": "log_hygiene",
                                          "severity_on_fail": "high"}]},
            "gate": {"mode": "auto"}}), "utf-8")
        repo = AISdlcRepo(root)
        self.assertIn("demo", [p["id"] for p in repo.list_packs()])
        self.assertEqual(repo.get_pack("demo")["version"], "0.2.0")
        self.assertIsNone(repo.get_pack("missing"))


class TestAttachAndE2E(unittest.TestCase):
    def test_attach_preserves_host_order_and_runs_after(self):
        from moira_core.governance import attach_pack
        host = Pipeline(id="p", name="P", nodes=[
            Node(id="a", name="a", type=NodeType.PRODUCER, role="x", backend="mock"),
            Node(id="b", name="b", type=NodeType.PRODUCER, role="x", backend="mock"),
        ])  # implicit linear: b depends on a
        pack = {"id": "demo", "version": "0.1.0",
                "checks": {"deterministic": [{"id": "lh", "built_in": "log_hygiene",
                                              "severity_on_fail": "critical"}], "llm": []},
                "gate": {"mode": "auto", "persona": "compliance", "blocks_on": ["critical"]}}
        attach_pack(host, pack)
        by = {n.id: n for n in host.nodes}
        self.assertEqual(by["b"].depends_on, ["a"])             # host linear order materialized
        self.assertEqual(by["gov-demo-lh"].depends_on, ["b"])  # governance runs after the host tail
        self.assertEqual(validate_pipeline(host), [])

    def test_log_leak_escalates_governance_gate(self):
        from moira_core import Store, BackendRegistry, MockBackend
        from moira_core.governance import attach_pack
        repo = tempfile.mkdtemp()
        Path(repo, "app.py").write_text(
            'import logging\nlog = logging.getLogger(__name__)\n'
            'def f(password):\n    log.info(f"pwd={password}")\n', "utf-8")
        pack = {"id": "logs-test", "version": "0.1.0",
                "checks": {"deterministic": [{"id": "log-hygiene", "built_in": "log_hygiene",
                                              "severity_on_fail": "critical"}], "llm": []},
                "gate": {"mode": "auto", "persona": "compliance", "blocks_on": ["high", "critical"]}}
        host = Pipeline(id="p", name="P", nodes=[
            Node(id="work", name="work", type=NodeType.PRODUCER, role="x", backend="mock")])
        attach_pack(host, pack)
        db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); db.close()
        store = Store(db.name)
        reg = BackendRegistry(); reg.register(MockBackend())
        res = Engine(store, reg, owner="t").start(host, {"cwd": repo})
        self.assertEqual(res.status.value, "waiting_gate")  # CRITICAL leak blocks the gov gate
        recs = {r["node_id"]: r for r in store.audit_records(res.run_id)}
        self.assertFalse(recs["gov-logs-test-log-hygiene"]["output"]["passed"])
        store.close()


class TestSummarizeGovernance(unittest.TestCase):
    def test_coverage_from_audit(self):
        from moira_core.governance import summarize_governance
        audit = [
            {"node_id": "gov-p1", "output": {"governance": "applied", "pack": "p1",
             "version": "0.1.0", "fingerprint": "abc", "required_evidence": ["e1"],
             "gate_node": "gov-p1-gate",
             "checks": [{"id": "lh", "node_id": "gov-p1-lh", "kind": "deterministic"},
                        {"id": "rev", "node_id": "gov-p1-rev", "kind": "llm"}]}},
            {"node_id": "gov-p1-lh", "output": {"passed": False}},
            {"node_id": "gov-p1-rev", "output": {"summary": "looks ok"}},
            {"node_id": "gov-p1-gate", "approvals": [{"decision": "escalate"}]},
        ]
        out = summarize_governance(audit)
        self.assertEqual(len(out), 1)
        pr = out[0]
        self.assertEqual(pr["pack"], "p1")
        statuses = {c["id"]: c["status"] for c in pr["checks"]}
        self.assertEqual(statuses["lh"], "failed")
        self.assertEqual(statuses["rev"], "advisory")
        self.assertEqual(pr["gate_decision"], "escalate")


class TestReportCoverage(unittest.TestCase):
    def test_report_renders_governance_table(self):
        from moira_core.report import render_run_report
        audit = [
            {"node_id": "gov-p1", "node_name": "Governance · p1", "status": "succeeded",
             "output": {"governance": "applied", "pack": "p1", "version": "0.1.0",
                        "fingerprint": "deadbeef", "required_evidence": ["e1"],
                        "gate_node": "gov-p1-gate",
                        "checks": [{"id": "lh", "node_id": "gov-p1-lh", "kind": "deterministic"}]}},
            {"node_id": "gov-p1-lh", "node_name": "lh", "status": "succeeded", "output": {"passed": False}},
            {"node_id": "gov-p1-gate", "node_name": "gate", "status": "succeeded",
             "approvals": [{"decision": "escalate"}]},
        ]
        md = render_run_report({"run": {"run_id": "r"}, "pipeline": {"id": "p"},
                                "audit": audit, "cost": {}})
        self.assertIn("## Governance · p1@0.1.0", md)
        self.assertIn("✗ failed", md)
        self.assertIn("advisory", md.lower())


def _max_severity(res):
    return max((f.severity for f in res.findings), default=Severity.INFO)


class TestLogHygieneCheck(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.engine = Engine(None, None, owner="t")  # checks don't touch store/registry

    def _check(self):
        node = Node(id="log-hygiene", name="log hygiene", type=NodeType.AUTO_CHECK,
                    check_kind="log_hygiene")
        return self.engine._run_check(node, {"cwd": self.cwd})

    def test_sensitive_data_in_log_is_critical(self):
        Path(self.cwd, "app.py").write_text(
            'import logging\nlog = logging.getLogger(__name__)\n'
            'def login(password):\n    log.info(f"user login pwd={password}")\n', "utf-8")
        res = self._check()
        self.assertFalse(res.output["passed"])
        self.assertEqual(_max_severity(res), Severity.CRITICAL)

    def test_clean_logging_passes(self):
        Path(self.cwd, "app.py").write_text(
            'import logging\nlog = logging.getLogger(__name__)\n'
            'def login(user_id):\n    log.info("user login id=%s", user_id)\n', "utf-8")
        res = self._check()
        self.assertTrue(res.output["passed"])
        self.assertEqual(_max_severity(res), Severity.INFO)

    def test_raw_print_in_app_code_is_medium(self):
        Path(self.cwd, "svc.py").write_text('def f():\n    print("debug here", x)\n', "utf-8")
        res = self._check()
        self.assertFalse(res.output["passed"])
        self.assertEqual(_max_severity(res), Severity.MEDIUM)


class TestShellCheckNotApplicable(unittest.TestCase):
    def test_missing_tool_is_not_applicable_not_failure(self):
        engine = Engine(None, None, owner="t")
        node = Node(id="a11y", name="axe", type=NodeType.AUTO_CHECK,
                    check_cmd="moira-nonexistent-binary-xyz --scan")
        res = engine._run_check(node, {"cwd": tempfile.mkdtemp()})
        self.assertEqual(res.output.get("status"), "not_applicable")
        self.assertTrue(res.output["passed"])              # N/A must NOT block the gate
        self.assertEqual(_max_severity(res), Severity.INFO)


if __name__ == "__main__":
    unittest.main()
