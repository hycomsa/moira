"""Render a run's audit to a Markdown report — the human-readable, git-committable
face of the defensible audit core.

Pure and zero-dep: takes the same dict `run_payload()` returns (run, pipeline,
events, audit, cost) and produces Markdown. Committed into the AI SDLC repo under
`.moira-runs/<run-id>/report.md` (see git_sink.write_report).
"""
from __future__ import annotations

import time
from typing import Any


def _fmt_ts(epoch: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))
    except Exception:  # noqa: BLE001
        return "—"


def _files_table(files: list[dict[str, Any]]) -> list[str]:
    rows = ["", "| File | Δ | + | − |", "|---|---|---|---|"]
    for f in files:
        add = "" if f.get("additions") is None else f["additions"]
        dele = "" if f.get("deletions") is None else f["deletions"]
        rows.append(f"| `{f.get('path','?')}` | {f.get('status','M')} | {add} | {dele} |")
    return rows


def render_run_report(payload: dict[str, Any], generated_at: float | None = None) -> str:
    run = payload.get("run") or {}
    pipeline = payload.get("pipeline") or {}
    audit = payload.get("audit") or []
    cost = payload.get("cost") or {}
    gen = generated_at if generated_at is not None else time.time()

    L: list[str] = []
    L.append(f"# Run report — {pipeline.get('name', run.get('pipeline_id', 'run'))}")
    L.append("")
    L.append(f"> **Run:** `{run.get('run_id', '?')}`  ")
    L.append(f"> **Status:** {run.get('status', '?')}  ")
    L.append(f"> **Owner:** {run.get('owner', '?')}  ")
    L.append(f"> **Pipeline:** {pipeline.get('id', run.get('pipeline_id', '?'))}  ")
    L.append(f"> **Generated:** {_fmt_ts(gen)}  ")
    L.append(f"> **Cost:** ${cost.get('usd', 0)} · "
             f"{cost.get('tokens_in', 0) + cost.get('tokens_out', 0)} tokens  ")
    # duration + leading model rolled up from the audit
    dur = sum(a.get("duration", 0) or 0 for a in audit)
    import collections as _c
    labels: _c.Counter = _c.Counter()
    for a in audit:
        inp = a.get("input") or {}
        lbl = inp.get("model") if (inp.get("model") and inp.get("model") != "(default)") else inp.get("backend")
        if lbl:
            labels[lbl] += 1
    lead = labels.most_common(1)[0][0] if labels else "—"
    L.append(f"> **Time:** {dur:.1f}s · **Leading model:** {lead}  ")
    # tamper-evidence: verify the audit hash-chain
    from .integrity import verify_chain
    chain = verify_chain(audit)
    if not chain.get("sealed", True):
        L.append(f"> **Audit chain:** unsealed (legacy · {chain['length']} records)")
    elif chain["ok"]:
        L.append(f"> **Audit chain:** ✓ verified ({chain['length']} sealed records · "
                 f"head `{(chain['head'] or '')[:12]}`)")
    else:
        L.append(f"> **Audit chain:** ✗ BROKEN at record {chain['broken_at']}")
    L.append("")

    # lineage (from the first audit record that carries it)
    lineage = next((a.get("lineage") for a in audit if a.get("lineage")), [])
    if lineage:
        L.append("## Lineage")
        L.append("")
        L.append(" → ".join(f"`{x}`" for x in lineage))
        L.append("")

    L.append("## Steps")
    L.append("")
    for a in audit:
        inp = a.get("input") or {}
        out = a.get("output") or {}
        L.append(f"### {a.get('node_name', a.get('node_id', '?'))} — {a.get('status', '?')}")
        meta = []
        if inp.get("backend"):
            meta.append(f"backend `{inp['backend']}`")
        if inp.get("model") and inp["model"] != "(default)":
            meta.append(f"model `{inp['model']}`")
        c = a.get("cost") or {}
        meta.append(f"${c.get('usd', 0)} · {a.get('duration', 0):.1f}s")
        L.append(f"> {' · '.join(meta)}")
        L.append("")
        for d in a.get("decisions") or []:
            L.append(f"- {d}")
        if a.get("tools"):
            L.append(f"- _tools:_ {', '.join(a['tools'])}")
        for ap in a.get("approvals") or []:
            who = ap.get("by", "?")
            L.append(f"- **{ap.get('decision', '?')}** by {who} — {ap.get('confirmed', '')}"
                     + (f" _(feedback: {ap['feedback']})_" if ap.get("feedback") else ""))
        if isinstance(out.get("files"), list) and out["files"]:
            L.append(f"- **files changed ({len(out['files'])})**"
                     + (" _(diff truncated)_" if out.get("truncated") else ""))
            L.extend(_files_table(out["files"]))
            # the unified diff itself, fenced as ```diff so Markdown viewers color it
            patch = out.get("patch")
            if isinstance(patch, str) and patch.strip():
                L.append("")
                L.append("<details><summary>diff</summary>")
                L.append("")
                L.append("```diff")
                L.append(patch.rstrip("\n"))
                L.append("```")
                L.append("")
                L.append("</details>")
        if isinstance(out.get("summary"), str):
            L.append(f"- _output:_ {out['summary']}")
        elif "passed" in out:
            L.append(f"- _check:_ {'passed' if out['passed'] else 'FAILED'}"
                     + (f" — `{out.get('cmd', '')}`" if out.get("cmd") else ""))
        L.append("")

    L.append("---")
    L.append("_Generated by Moira — governed orchestration · the audit record is the source of truth._")
    return "\n".join(L) + "\n"
