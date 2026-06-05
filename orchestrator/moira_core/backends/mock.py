"""Deterministic mock backend — exercises the full pipeline without any API.

This is how the spike is testable here-and-now (operating-model.md pillar 6:
the spike is the first eval). It produces role-appropriate outputs, decisions,
findings (with tunable confidence/severity), and cost — so the engine, gates,
audit record, lineage and retries can all be asserted deterministically.

Tunables (via node.role and a per-run scenario dict) let tests force:
- a low-confidence finding  -> hybrid gate routes to human
- a HIGH-severity finding   -> auto gate escalates
- a transient failure       -> retry-N-then-gate path
"""
from __future__ import annotations

from typing import Any

from ..models import BackendResult, Cost, Finding, Node, Severity


class MockBackend:
    name = "mock"

    def __init__(self, scenario: dict[str, Any] | None = None) -> None:
        # scenario keys are node ids; values tune behavior for that node
        self.scenario = scenario or {}
        self._attempts: dict[str, int] = {}

    def run(self, node: Node, context: dict[str, Any]) -> BackendResult:
        cfg = self.scenario.get(node.id, {})

        # --- simulate transient failures for retry testing ----------------- #
        fail_times = cfg.get("fail_times", 0)
        attempt = self._attempts.get(node.id, 0)
        self._attempts[node.id] = attempt + 1
        if attempt < fail_times:
            return BackendResult(ok=False, error=f"transient failure (attempt {attempt + 1})",
                                 cost=Cost(tokens_in=100, tokens_out=0, usd=0.001))

        role = node.role or node.id
        spec = context.get("spec_text", "")
        upstream = context.get("upstream", {})

        handler = {
            "requirements-analyst": self._analyst,
            "solution-architect": self._architect,
            "code-generator": self._coder,
            "test-author": self._tester,
            "code-quality": self._quality,
            "security": self._security,
        }.get(role, self._generic)

        return handler(node, spec, upstream, cfg)

    # ---- producer roles ---------------------------------------------------- #
    def _analyst(self, node, spec, upstream, cfg) -> BackendResult:
        return BackendResult(
            output={
                "summary": "Parsed FUNC spec into structured requirements.",
                "requirements": ["auth via token", "persist session", "expire after 30m"],
                "gaps": cfg.get("gaps", []),
            },
            tools_used=["repo_reader", "spec_parser"],
            decisions=["Treated session expiry as a hard requirement (spec §3.2)."],
            cost=Cost(tokens_in=1200, tokens_out=400, usd=0.012),
        )

    def _architect(self, node, spec, upstream, cfg) -> BackendResult:
        return BackendResult(
            output={
                "design": "Token middleware + session store; stateless verify.",
                "adr_refs": ["ADR-005-authentication"],
                "components": ["auth.middleware", "session.store", "token.verify"],
            },
            tools_used=["repo_reader"],
            decisions=["Chose stateless JWT verify over server sessions (cost, scale)."],
            cost=Cost(tokens_in=1800, tokens_out=600, usd=0.020),
        )

    def _coder(self, node, spec, upstream, cfg) -> BackendResult:
        return BackendResult(
            output={
                "files": [
                    {"path": "src/auth/middleware.py", "lines": 84},
                    {"path": "src/auth/token.py", "lines": 41},
                ],
                "diff_summary": "+125 lines, 2 files",
            },
            tools_used=["claude_code_cli", "editor", "fs"],
            decisions=["Used PyJWT (matches standards/dependencies.md).",
                       "No secrets hardcoded; read from env (guardrail)."],
            cost=Cost(tokens_in=4200, tokens_out=2100, usd=0.078),
        )

    def _tester(self, node, spec, upstream, cfg) -> BackendResult:
        return BackendResult(
            output={
                "tests": ["test_token_expiry", "test_invalid_token", "test_missing_header"],
                "coverage": 0.87,
                "passed": True,
            },
            tools_used=["claude_code_cli", "pytest"],
            decisions=["Added negative-path tests for expiry and tampering."],
            cost=Cost(tokens_in=2600, tokens_out=900, usd=0.034),
        )

    # ---- verifier roles ---------------------------------------------------- #
    def _quality(self, node, spec, upstream, cfg) -> BackendResult:
        conf = cfg.get("confidence", 0.92)
        sev = Severity(cfg.get("severity", "low"))
        findings = [Finding(id="CQ-1", title="Function length within limits",
                            severity=Severity.INFO, confidence=0.99)]
        if cfg.get("add_finding", True):
            findings.append(Finding(id="CQ-2", title="Missing docstring on token.verify",
                                    severity=sev, confidence=conf,
                                    detail="standards/commenting.md"))
        return BackendResult(
            output={"verdict": "pass" if sev not in (Severity.HIGH, Severity.CRITICAL) else "fail"},
            tools_used=["linter", "standards_router"],
            decisions=["Checked against o2s/global/commenting.md"],
            findings=findings,
            cost=Cost(tokens_in=1500, tokens_out=300, usd=0.011),
        )

    def _security(self, node, spec, upstream, cfg) -> BackendResult:
        conf = cfg.get("confidence", 0.95)
        sev = Severity(cfg.get("severity", "low"))
        findings = [Finding(id="SEC-1", title="No hardcoded secrets detected",
                            severity=Severity.INFO, confidence=0.98)]
        if cfg.get("add_finding", True):
            findings.append(Finding(id="SEC-2", title="Dependency PyJWT pinned, no known CVE",
                                    severity=sev, confidence=conf))
        return BackendResult(
            output={"scan": "SAST+deps+secrets", "verdict":
                    "pass" if sev not in (Severity.HIGH, Severity.CRITICAL) else "fail"},
            tools_used=["sast", "dependency_scanner", "secrets_scanner"],
            decisions=["Ran automated security probing (no manual pentest — by design)."],
            findings=findings,
            cost=Cost(tokens_in=2000, tokens_out=400, usd=0.016),
        )

    def _generic(self, node, spec, upstream, cfg) -> BackendResult:
        return BackendResult(
            output={"note": f"{node.name} executed (generic mock)."},
            tools_used=["mock"],
            decisions=[],
            cost=Cost(tokens_in=500, tokens_out=200, usd=0.005),
        )
