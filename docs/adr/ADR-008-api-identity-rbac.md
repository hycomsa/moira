# ADR-008: API identity & RBAC

**Date:** 2026-06-26
**Status:** Accepted (backend + web cockpit auth done; Tauri webview token + live OIDC pending)
**Deciders:** Tomasz Skonieczny

**Relates to:** operating-model.md (identity/RBAC design), must-fix #2; arms #3 (gates) and #5 (governance override).

## Context

The API bound `127.0.0.1` with wildcard CORS and **no authentication**; gate approval took the
approver from the request body (`by`), and the docs invited LAN/phone access. So Moira's "human
quality gate" and governance overrides were *labels*, not authorization — anyone reaching the port
could read files, start runs, and approve any gate under any name. RBAC was designed in
operating-model.md (5 roles) but not enforced.

## Decision

**One identity contract, two profiles, default-deny RBAC.**

### Identity (`MOIRA_AUTH_MODE`)
Always *verify a JWT → claims → Principal*; only the issuer/role-source swaps:
- `off` (default, until the frontend is wired): enforcement disabled; caller treated as local admin. Nothing breaks.
- `local` (desktop/dev): the sidecar **self-issues + verifies a short-lived HS256 JWT** with its own
  secret (stdlib only — no IdP/Keycloak). Roles from the token's `roles` claim (local user → `[admin]`).
- `oidc` (team): a real IdP issues the JWT; verified via JWKS (lazy-imports PyJWT, optional dep like
  psycopg). A `group→role` map (Moira settings/env, **not** per-user in the repo) maps IdP groups to roles.

No user→role assignments live in the repo (rejected as weak).

### Authorization (RBAC)
Five roles (Admin/Developer/Compliance/Client/Viewer) → a fixed code matrix of **actions**
(`configure/launch/approve_gate/governance_override/run_eval/read_sensitive/read`) and **approvable
gate personas**. Separation of duties: Developer can't approve compliance/legal gates; Client reads
docs without touching config/code; Viewer reads only. `qa`/`accessibility-lead` sit under Developer.

Enforcement is **default-deny** at the `do_GET`/`do_POST`/`do_DELETE` choke-points: every `/api/*`
route maps to a required permission (plain reads → `read`; file/log/browse/debug/runner/health →
`read_sensitive`; mutations → `launch`/`configure`). PUBLIC allowlist: non-`/api` static,
`/api/ready` (readiness, no secrets), OPTIONS. 401 unauthenticated, 403 unauthorized.

Gate approve/reject takes the approver from the **authenticated principal** (request body `by` is
ignored — no spoofing) and requires `can_approve(principal, gate.persona)`.

## Built (this commit, fully tested incl. live HTTP + Postgres)
`moira_core/authz.py` (roles, matrix, `can`/`can_approve`, `required_action`, `authorize_request`);
`moira_core/authn.py` (modes, local HS256 mint/verify, claims→Principal, OIDC JWKS path + group→role);
enforcement wired in `moira_api.py` (feature-switched by mode); gate persona check + principal-based
approver; `/api/ready`. Tests: 25 unit (authz/authn) + 7 HTTP e2e (401/403/200, sensitive-read
tiering, **gate persona enforced + body-spoof ignored**).

## Phase 2 — web cockpit auth ON (done, verified live)
The sidecar, in `local` mode, mints a short-lived session token for the local user and **injects it
+ a `fetch` wrapper into the served `index.html`**, so the web cockpit authenticates with no IdP and
no rebuild. `run-cockpit.sh` now defaults `MOIRA_AUTH_MODE=local`. Verified end-to-end against a live
sidecar: `/api/ready` public; `/api/*` → 401 without token; injected token → 200; governance packs
load. The library default stays `off` (tests/embedding unaffected).

## Still deferred
- **Tauri desktop:** loads EMBEDDED assets, so the index.html injection does NOT reach its webview —
  desktop stays `off` until the Tauri shell injects the token into the webview (Rust init script/IPC).
  `run-desktop.sh` documents this; do not flip it to `local` until then.
- Tighten CORS to the known frontend origin (today still `*`; acceptable with header-bearer tokens —
  no cookies — but defense-in-depth).
- Live OIDC against a real IdP; structured audit fields (`auth_source`, `persona_at_decision`) beyond
  the current `by`=subject + source-in-confirmed.
- Enforce governance pack `override.requires_reason` / `allowed_personas` at a dedicated override
  endpoint (today: persona authority via the gate is enforced).

## Consequences
- The quality gate and governance overrides become real, non-forgeable controls; audit records the
  authenticated subject, not user-entered text.
- Desktop stays zero-config (mode `off` today; `local` self-issued JWT when turned on — no IdP).
- Until phase 2 flips the default, enforcement is opt-in via `MOIRA_AUTH_MODE`, so nothing regresses.
