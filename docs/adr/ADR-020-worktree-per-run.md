# ADR-020: Run isolation via git worktrees (design decision)

- **Status**: Proposed (design settled here; implementation is the largest
  single investment of the next phase — do not start it piecemeal)
- **Date**: 2026-08-18
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-004 (delegated execution — agents write into a real
  working directory), ADR-006 (durable runner — resume must survive restarts),
  ADR-014 (test-fix loop — checks run where the code is),
  `m-c-research/` ST2 ("the largest single return in the series")

## Context

Every run today executes in the workspace's shared `code_path`. Consequences:

- **two concurrent runs on one workspace collide** (same files, same test
  runs) — the practical reason the embedded runner stays single-threaded;
- **no ×N variants** (the same task explored twice needs two working copies);
- the review artifact is a diff captured per step (`gitdiff.py` snapshots),
  not a branch a human can check out, run, or merge.

The comparative research rated per-run worktree isolation Cezar's single most
valuable structural idea, and its bug history (#347/#472/#782) a free
checklist. Precondition called out there and honored here: **decide the
two-repo question first** — Moira workspaces have `repo_path` (the AI SDLC
spec repo) *and* `code_path` (the code repo), and pipelines can touch both.

## Decision (design)

### 1. What gets isolated: `code_path` only

The **code repo** gets a worktree per run. The **AI SDLC repo does not**:
authoring artifacts (intents/func-specs/backlog) are *meant* to be a shared,
append-mostly source of truth — the human gate reviews them in place, two
discovery runs rarely target the same artifact, and `.moira-runs/` mirroring
plus artifact review already govern that repo. Isolating it would break every
consumer that reads specs from the canonical checkout (traceability, task
completeness, other runs' context). A coding+authoring pipeline therefore
runs: skills → `repo_path` as today; coding nodes → the run's code worktree.

### 2. Layout and lifecycle

- Worktree at `<code_path>/.moira/worktrees/<run_id>`, branch
  `moira/<run_id>` forked from the **freshest** of `HEAD` vs
  `origin/<default>` (Cezar #782: forking a stale local base silently
  reverts other people's merged work).
- Creation is **fail-closed**: if the worktree cannot be created, the run
  does not start against the shared checkout as a "fallback" — that fallback
  would silently reintroduce every collision this ADR removes.
- Creation is **idempempotent** (resume/retry): reuse a healthy existing
  worktree for the run; `git worktree prune` + repair/reattach before giving
  up; never `rm -rf` a non-empty directory that git still tracks.
- **Autosave**: after each HEAVY/coding node, commit the worktree's dirty
  state (`moira: autosave after <node_id>`) — a crashed or cancelled run
  leaves inspectable work, and the durable runner can hand the run to
  another worker without losing uncommitted files.
- **Retention**: keep the last N run worktrees (default 10) per workspace;
  prune oldest on run start; a pruned-but-needed worktree is rematerialized
  from its branch (the branch outlives the directory).

### 3. What the engine/API change (and what they don't)

- Launch resolves `ctx["cwd"]` for coding nodes to the run's worktree;
  `repo_path`-cwd authoring nodes are untouched. `gitdiff` capture, the
  `test_exec` check and ADR-014's evidence all operate inside the worktree
  with **zero changes** — they already honor `ctx["cwd"]`.
- The engine stays worktree-agnostic: a new `moira_core/worktree.py` owns
  create/reuse/repair/autosave/prune; the launch path and the runner call it.
- **Acceptance flow**: approving the run's final gate does NOT auto-merge.
  The run's deliverable is the `moira/<run_id>` branch; the report and the
  run page link it (`git checkout moira/<run_id>` / a PR/MR created by the
  human or a future integration). Auto-merge is a separate, later decision —
  merging without a human is an autonomy-profile question (research ST5),
  not an isolation question.

### 4. What this unblocks (in order)

1. **Multi-run per workspace** (embedded runner thread pool; `waiting_gate`
   must not hold an execution slot — Cezar's semaphore semantics).
2. **×N variants** of one task in sibling worktrees + judge-assisted pick
   (research ST13; requires this + multi-run).
3. **Branch-diff review**: the Inbox gate card can show the branch diff
   instead of reconstructed per-step patches.

## Consequences

- Positive: safe parallelism, real exploration, a checkoutable review
  artifact, crash-inspectable state — without touching the audit model.
- Costs: worktree lifecycle edge cases are the L in the estimate (the listed
  bug checklist is the mitigation); disk usage grows with retention N;
  repos with heavyweight setup (node_modules, venvs) pay a per-run setup tax
  — mitigations (shared caches, setup hooks) are implementation-phase
  decisions.
- Explicitly out of scope here: isolating `repo_path` (revisit only if
  concurrent authoring collisions materialize), auto-merge, Windows.

## Alternatives Considered

- **Isolate both repos** — rejected: breaks every canonical-checkout reader
  (traceability, completeness, cross-run context) to solve a collision that
  authoring workflows don't exhibit; gates already govern artifact changes.
- **Full clones per run** — rejected: worktrees share the object store
  (cheap, fast) and prune cleanly; clones multiply disk and fetch time.
- **Lock the workspace instead (one run at a time, forever)** — the status
  quo; rejected as the thing this ADR exists to remove.
- **Copy-on-write directory snapshots (no git)** — rejected: loses the
  branch-as-deliverable and the rematerialization-from-branch property.

## References

- `m-c-research/11-orkiestracja-i-pipelining.md`, `21-…`, `23-…` (ST2, ST11,
  ST13) — including Cezar's worktree bug list adopted as a checklist
- `orchestrator/moira_core/gitdiff.py`, `moira_api.py` launch path (cwd
  resolution), `runner.py` (handoff/resume)
