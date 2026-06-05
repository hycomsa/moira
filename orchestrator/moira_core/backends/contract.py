"""Shared structured-output contract for model-calling backends.

Both ClaudeCodeBackend and LiteLLMBackend ask the model to end with a JSON object
between sentinel markers, and parse it robustly (markers → balanced-brace → raw).
Keeping this in one place means every backend produces identically-shaped output
for the audit record, regardless of provider (model-agnostic).
"""
from __future__ import annotations

import json
from typing import Any

START = "===MOIRA_JSON_START==="
END = "===MOIRA_JSON_END==="

SYSTEM = (
    "You are a stage agent in the Moira governed SDLC pipeline. Do the requested "
    "stage work concisely. CRITICAL OUTPUT CONTRACT: end your response with a single "
    f"JSON object between the EXACT markers {START} and {END}, containing keys: "
    "output (object), decisions (string[] — the choices you made), tools_used "
    "(string[]). Emit nothing after the end marker."
)


def build_stage_prompt(role: str, spec_ref: str, spec_text: str,
                       upstream: dict[str, Any], feedback: str = "") -> str:
    fb = f"\n=== REVIEWER FEEDBACK (address this) ===\n{feedback}\n" if feedback else ""
    return (
        f"Role: '{role}' agent. Spec reference: {spec_ref}\n\n"
        f"=== SPEC ===\n{spec_text}\n\n"
        f"=== UPSTREAM OUTPUTS ===\n{json.dumps(upstream, indent=2)[:4000]}\n"
        f"{fb}\n"
        f"Do the work for this stage, then emit the contracted JSON between the markers."
    )


def extract_contract(text: str) -> dict[str, Any]:
    """markers first, then last balanced-brace contract object, then raw fallback."""
    if START in text and END in text:
        chunk = text.split(START, 1)[1].split(END, 1)[0].strip()
        obj = _loads_lenient(chunk)
        if obj is not None:
            return obj
    obj = _last_balanced_json(text)
    if obj is not None:
        return obj
    return {"raw": text[:2000]}


def _loads_lenient(s: str) -> dict[str, Any] | None:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _last_balanced_json(text: str) -> dict[str, Any] | None:
    starts = [i for i, c in enumerate(text) if c == "{"]
    for start in reversed(starts):
        depth = 0
        for j in range(start, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:j + 1])
                        if isinstance(obj, dict) and ("output" in obj or "decisions" in obj):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break
    return None
