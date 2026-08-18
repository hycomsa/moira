# ADR-016: Escalating, verified process termination (SIGTERM → SIGKILL → unblock)

- **Status**: Accepted (implemented)
- **Date**: 2026-08-18
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-006 (mid-drive cancellation, B1), ADR-004 (delegation to CLI
  subprocesses — which makes process hygiene an orchestrator concern),
  `m-c-research/` QW7

## Context

The timeout watchdog and `cancel()` did a bare `proc.kill()` on the direct
child. Three defects, all documented as production postmortems in Cezar
(#844/#858 — the research's advice: "these are postmortems Moira hasn't lived
through yet; copying someone else's scars is cheaper"):

1. **No process-group kill.** The `claude` CLI spawns children (tools, node
   subprocesses) that inherit our stdout pipe. Killing only the direct child
   leaves a grandchild holding the pipe open — and the stream reader
   (`_reduce_stream`) blocks forever waiting for an EOF that never comes.
   Reproduced in this repo by a test that hangs on the pre-ADR code.
2. **No graceful step.** Straight SIGKILL denies the CLI any chance to flush
   state or clean up.
3. **No verification.** `kill()` returned immediately; nothing confirmed the
   process actually exited before the backend reported "killed".

## Decision

`_terminate_tree()` in `claude_code.py` — one termination path used by the
timeout watchdog, `cancel()`, and the cancel-race branch:

1. The CLI is spawned with **`start_new_session=True`**, so it and everything
   it spawns share one process group; signals go to the **group**
   (`os.killpg`, with a direct-child fallback if the group is gone).
2. **SIGTERM first**, then up to `TERM_GRACE = 8 s` for a verified exit
   (`proc.wait`); only a process that ignores it gets **SIGKILL**, then up to
   `EOF_GRACE = 4 s` (grace values adopted from Cezar's field-tested 8 s/4 s
   watchdogs). A well-behaved CLI is never SIGKILLed — asserted by test.
3. **Last-resort unblock**: if something detached survives even the group
   SIGKILL and still holds our pipe, our stdout end is force-closed so the
   reader unblocks (it handles the resulting `ValueError/OSError`).
4. **The verdict is race-free and visible**: the reader unblocks the instant
   the group dies, while the watchdog thread may still be mid-escalation — so
   the run path **joins the watchdog** before composing the result, and the
   error names what happened: `"timed out after Ns (SIGTERM, exit verified)"`
   vs `"(SIGTERM→SIGKILL, exit verified)"`. The escalation is part of the
   trail, not a silent detail.
5. `cancel()` fires the escalation **off-thread** (daemon) per process — the
   API's cancel request returns promptly while grace windows run.

## Consequences

- Positive: no orphaned CLI children after timeout/cancel; no reader hangs on
  inherited pipes; graceful shutdown first; the audit/events can distinguish a
  cooperative exit from a forced one.
- Negative: a timed-out node now takes up to `TERM_GRACE` longer to report
  (bounded, and only on the failure path); POSIX-specific (`killpg`,
  `start_new_session`) — acceptable, the desktop/server targets are
  Linux/macOS; Windows would need a Job-Objects port behind the same seam.
- The `LiteLLMBackend` still has no in-flight cancel (no subprocess to
  signal) — unchanged scope, tracked in research ST8/QW8 notes.

## Alternatives Considered

- **Immediate SIGKILL on the group (no TERM step)** — rejected: denies cleanup
  and makes every timeout look identical in the trail.
- **`subprocess.run(timeout=…)`** — rejected: it kills only the direct child
  (same grandchild-pipe hang) and forfeits streaming.
- **Reader on a separate thread with its own timeout** — rejected: hides the
  leak instead of fixing it; the group kill addresses the cause.

## References

- `orchestrator/moira_core/backends/claude_code.py` — `_terminate_tree`,
  `_signal_group`, `TERM_GRACE`/`EOF_GRACE`, watchdog join, threaded cancel
- `orchestrator/tests/test_process_watchdog.py` — 4 tests: the
  grandchild-pipe hang (hangs pre-ADR), SIGKILL escalation of a TERM-ignoring
  tree, SIGTERM-only for a cooperative process, prompt group-kill on cancel
- `m-c-research/23-rekomendacje-state-of-the-art.md` — QW7
