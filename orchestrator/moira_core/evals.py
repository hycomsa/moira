"""LLM-as-judge evaluation: judge prompts + scorecard normalization.

An evaluation in Moira is a *tiny one-node run* — an `evaluator` node judges a
TARGET against CRITERIA and returns a scorecard. Because it's a normal run it
flows through the existing engine → audit (hash-chained) → persistence → report
→ traceability → run_metrics, with zero new persistence.

This module is pure and dependency-free: it builds the judge prompt and
normalizes whatever JSON the model returns into a stable scorecard:

    {
      "kind":     "quality" | "conformance",
      "criteria": [{"name": str, "score": 0..1, "verdict": "pass|warn|fail", "note": str}],
      "overall":  0..1,
      "missing":  [str],     # gaps / uncovered acceptance criteria
      "summary":  str,
      "parsed":   bool,      # False when the model didn't return a usable scorecard
    }
"""
from __future__ import annotations

from typing import Any, Optional

QUALITY_CRITERIA = ["completeness", "clarity", "consistency", "testability", "traceability"]
CONFORMANCE_CRITERIA = ["acceptance-criteria coverage", "correctness", "no drift from spec",
                        "edge cases handled", "standards adherence"]
COMPLIANCE_CRITERIA = ["pokrycie wymogów regulacji", "brak naruszeń", "kompletność kontroli",
                       "udokumentowanie podstaw / decyzji"]

# severity ladder for compliance findings (matches the compliance-check skill)
SEVERITIES = ["BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO"]


def default_criteria(kind: str) -> list[str]:
    if kind == "conformance":
        return CONFORMANCE_CRITERIA
    if kind == "compliance":
        return COMPLIANCE_CRITERIA
    return QUALITY_CRITERIA


def verdict_for(score: float) -> str:
    if score >= 0.8:
        return "pass"
    if score >= 0.5:
        return "warn"
    return "fail"


