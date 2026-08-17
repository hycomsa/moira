# ADR-012: Backend install/login probes + launch gating

- **Status**: Accepted (implemented)
- **Date**: 2026-08-17
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-004 (execution delegated to CLI backends under the user's own
  login — which makes login state operational, not incidental), ADR-011
  (informed retry — what used to absorb these failures), `m-c-research/` QW4

## Context

Moira delegates execution to CLIs authenticated by the user (`claude` under the
user's Pro/Max login — ADR-004). Before this change the system's entire
awareness of that dependency was `shutil.which(binary)` surfaced as a boolean
in `/api/health`. Nothing checked the version and nothing checked the login, so
a logged-out or missing CLI produced the worst failure shape: the launch
succeeded, the run was enqueued, and the truth surfaced **minutes later** as a
failed node after burning its retries — at the worst possible moment for
diagnosis.

The comparative research (`m-c-research/`, file 12) flagged this as a mature,
field-tested pattern in Cezar (`backend-detect` / `provider-auth`: probes,
status latch, asymmetric caching) that Moira lacked entirely. Both probe
commands were verified against the real CLI before implementation:
`claude --version` → `"2.1.233 (Claude Code)"`, `claude auth status` → JSON
with `loggedIn` / `email` / `authMethod`.

## Decision

New stdlib-only module `backends/probes.py` + wiring, governed by three
honesty rules:

1. **A probe reports only what it verified.** A probe that errors yields
   `authenticated=None` (*unknown*) — never a claimed state. The CLI owns its
   output format; if it changes, Moira degrades to "unknown", not to a lie.
2. **Unknown never blocks.** `launch_blockers()` blocks only *definite*
   unusability: not installed, or auth status explicitly `loggedIn: false`.
   A broken probe must never take the product down with it.
3. **Every negative carries the fix.** Each blocker/status includes a
   copy-paste command (`npm install -g @anthropic-ai/claude-code`,
   `claude auth login`) — surfaced in the API error, `/api/health`, and the
   cockpit Settings panel.

### Probe scope per backend

- `claude_code`: PATH check → `--version` banner (cosmetic; its failure never
  fails the probe) → `auth status` JSON (`loggedIn`, `email`).
- `litellm`: import check only. Auth is per-provider API keys whose validity
  cannot be verified without a **paid** call — so the probe deliberately never
  claims a login state (rule 1).
- `mock` and unknown backends: benign (`installed=True`) — never block what we
  cannot see; if such a backend fails, the run reports it honestly (ADR-011).

### Asymmetric-TTL cache

Healthy results cache for `TTL_OK=300 s`, negative/unknown for `TTL_BAD=30 s` —
a just-fixed login is noticed within seconds while a healthy setup isn't
re-probed on every `/api/health` poll. (Pattern carried over from Cezar
together with this detail, per the research's explicit warning not to drop it.)
The cache is warmed off-thread at API start (`probes.warm`, daemon — never
blocks boot).

### Launch gating

All three work-launching paths fail fast with **503
`{error: "backend not ready", blockers: [...]}`** when a required backend is
definitely unusable:

- `POST /api/runs` — gates the set of backends actually used by the *resolved*
  pipeline's executable nodes (gates/auto-checks excluded; a mixed-backend
  pipeline is gated on all of them);
- `POST /api/discovery` and `POST /api/eval` — gate `claude_code`, which they
  hard-require.

503 (service unavailable, retryable) rather than 400: the request is
well-formed; the environment is not ready.

## Consequences

### Positive

- Misconfiguration surfaces **before** any run record, job, or token spend
  exists, with the exact fix command — instead of as a failed node.
- `/api/health` now answers "can work actually run?" (installed, version,
  login, detail, hint per backend), and the cockpit Settings panel renders it
  live with the fix command.
- Honest on CI: machines without the CLI report `installed=false`; nothing
  fabricates readiness (and `mock` runs remain ungated, so offline tests pass).

### Negative / trade-offs

- First unwarmed probe spends ~1–2 s on two subprocesses inside a request;
  bounded by `PROBE_TIMEOUT=10 s` per command and amortized by the cache +
  boot warm-up.
- A launch can pass the gate and still hit a just-expired login (TTL window) —
  the gate reduces the failure class, it cannot eliminate it; ADR-011's
  informed retry remains the backstop.
- `auth status` parsing is coupled to CLI output; by rule 1 a format change
  degrades to "unknown" (which never blocks), not to an outage.

## Alternatives Considered

- **Gate inside the engine/runner** — rejected: by then the run row and job
  exist and the user has lost the synchronous error path; fail-fast belongs at
  the API boundary, and the runner keeps ADR-011 retries for what slips
  through.
- **Block on unknown auth state** — rejected: violates rule 2; a probe outage
  would freeze all launches.
- **Probe litellm key validity with a live model call** — rejected: costs
  money per health poll and rate-limits the user for telemetry.
- **Status latch on 401 mid-run** (Cezar's second half of the pattern:
  flipping the cached status when a run hits an auth error) — deferred:
  requires classifying backend errors, which belongs with ST8
  (provider-limit handling) on the durable-job layer.

## References

- `orchestrator/moira_core/backends/probes.py`
- `orchestrator/moira_api.py` — `/api/health` probes, launch gates on
  `/api/runs`, `/api/discovery`, `/api/eval`, boot warm-up
- `cockpit/src/pages/SettingsPage.tsx` — live Backends panel;
  `cockpit/src/api.ts` — `BackendProbe`
- `orchestrator/tests/test_backend_probes.py` — 14 tests (probe parsing,
  unknown-on-error, asymmetric TTL, blockers, HTTP launch gate, health shape)
- `m-c-research/23-rekomendacje-state-of-the-art.md` — QW4; deferred latch → ST8
