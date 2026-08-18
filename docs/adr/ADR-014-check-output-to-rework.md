# ADR-014: Closed test-fix loop — failing check output feeds the rework prompt

- **Status**: Accepted (implemented)
- **Date**: 2026-08-18
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-009 (findings digest — the judgment channel), ADR-010 (loop
  cap — the safety this loop requires), ADR-011 (retry errors — the mechanical
  channel), `m-c-research/` ST1

## Context

After ADR-009 a rejected producer received a *digest* of the findings that
blocked it — severity, title, a truncated detail. For a failing `test_exec`
that means "tests FAILED — 2 failed, 10 passed" plus an 800-char tail: enough
to know *that* it failed, rarely enough to know *what to fix*. The research
(`m-c-research/`, files 11/15) identified the missing piece of the
"generate → test → fix until green or escalate" cycle: the raw failing-test
output must reach the producer's next attempt, the way Cezar's shell checks
feed their output into the retry prompt (its `CHECK_OUTPUT_CAP = 20 000`
constant is adopted here as a field-tested size).

This lands deliberately *after* QW1–QW3: a loop fed with evidence is a cost
loop, and its safety rails (the ADR-010 iteration cap above all) had to exist
first.

## Decision

### 1. Opt-in per gate: `GateConfig.rework_check_output: bool = False`

Default behavior is unchanged — including `escalate_on_blocking=True`, which
remains the deliberate product default (a blocking finding pauses for a
human). A team that wants the autonomous test-fix cycle configures a gate with
`escalate_on_blocking: false`, a `max_loop` budget, and
`rework_check_output: true` — three explicit, git-versioned decisions.
Editable in the cockpit gate inspector.

### 2. A third, distinct prompt channel

`contract.check_output_block()` renders the evidence as
`=== FAILING CHECK OUTPUT (make these pass) ===`. The three rework/retry
channels now have disjoint semantics, and blurring them would teach the model
to misread one as another:

| Channel | Section | Meaning |
|---|---|---|
| ADR-009 | `REVIEWER FEEDBACK` | judgment digest about produced work |
| ADR-011 | `PREVIOUS ATTEMPT FAILED` | mechanical errors of attempts that produced nothing |
| ADR-014 | `FAILING CHECK OUTPUT` | raw ground truth from a real command the work failed |

The **tail** is kept on truncation (`CHECK_OUTPUT_CAP = 20 000`, shared
constant imported by the engine) — test runners print failures at the end.

### 3. Evidence is derived from the audit trail

Checks now persist their output tail into the audit record
(`output.check_output` on `test_exec` and `shell`; `ac_coverage` and
`log_hygiene` already carry structured detail). On reject, the engine's
`_failing_check_output()` reads the **latest audit record of each consumed
check** and collects the failing ones — the same audit-derived pattern as
ADR-010's counter, and for the same reason: it works identically inside one
drive and across `resume()`/restart/worker handoff (verified by test with a
fresh context after resume). The in-memory `verifier_results` dict would be
empty on the resume path.

### 4. Both reject paths, one delivery point

`_deliver_rework_context()` is the single place both the auto-gate reject (in
`_drive`) and the human reject (in `resume`) use: QW1's digest always, the raw
evidence when the flag is on. Human rejects benefit too — the reviewer's note
and the failing output compose. The channel (`context["check_output"][target]`)
is written only by the single-threaded drive/resume paths (same safety
argument as the `feedback` channel, ADR-011 §2).

### 5. Audit fidelity

When a rework prompt carried check output, the producer's audit record input
gains `check_output` — the sealed trail describes everything the model was
shown (the invariant established in ADR-011 and required by the research:
"pełny ślad iteracji w audycie jest warunkiem, nie opcją").

## Consequences

### Positive

- The full autonomous cycle now exists — generate → real command verdict →
  informed rework → green, or forced human escalation after `max_loop` — and
  it is Moira-shaped: gated, capped, audited, opt-in.
- Zero new API surface; one boolean on GateConfig, one prompt section, one
  audit input field.

### Negative / trade-offs

- Up to ~20 kB more prompt per rework iteration — the flag is opt-in precisely
  because this multiplies loop cost; pairs naturally with the ADR-010 cap and,
  later, a server-enforced cost budget (research ST4).
- Check output is uncontrolled text entering the prompt (test names, code
  fragments); it also already enters audit records and events, so no new data
  class — but the untrusted-data framing (research QW9) grows more relevant
  with this change.
- Only the latest record per consumed check is read; a gate consuming the same
  check id twice in exotic DAGs would see one result — acceptable (node ids
  are unique per pipeline).

## Alternatives Considered

- **Fold the raw output into the ADR-009 feedback digest** — rejected: the
  digest exists to stay readable; 20 kB of test output inside it would bury
  the judgment and blur channel semantics.
- **Read from in-memory `verifier_results`** — rejected: empty across
  `resume()`; the audit trail is the only source that works on every path.
- **Always-on (no flag)** — rejected: multiplies cost on every gated pipeline
  and changes existing runs' prompts silently; opt-in keeps it a visible,
  versioned pipeline decision.
- **Head truncation** — rejected: pytest/jest print the failure summary at the
  end; keeping the head keeps the noise.

## References

- `orchestrator/moira_core/backends/contract.py` — `check_output_block`, `CHECK_OUTPUT_CAP`
- `orchestrator/moira_core/engine.py` — `_failing_check_output`,
  `_deliver_rework_context`, check output persistence, audit input
- `orchestrator/moira_core/backends/claude_code.py`, `litellm_backend.py` — prompt wiring
- `cockpit/src/pages/PipelinesPage.tsx` — gate checkbox
- `orchestrator/tests/test_check_output_rework.py` — 10 tests (block rendering,
  system/human reject delivery incl. fresh-context resume, opt-in default,
  audit fidelity, check persistence, prompt wiring)
- `m-c-research/23-rekomendacje-state-of-the-art.md` — ST1
