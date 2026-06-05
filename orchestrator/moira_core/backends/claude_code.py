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
import os
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
    # opt-in: roles that drive Claude Code "Superpowers" (loaded per-run via --plugin-dir from
    # $MOIRA_SUPERPOWERS_DIR) instead of the custom dev@* skills. Coexists — only these roles change.
    SUPERPOWERS_ROLES = {"superpowers-coder"}
    # heavy coding roles: plan→TDD→subagents need a bigger turn/time budget than the default
    HEAVY_ROLES = {"superpowers-coder", "code-generator", "coder",
                   "backend-developer", "frontend-developer"}
    # appended to the system prompt for heavy/superpowers roles so interactive skills
    # (brainstorm/plan-approval) don't stall a headless run
    AUTONOMY = ("Work autonomously end-to-end. If a planning, brainstorming, or review skill would "
                "normally pause to ask the user, make reasonable assumptions, state them briefly, and "
                "proceed — never stop and wait for confirmation. Finish by leaving the working tree edited.")

    def __init__(self, binary: str = "claude", timeout: int = 600,
                 permission_mode: str = "acceptEdits", max_turns: int = 12,
                 heavy_timeout: int = 1800, heavy_max_turns: int = 40) -> None:
        self.binary = binary
        self.timeout = timeout
        self.permission_mode = permission_mode
        self.max_turns = max_turns
        self.heavy_timeout = heavy_timeout
        self.heavy_max_turns = heavy_max_turns

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

    def _build_cmd(self, node: Node, context: dict[str, Any]) -> list[str]:
        """Assemble the `claude` argv (pure — no I/O, so it's unit-testable)."""
        role = node.role or node.id
        is_skill = bool(node.skill)
        heavy = role in self.HEAVY_ROLES
        max_turns = self.heavy_max_turns if heavy else self.max_turns
        cmd = [
            self.binary, "-p", self._build_prompt(node, context),
            # realtime NDJSON so we can stream reasoning/tools/tokens live (stream-json
            # requires --verbose); the final `result` event is parsed like the old json mode.
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", self.permission_mode,
            "--max-turns", str(max_turns),
        ]
        # opt-in: load Superpowers for this session only (true coexistence with dev@* — other
        # runs are untouched). Enabled when the role opts in AND the env var points at the plugin.
        sp_dir = os.environ.get("MOIRA_SUPERPOWERS_DIR")
        if role in self.SUPERPOWERS_ROLES and sp_dir:
            cmd += ["--plugin-dir", sp_dir]
        # skill runs let the framework skill behave normally (it writes artifacts);
        # non-skill stage runs use the JSON output contract (+ an autonomy nudge for heavy roles).
        if not is_skill:
            system = self.SYSTEM + ("\n\n" + self.AUTONOMY if heavy else "")
            cmd += ["--append-system-prompt", system]
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
        return cmd

    @staticmethod
    def _reduce_stream(lines, on_record=None):
        """Consume the claude stream-json NDJSON, coalescing per assistant turn:
        emit a live record for each text block / tool_use (via on_record(rec, tin, tout))
        and return (final_result_event, tokens_in, tokens_out). Pure + testable."""
        final = None
        tin = tout = 0
        for raw in lines:
            raw = (raw or "").strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            et = ev.get("type")
            if et == "assistant":
                msg = ev.get("message", {}) or {}
                u = msg.get("usage", {}) or {}
                if u.get("input_tokens"):
                    tin = u["input_tokens"]
                tout += u.get("output_tokens", 0) or 0
                for b in msg.get("content", []) or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and (b.get("text") or "").strip():
                        rec = {"kind": "assistant", "text": b["text"][:4000]}
                    elif b.get("type") == "tool_use":
                        inp = json.dumps(b.get("input", {}), ensure_ascii=False)[:160]
                        rec = {"kind": "tool", "text": f"{b.get('name', 'tool')}  {inp}"}
                    else:
                        continue
                    if on_record:
                        on_record(rec, tin, tout)
            elif et == "result":
                final = ev
                u = ev.get("usage", {}) or {}
                if u.get("input_tokens"):
                    tin = u["input_tokens"]
                if u.get("output_tokens"):
                    tout = u["output_tokens"]
                if on_record:
                    on_record({"kind": "result", "text": str(ev.get("result", ""))[:500]}, tin, tout)
        return final, tin, tout

    def run(self, node: Node, context: dict[str, Any]) -> BackendResult:
        if not self.available():
            return BackendResult(ok=False,
                                 error=f"claude CLI '{self.binary}' not found on PATH")
        cmd = self._build_cmd(node, context)
        role = node.role or node.id
        timeout = self.heavy_timeout if role in self.HEAVY_ROLES else self.timeout
        live_path = context.get("live_path")
        node_id = node.id

        def emit(rec: dict, tin: int, tout: int) -> None:
            if not live_path:
                return
            import time as _t
            line = {"t": round(_t.time(), 3), "node": node_id,
                    "tokens_in": tin, "tokens_out": tout, **rec}
            try:
                with open(live_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
            except OSError:
                pass

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, bufsize=1, cwd=context.get("cwd"))
        except Exception as e:  # noqa: BLE001
            return BackendResult(ok=False, error=f"claude CLI error: {e}")

        import threading as _threading
        timed_out = {"v": False}

        def _kill():
            timed_out["v"] = True
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        watchdog = _threading.Timer(timeout, _kill)
        watchdog.start()
        try:
            final, _, _ = self._reduce_stream(proc.stdout, on_record=emit)
            proc.wait()
        finally:
            watchdog.cancel()

        if timed_out["v"]:
            return BackendResult(ok=False, error="claude CLI timed out")
        if final is None:
            err = (proc.stderr.read() if proc.stderr else "").strip()[:500]
            return BackendResult(ok=False, error=err or f"non-zero exit ({proc.returncode})")
        return self._result_from_envelope(final)

    def _parse(self, stdout: str) -> BackendResult:
        """Parse a single json envelope string (kept for compatibility/tests)."""
        try:
            envelope = json.loads(stdout.strip())
        except json.JSONDecodeError:
            envelope = {"result": stdout.strip()}
        return self._result_from_envelope(envelope if isinstance(envelope, dict) else {"result": stdout})

    def _result_from_envelope(self, envelope: dict[str, Any]) -> BackendResult:
        # unwrap the claude result envelope (result + usage + cost)
        result_text = envelope.get("result", "") or ""
        usage = envelope.get("usage", {}) or {}
        cost = Cost(
            tokens_in=usage.get("input_tokens", 0) or 0,
            tokens_out=usage.get("output_tokens", 0) or 0,
            usd=envelope.get("total_cost_usd", 0.0) or 0.0,
        )
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
