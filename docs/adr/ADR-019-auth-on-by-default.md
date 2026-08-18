# ADR-019: Auth on by default, end-to-end (TODO)

- **Status**: Proposed (accepted direction, implementation TODO — deliberately
  parked; see "Why not yet")
- **Date**: 2026-08-18
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-008 (the identity/RBAC machinery this turns on),
  SECURITY_BOUNDARIES.md (rows this will retire), `m-c-research/` ST3

## Context

ADR-008 built the full identity stack — JWT (local HS256 / OIDC), default-deny
RBAC with five roles, persona-gated approvals — but three execution gaps keep
it theoretical in exactly the situations a customer sees first:

1. The **API process default** is `MOIRA_AUTH_MODE=off` (`authn.py`); only the
   web launcher (`run-cockpit.sh`) opts into `local`. Anything else that
   starts the sidecar — a script, a service unit, a future installer — runs
   open unless it remembers the env var.
2. The **Tauri desktop runs with auth off by design**: the session token is
   injected into `index.html` when served over HTTP, but the desktop webview
   loads embedded assets, so the token never arrives (the repo's only literal
   TODO).
3. **CORS is `*`** — acceptable-ish with bearer headers (no cookies), but
   flagged as defense-in-depth debt since the must-fix review.

"Governance off by default is governance that doesn't exist in the demo" —
the research's phrasing stands. SECURITY_BOUNDARIES currently owns this
honestly; this ADR is the plan to retire those rows.

## Decision (direction)

1. **`MOIRA_AUTH_MODE=local` becomes the process default.** `off` stays
   available but must be *asked for* (`MOIRA_AUTH_MODE=off`), flipping the
   burden: openness becomes the explicit choice. `run-desktop.sh` keeps `off`
   only until (2) lands, then drops it.
2. **Tauri token delivery**: the Rust shell (which already spawns the sidecar
   and knows its startup) receives the session token from the sidecar's
   stdout/handshake and injects it into the webview via an initialization
   script (`window.__MOIRA_TOKEN__`) — same contract the web injection uses,
   so `api.ts` needs no changes. No token in the URL (leaks via logs/history).
3. **CORS narrowed** to the served origin (`http://127.0.0.1:<port>` and the
   Tauri origin); `MOIRA_ALLOWED_ORIGINS` for overrides. Preflight stays 204.
4. **One-command onboarding** so solo DX survives the default flip: on first
   start with no configured secret, the sidecar mints the secret, provisions
   the local admin token and hands it to the launching shell (web: injected as
   today; CLI: printed once). No manual token juggling for the single-user
   case.

## Acceptance criteria (for the implementing change)

- A bare `python3 moira_api.py` rejects unauthenticated `/api/runs` with 401.
- The desktop app works with auth **on**: gate decisions carry the
  authenticated principal, and `run-desktop.sh` contains no `MOIRA_AUTH_MODE`.
- A browser page on a foreign origin cannot call mutating APIs (CORS).
- `SECURITY_BOUNDARIES.md` rows "auth everywhere by default" and the CORS
  clause move to the guaranteed table.
- Fresh-clone solo onboarding remains one command.

## Why not yet

The flip changes the default behavior of every entry point at once
(CLI/tests/scripts) and demands the Tauri handshake plus onboarding UX in the
same change — shipping it piecemeal would break either the demo path or the
test harness. It is scheduled after the current quality-loop/budget wave, as
its own focused change. Until then the launchers keep compensating
(`run-cockpit.sh` forces `local`) and SECURITY_BOUNDARIES keeps telling the
truth.

## References

- `orchestrator/moira_core/authn.py:33` (the default), `moira_api.py` (CORS,
  token injection), `src-tauri/` (handshake site), `run-*.sh`
- `docs/MANUAL_VERIFICATION.md` — the desktop-auth manual check this unblocks
- `m-c-research/23-rekomendacje-state-of-the-art.md` — ST3 (P0)
