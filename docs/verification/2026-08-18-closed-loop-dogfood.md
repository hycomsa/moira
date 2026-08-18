# Dogfood: the closed test-fix loop on the real claude CLI (2026-08-18)

First real-world validation of the ADR-009/010/011/014 quality loop — real
`claude` CLI (logged-in user account), real money, real test runs; measured
from the product's own audit trail. Harness:
[`orchestrator/verify_real_testfix_loop.py`](../../orchestrator/verify_real_testfix_loop.py)
(self-contained: generates a throwaway git code repo, runs the engine
directly, prints JSON metrics; no existing repo is touched).

## Setup

Pipeline `implement → test_exec → AUTO gate` with `escalate_on_blocking=false`,
`max_loop=3`, `rework_check_output=true`, `on_reject_goto=implement`. The task:
implement `basket_total()` from a spec; the acceptance tests are QA-owned and
the spec **forbids reading or modifying `tests/`**. Two scenarios:

- **A — oneshot**: the full contract is derivable from the spec (baseline).
- **B — rework**: two QA decisions are deliberately absent from the spec and
  ungues­sable (`LookupError("no items")` for an empty basket; zero quantity →
  `ValueError("invalid quantity")`) — forcing honest first-attempt failures so
  the loop itself is what gets measured.

## Results

| Metric | A (oneshot) | B (rework) |
|---|---|---|
| Run status | succeeded, **unattended** | succeeded, **unattended** |
| Implement iterations | 1 | 3 (fail → fail → green) |
| System rejects (of `max_loop=3`) | 0 | 2 |
| Rework prompts carried `FAILING CHECK OUTPUT` | n/a | 2/2 |
| Wall time | 48.3 s | 130.9 s |
| Cost | $0.48 | $1.50 |
| Audit chain | ok, sealed (3 records) | ok, sealed (9 records) |
| Tests green (verified outside Moira) | yes | yes |

**Iteration trace of B** (from the sealed audit): attempt 1 implemented the
guessable contract and missed both hidden rules (`ValueError("empty basket")`,
no zero-quantity check). The gate rejected; the rework prompt carried the raw
unittest output. Attempt 2 fixed the zero-quantity rule and the exception
*type*, but kept the old message — the next check output showed exactly
`'empty basket' != 'no items'`. Attempt 3 converged. Every step, reject and
approval is in the hash-chained audit; the chain verifies.

## Findings

1. **The loop works in reality, not just in tests.** Evidence-driven,
   incremental convergence within the `max_loop` budget, fully unattended,
   with the whole trail sealed. This was the kill-test question — answered.
2. **The agent honored the governance constraint.** The live stream shows it
   explicitly declining to read `tests/` per the spec — no prompt-injection or
   constraint-violation observed in either run. (One data point, not proof;
   the tools-policy hardening arguments stand.)
3. **Scenario design matters.** The first version of scenario A leaked its
   "hidden" details in the spec wording and the agent legitimately one-shotted
   — a useful reminder that measuring the loop requires constraints that are
   genuinely absent from the prompt, and that a capable model makes the happy
   path cheap ($0.48, 48 s).
4. **Cosmetic**: the QW1 findings digest for `test_exec` reads
   "tests FAILED — tests FAILED\n…" (title duplicates the summary head). The
   raw `check_output` carried the real signal. Worth a small polish, not a fix.
5. **Observed**: the agent's attempt to run a sanity check itself was refused
   by the CLI sandbox (fine — the spec forbade running tests; the deterministic
   check is the engine's job, and that separation held).

## Costs & scaling note

A 3-iteration loop on a trivial task cost $1.50. Real FUNC-sized tasks will
multiply this — reinforcing the research's sequencing: the server-enforced
cost budget (ST4) is the right next guardrail before autonomous loops are used
routinely.

> Reproduce: `cd orchestrator && python3 verify_real_testfix_loop.py
> --scenario rework` (spends real tokens; requires a logged-in `claude` CLI).
