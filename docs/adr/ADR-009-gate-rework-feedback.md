# ADR-009: System-generated rework feedback on gate reject

- **Status**: Accepted (implemented)
- **Date**: 2026-08-17
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-006 (engine as v0.2 run driver), `m-c-research/` QW1 (Moira vs Cezar research)

## Context

The engine's rework loop (`on_reject_goto`: reject at a gate resets the target
node and its downstream subtree, then re-drives) had an information gap: when a
**system** decided the reject — an `auto` gate with `escalate_on_blocking=False`
hitting a blocking finding, or a `hybrid` gate auto-denying on
`min_conf < low_cutoff` — `GateDecision.feedback` was left empty. The producer
re-ran blind: it received no signal about *what* to fix, so an automatic
reject→rework iteration burned tokens without converging.

Human rejects never had this problem: the reviewer types feedback in the Inbox
and `Engine.resume()` delivers it via `context["feedback"][target]`, which every
model backend already renders as a `=== REVIEWER FEEDBACK (address this) ===`
prompt section (`backends/contract.py`) and the audit record captures as
`input.feedback`.

The comparative research against Cezar (`m-c-research/`, files 10/11/14/15)
identified this as Moira's single highest value-to-cost fix (QW1): Cezar's
otherwise primitive retry loop is *closed* (failing check output reaches the
next prompt), while Moira's formally stronger gate/rework mechanism was *blind*.

## Decision

On a system reject, the gate serializes the findings that caused the decision
into `GateDecision.feedback`, reusing the existing delivery channel end-to-end
(engine context → backend prompt → audit record). Implemented as
`gates.findings_feedback(verifier_results)`:

1. **Deterministic findings first.** Results carrying `output["check"]`
   (`test_exec`, `ac_coverage`, `log_hygiene`, `shell`) are listed before LLM
   verifier findings — command output is ground truth, LLM findings are
   advisory. (`engine._run_shell_check` now stamps `check: shell` on executed
   commands too, so the marker is uniform across all four built-in checks.)
2. **Only defects.** `INFO` findings are passes and are skipped.
3. **Digest, not transcript.** Sorted by severity (desc) then confidence (asc),
   capped at `FEEDBACK_MAX_FINDINGS = 5` items with a `(+N more)` remainder
   line; each finding's detail truncated at `FEEDBACK_DETAIL_CAP = 500` chars.
4. **Human paths untouched.** Approve and escalate decisions carry no generated
   feedback; a human reject keeps the reviewer's own text (never overwritten).

## Consequences

### Positive

- The automatic reject→rework loop is informationally closed: the producer's
  next attempt sees exactly which findings blocked it, with deterministic
  evidence (e.g. failing test output) ranked above LLM opinions.
- Zero new schema or API surface: the feedback rides the existing
  `GateDecision.feedback` field, prompt section, and audit `input.feedback` —
  so it is automatically part of the sealed audit record.
- Backend-agnostic: claude_code, litellm and mock all consume
  `context["feedback"]` already.

### Negative / not addressed here

- ~~The rework loop is still **unbounded**~~ — resolved by **ADR-010**
  (`GateConfig.max_loop`, default 3, audit-derived counter): feedback and the
  cap now land together, as this ADR required.
- Backend *retry* (`max_retries` on transient failure) remains blind — that is
  research QW3, a separate change.
- `auto_check` stdout/stderr reaches feedback only through the check's Finding
  `detail` (tail-capped); full check-output injection is research ST1.

### Risks

- LLM findings can be vague; the deterministic-first ordering mitigates this by
  putting reproducible evidence at the top of the digest.

## Alternatives Considered

- **Inject raw verifier outputs into the producer prompt** — rejected for QW1:
  unbounded prompt growth and duplication of what `upstream` already carries;
  the digest keeps the loop cheap. Full check-output injection is deferred to
  ST1 with its own opt-in and audit trail.
- **Prefill feedback on escalate too** — rejected: the Inbox already renders
  the findings to the human, and prefilled text would blur whether the recorded
  feedback is the reviewer's judgment or a machine digest.

## References

- `orchestrator/moira_core/gates.py` — `findings_feedback()` + both reject paths
- `orchestrator/moira_core/backends/contract.py` — prompt delivery section
- `orchestrator/tests/test_gate_feedback.py` — unit + integration coverage
- `m-c-research/23-rekomendacje-state-of-the-art.md` — QW1 and its follow-ups
  (QW2 cap, QW3 retry context, ST1 check-output injection)
