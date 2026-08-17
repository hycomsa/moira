# ADR-011: Informed backend retry — error context + linear backoff

- **Status**: Accepted (implemented)
- **Date**: 2026-08-17
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-009 (rework feedback), ADR-010 (bounded rework loop),
  ADR-006 (durable runner — job-level retries are a different layer),
  `m-c-research/` QW3 (Moira vs Cezar research)

## Context

`Engine._exec_node` retries a failed backend call up to `Node.max_retries`
times (retry-N-then-gate). Two defects, called out by the comparative research
(`m-c-research/`, files 10/12/15):

1. **Blind**: the retry prompt was byte-identical to the failed attempt's. A
   deterministic failure — a structured-output contract parse error, a bad
   flag, a missing file — failed every attempt the same way, burning the whole
   budget to learn nothing. (Reference: Cezar's `onFail.retry` injects the
   failure output into the retry prompt — its loop is primitive but *closed*.)
2. **Immediate**: attempts fired back-to-back, so transient failures (rate
   limits, network) got no air, and a hung-then-killed CLI was re-spawned
   instantly.

This completes the Sprint-1 trio: ADR-009 informed the *rework* loop
(gate-level), ADR-010 bounded it; this ADR informs and paces the *retry* loop
(node-level). They are different loops: rework re-runs work a verifier judged
inadequate; retry re-runs work that produced nothing at all.

## Decision

### 1. A dedicated prompt channel, separate from REVIEWER FEEDBACK

Previous errors reach the next attempt as
`=== PREVIOUS ATTEMPT FAILED (fix the cause, do not repeat it) ===`, rendered
by `contract.attempt_errors_block()`. A separate section — not reuse of the
`REVIEWER FEEDBACK` channel — because the semantics differ: feedback is a
quality judgment about produced work; this is a mechanical failure report about
attempts that produced none. Blending them would teach agents to treat crash
logs as review guidance.

The block shows the **last 3** errors (most recent matter most), numbered by
attempt, each capped at 500 chars, with an `(N earlier attempt(s) omitted)`
remainder — same digest philosophy as ADR-009.

### 2. Delivery via a per-attempt context copy

The engine passes `{**context, "attempt_errors": [...]}` to `backend.run()` on
retries only. Alternatives:

- **Mutate the shared context dict** (like `context["feedback"]`) — rejected:
  `_exec_node` runs in parallel worker threads; the shared dict is read
  concurrently by sibling nodes, and a key that appears/disappears mid-batch is
  a race. (`feedback` is written only by the single-threaded drive/resume
  paths, which is why it may live in the shared dict.) A shallow copy makes the
  channel visible to exactly one attempt of one node, verified by test.
- **Backend-internal retry** — rejected: retry policy (count, pacing, what the
  next attempt is told) is orchestration, and pushing it into every backend
  duplicates it per adapter; the engine owns the loop, backends stay one-shot.

All three prompt shapes get the block: `ClaudeCodeBackend._build_prompt`
appends it in the wrapper (covers stage/eval/skill uniformly);
`LiteLLMBackend` mirrors it. MockBackend ignores it by design (tests assert on
the raw channel).

### 3. Linear backoff, env-tunable

`sleep(min(30, MOIRA_RETRY_BACKOFF × failures_so_far))` before each retry,
never before the first attempt. Default base `2` s → 2 s, 4 s pacing under the
default `max_retries=2`. Linear (not exponential) because the attempt budget is
small (≤ a handful) — exponential curves earn their complexity only with long
retry chains. Env-configured (`MOIRA_RETRY_BACKOFF`, documented in
PERSISTENCE.md) so tests/CI can zero it and rate-limited setups can raise it.

### 4. Audit fidelity

The retry prompt now contains content the sealed audit must describe, so the
audit record's `input` gains `attempts` (actual count, not the configured max)
and `attempt_errors` (truncated) whenever retries happened. First-attempt
successes stay noise-free (no `attempt_errors` key). The pre-existing per-error
`retry` events are unchanged.

## Consequences

### Positive

- A retryable-but-deterministic failure now converges or fails fast with an
  informed trail instead of burning identical attempts; transient failures get
  breathing room.
- The sealed audit fully reconstructs what each attempt was shown.
- No API/schema surface: a prompt section, a context key, two audit input
  fields.

### Negative / trade-offs

- Retries are slower by design (default +2 s/+4 s per failing node). CI keeps
  the suite fast by zeroing `MOIRA_RETRY_BACKOFF` where retry paths are
  exercised; the always-failing retry test costs ~6 s at defaults — accepted.
- A sleeping worker thread is not cancellable mid-backoff (cancellation is
  honored between node batches and via subprocess kill, ADR-006/B1); worst
  case adds seconds to a cancel — accepted at this backoff scale.
- Error text can contain model/tool output; it was already persisted in retry
  events, so the prompt/audit exposure adds no new data class. The untrusted-
  data framing for such content is research QW9, tracked separately.

## Alternatives Considered

- **Reuse the feedback channel** — rejected (semantics, see §1).
- **Exponential backoff with jitter** — rejected for now: over-engineered for
  ≤3 attempts; revisit if `max_retries` grows or provider rate-limit handling
  (research ST8) lands.
- **Keep errors in the shared context under a per-node key** — rejected:
  see §2 thread-safety.
- **Provider-limit detection (held_until re-claim)** — out of scope: that is
  research ST8 on the durable-job layer; this ADR only paces in-process
  attempts.

## References

- `orchestrator/moira_core/backends/contract.py` — `attempt_errors_block()`
- `orchestrator/moira_core/engine.py` — informed retry loop + audit input
- `orchestrator/moira_core/backends/claude_code.py`, `litellm_backend.py` — wiring
- `orchestrator/PERSISTENCE.md` — `MOIRA_RETRY_BACKOFF`
- `orchestrator/tests/test_retry_context.py` — 11 tests (block rendering,
  informed-retry integration, shared-context isolation, backoff pacing,
  audit fidelity, prompt wiring)
- `m-c-research/23-rekomendacje-state-of-the-art.md` — QW3; related ST8
