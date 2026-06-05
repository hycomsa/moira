"""render_run_report — Markdown rendering of a run's audit."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core.report import render_run_report  # noqa: E402

PAYLOAD = {
    "run": {"run_id": "run-abc", "owner": "tomek", "status": "succeeded",
            "pipeline_id": "sdlc-rn-mobile", "workspace_id": "csl-driver"},
    "pipeline": {"id": "sdlc-rn-mobile", "name": "SDLC — React Native (mobile)", "nodes": []},
    "cost": {"tokens_in": 1000, "tokens_out": 500, "usd": 0.42},
    "audit": [
        {"node_id": "implement", "node_name": "Frontend Developer", "status": "succeeded",
         "owner": "tomek", "input": {"backend": "claude_code", "model": "(default)"},
         "decisions": ["wrote onboarding screen"], "tools": ["Write", "Bash"],
         "approvals": [], "cost": {"usd": 0.3}, "duration": 120.0,
         "lineage": ["FUNC-APP-onboarding", "REQ-APP-03", "INT-APP-driver-mobile-app"],
         "output": {"files": [{"path": "src/Onboarding.tsx", "status": "M",
                               "additions": 14, "deletions": 14}], "patch": "…", "truncated": False}},
        {"node_id": "gate-impl", "node_name": "Impl gate", "status": "succeeded",
         "owner": "lead-dev", "input": {}, "decisions": [], "tools": [], "cost": {},
         "duration": 0.0, "lineage": [],
         "approvals": [{"decision": "approve", "by": "lead-dev", "confirmed": "looks good"}],
         "output": {"decision": "approve"}},
    ],
}


class TestReport(unittest.TestCase):
    def setUp(self):
        self.md = render_run_report(PAYLOAD, generated_at=0.0)

    def test_header_and_meta(self):
        self.assertIn("# Run report — SDLC — React Native (mobile)", self.md)
        self.assertIn("`run-abc`", self.md)
        self.assertIn("$0.42", self.md)
        self.assertIn("1500 tokens", self.md)

    def test_lineage_chain(self):
        self.assertIn("## Lineage", self.md)
        self.assertIn("FUNC-APP-onboarding", self.md)
        self.assertIn("→", self.md)

    def test_files_table_rendered(self):
        self.assertIn("files changed (1)", self.md)
        self.assertIn("| `src/Onboarding.tsx` |", self.md)
        self.assertIn("| 14 |", self.md)

    def test_gate_approval_rendered(self):
        self.assertIn("**approve** by lead-dev", self.md)

    def test_backend_shown(self):
        self.assertIn("backend `claude_code`", self.md)


if __name__ == "__main__":
    unittest.main()
