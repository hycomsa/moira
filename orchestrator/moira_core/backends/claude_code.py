"""Claude Code CLI backend (ADR-004 + Cezar runner model).

Delegates a node's execution to the `claude` CLI running under the user's OWN
login (no per-seat API billing). This is the real coding/reasoning backend.

It is GUARDED: importing/using this module never requires credentials. `run()`
shells out to `claude` in headless mode and parses the result. If the CLI is
absent or fails, it returns an error BackendResult (the engine then applies the
retry-then-gate policy) — it never crashes the orchestrator.

NOTE: this is wired but not exercised in the offline test suite; the spike's
thesis (governed orchestration + gates + audit) is proven with MockBackend.
Run with a real `claude` CLI present to exercise true delegation.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from ..models import BackendResult, Cost, Node
from . import contract


class ClaudeCodeBackend:
    name = "claude_code"

    # Output contract (shared across backends — see contract.py)
    START = contract.START
    END = contract.END
    SYSTEM = contract.SYSTEM

    # roles that should reason over provided context, not explore/modify the repo
    REASONING_ROLES = {"requirements-analyst", "solution-architect",
                       "code-quality", "security", "evaluator"}
    # roles that judge an evaluation TARGET and emit a scorecard (see evals.py)
    EVAL_ROLES = {"evaluator", "spec-conformance-verifier",
                  "compliance-verifier", "compliance-reviewer"}
    # judging roles that READ code/specs but must never modify (read-only exploration)
    READONLY_ROLES = {"spec-conformance-verifier", "compliance-verifier", "compliance-reviewer"}

    def __init__(self, binary: str = "claude", timeout: int = 600,
                 permission_mode: str = "acceptEdits", max_turns: int = 12) -> None:
        self.binary = binary
        self.timeout = timeout
        self.permission_mode = permission_mode
        self.max_turns = max_turns

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _build_prompt(self, node: Node, context: dict[str, Any]) -> str:
        if (node.role in self.EVAL_ROLES) or context.get("eval_kind"):
            # LLM-as-judge: build a scorecard prompt; the contract still wraps the
            # response so the scorecard lands in `output` (see evals.build_eval_prompt).
            from ..evals import build_eval_prompt
            return build_eval_prompt(
                context.get("eval_kind", "quality"),
                context.get("eval_target", "") or context.get("spec_text", ""),
                context.get("eval_criteria"),
            )
        if node.skill:
            # Discovery/BA: invoke an AI SDLC framework skill (slash-command), with
            # the user's elaboration appended. The skill authors artifacts in cwd.
            # auto-chain: an empty skill_input inherits the prior step's artifact id
            inp = node.skill_input or context.get("produced_artifact", "") or node.spec_ref or context.get("func_id", "")
            line = f"/{node.skill} {inp}".strip()
            parts = [p for p in [(node.prompt_extra or "").strip(),
                                 (context.get("feedback", {}).get(node.id, "") or "").strip()] if p]
            return line + ("\n\n" + "\n\n".join(parts) if parts else "")
        return contract.build_stage_prompt(
            role=node.role or node.id, spec_ref=node.spec_ref,
            spec_text=context.get("spec_text", ""),
            upstream=context.get("upstream", {}),
            feedback=context.get("feedback", {}).get(node.id, ""),
        )

    def run(self, node: Node, context: dict[str, Any]) -> BackendResult:
        if not self.available():
            return BackendResult(ok=False,
                                 error=f"claude CLI '{self.binary}' not found on PATH")
        role = node.role or node.id
        is_skill = bool(node.skill)
        cmd = [
            self.binary, "-p", self._build_prompt(node, context),
            "--output-format", "json",
            "--permission-mode", self.permission_mode,
            "--max-turns", str(self.max_turns),
        ]
        # skill runs let the framework skill behave normally (it writes artifacts);
        # non-skill stage runs use the JSON output contract.
        if not is_skill:
            cmd += ["--append-system-prompt", self.SYSTEM]
        # per-node model override (enables cross-model verification / strong-model planning)
        if node.model and node.model not in ("", "mock"):
            cmd += ["--model", node.model]
        # reasoning roles run tool-light (no edits) so they stay focused + cheap;
        # skill runs MUST keep tools (the skill writes files into the repo)
        if role in self.REASONING_ROLES and not is_skill:
            cmd += ["--disallowed-tools", "Edit", "Write", "Bash"]
        # conformance / compliance judges READ the code (Read/Grep/Glob/Bash) but must
        # never modify it — keep exploration tools, forbid edits.
        elif role in self.READONLY_ROLES and not is_skill:
            cmd += ["--disallowed-tools", "Edit", "Write"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                cwd=context.get("cwd"),
            )
        except subprocess.TimeoutExpired:
            return BackendResult(ok=False, error="claude CLI timed out")
        except Exception as e:  # noqa: BLE001
            return BackendResult(ok=False, error=f"claude CLI error: {e}")

        if proc.returncode != 0:
            return BackendResult(ok=False, error=proc.stderr.strip()[:500] or "non-zero exit")

        return self._parse(proc.stdout)

    def _parse(self, stdout: str) -> BackendResult:
        # 1) unwrap the claude --output-format json envelope (result + usage + cost)
        text = stdout.strip()
        result_text = text
        cost = Cost()
        try:
            envelope = json.loads(text)
            if isinstance(envelope, dict):
                result_text = envelope.get("result", text)
                usage = envelope.get("usage", {}) or {}
                cost = Cost(
                    tokens_in=usage.get("input_tokens", 0),
                    tokens_out=usage.get("output_tokens", 0),
                    usd=envelope.get("total_cost_usd", 0.0) or 0.0,
                )
        except json.JSONDecodeError:
            pass

        payload = self._extract_contract(result_text)
        out = payload.get("output", payload.get("raw", {}))
        if not isinstance(out, dict):  # raw text (e.g. skill runs) -> wrap
            out = {"result": str(out).strip()[:2000]}
        elif not out:
            out = {"result": result_text.strip()[:2000]}
        return BackendResult(
            output=out,
            tools_used=payload.get("tools_used") or ["claude_code_cli"],
            decisions=payload.get("decisions", []),
            cost=cost,
            ok=True,
        )

    def _extract_contract(self, text: str) -> dict[str, Any]:
        # delegates to the shared contract parser (kept as a method for test compat)
        return contract.extract_contract(text)