def _clamp01(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return 0.0 if f < 0 else 1.0 if f > 1 else round(f, 3)


def build_eval_prompt(kind: str, target_text: str, criteria: Optional[list[str]] = None) -> str:
    """Build the judge prompt. The backend wraps the response in the shared output
    contract, so we instruct the model to make its `output` object BE the scorecard."""
    crit = criteria or default_criteria(kind)
    crit_lines = "\n".join(f"  - {c}" for c in crit)
    shape = (
        "Your `output` object MUST be the scorecard with EXACTLY these keys:\n"
        '  - "criteria": array of {"name": string, "score": number 0..1, '
        '"verdict": "pass"|"warn"|"fail", "note": string (one line)}\n'
        '  - "overall": number 0..1 (holistic, not just the average)\n'
        '  - "missing": array of strings — concrete gaps (for conformance: uncovered acceptance criteria)\n'
        '  - "summary": string — 1-2 sentences for a reviewer\n'
        "Score honestly; reserve scores above 0.9 for genuinely excellent work."
    )
    if kind == "conformance":
        intro = (
            "You are a strict spec-conformance verifier. Inspect the CODE in your working "
            "directory and judge how well it implements the FUNCTIONAL SPEC below. For each "
            "acceptance criterion, decide if it is covered by the code; list any that are "
            "missing or drifted. Read the relevant source files before judging."
        )
        body = f"=== FUNCTIONAL SPEC (judge the code against this) ===\n{target_text}"
    elif kind == "compliance":
        intro = (
            "Jesteś audytorem compliance (postawa audytora zewnętrznego, nie kolegi-developera). "
            "Zbadaj KOD w katalogu roboczym pod kątem REGULACJI poniżej. Uruchom jej sekcję "
            "Checklist, prześledź przepływ danych, nie zgaduj — weryfikuj w kodzie (czytaj pliki). "
            "Nie wymyślaj numerów artykułów; nie udawaj prawnika — granice eskaluj do człowieka."
        )
        body = f"=== REGULACJA / STANDARD (oceniaj kod względem tego) ===\n{target_text}"
        shape = (
            "Twój obiekt `output` MUSI być kartą wyników z DOKŁADNIE tymi kluczami:\n"
            '  - "criteria": tablica {"name": string (obszar regulacji), "score": number 0..1, '
            '"verdict": "pass"|"warn"|"fail", "note": string}\n'
            '  - "findings": tablica {"severity": "BLOCKER"|"HIGH"|"MEDIUM"|"LOW"|"INFO", '
            '"title": string, "regulation": string (parafraza artykułu/wymogu jednym zdaniem), '
            '"location": string (plik:linia), "recommendation": string (1-3 zdania)}\n'
            '  - "overall": number 0..1 (holistycznie; obecność BLOCKER/HIGH znacząco obniża)\n'
            '  - "missing": tablica string — niepokryte wymogi\n'
            '  - "summary": string — 1-2 zdania dla reviewera (czy można mergować)\n'
            "Severity dobieraj konserwatywnie po stronie wyższej. Każde znalezisko MUSI mieć "
            "regulację i lokalizację."
        )
    else:
        intro = (
            "You are a senior reviewer performing a quality evaluation. Judge the ARTIFACT "
            "below against the criteria. Be specific and evidence-based; cite what is weak."
        )
        body = f"=== ARTIFACT UNDER REVIEW ===\n{target_text}"
    return (
        f"{intro}\n\n"
        f"=== CRITERIA ===\n{crit_lines}\n\n"
        f"{body}\n\n"
        f"=== OUTPUT ===\n{shape}"
    )


def normalize_scorecard(output: Any, kind: str = "quality") -> dict[str, Any]:
    """Coerce a model `output` object into the stable scorecard shape.

    Robust to: missing keys, scores out of range or as strings, missing verdicts
    (derived from score), and a non-scorecard output (e.g. the backend wrapped raw
    text as {"result": ...}) → returns parsed=False with whatever summary we can find.
    """
    if not isinstance(output, dict):
        return {"kind": kind, "criteria": [], "overall": 0.0, "missing": [],
                "findings": [], "summary": str(output)[:500], "parsed": False}

    raw_crit = output.get("criteria")
    criteria: list[dict[str, Any]] = []
    if isinstance(raw_crit, list):
        for c in raw_crit:
            if not isinstance(c, dict):
                continue
            score = _clamp01(c.get("score"), 0.0)
            verdict = c.get("verdict")
            if verdict not in ("pass", "warn", "fail"):
                verdict = verdict_for(score)
            criteria.append({
                "name": str(c.get("name", "criterion"))[:80],
                "score": score,
                "verdict": verdict,
                "note": str(c.get("note", ""))[:300],
            })

    # compliance findings (severity-mapped); empty for quality/conformance
    findings: list[dict[str, Any]] = []
    raw_find = output.get("findings")
    if isinstance(raw_find, list):
        for f in raw_find:
            if not isinstance(f, dict):
                continue
            sev = str(f.get("severity", "INFO")).upper()
            if sev not in SEVERITIES:
                sev = "INFO"
            findings.append({
                "severity": sev,
                "title": str(f.get("title", ""))[:200],
                "regulation": str(f.get("regulation", ""))[:200],
                "location": str(f.get("location", ""))[:160],
                "recommendation": str(f.get("recommendation", ""))[:400],
            })

    # overall: trust the model if it gave a number, else mean of criterion scores
    if isinstance(output.get("overall"), (int, float)):
        overall = _clamp01(output["overall"])
    elif criteria:
        overall = round(sum(c["score"] for c in criteria) / len(criteria), 3)
    else:
        overall = 0.0
    # a serious finding caps the headline score (BLOCKER → can't pass; HIGH → at most warn)
    if any(f["severity"] == "BLOCKER" for f in findings):
        overall = min(overall, 0.2)
    elif any(f["severity"] == "HIGH" for f in findings):
        overall = min(overall, 0.5)

    missing = output.get("missing")
    missing = [str(m)[:200] for m in missing] if isinstance(missing, list) else []

    summary = output.get("summary")
    if not isinstance(summary, str):
        summary = output.get("result") if isinstance(output.get("result"), str) else ""

    # parsed = we got at least one criterion, a finding, or an explicit overall
    parsed = bool(criteria) or bool(findings) or isinstance(output.get("overall"), (int, float))
    return {"kind": kind, "criteria": criteria, "overall": overall, "missing": missing,
            "findings": findings, "summary": summary[:500], "parsed": parsed}
