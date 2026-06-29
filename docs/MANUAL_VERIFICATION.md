# Manual verification — things that need a real machine

These checks could **not** be verified in the headless dev/CI environment (no GUI
display, no real IdP). Run them on a machine with a desktop session / your IdP.
Everything else (214 unit/integration tests, live Postgres, web-cockpit auth, the
governance gate e2e) is automated and green.

## Must verify (core of the auth/governance work)

1. **Web cockpit with auth ON**
   - `./run-cockpit.sh` (defaults to `MOIRA_AUTH_MODE=local`), open http://127.0.0.1:8765
   - Expect: dashboard loads (data populated). Click a real flow: start a run → approve a gate → confirm it proceeds.
   - The sidecar injects the session token into the served page; no login needed.

2. **Desktop (Tauri) still works**
   - `./run-desktop.sh` on a machine with a display.
   - Expect: window opens, app works **as before** (desktop intentionally stays `MOIRA_AUTH_MODE=off`).
   - ⚠️ Do NOT set `MOIRA_AUTH_MODE=local` for desktop yet — the Tauri webview loads embedded assets, so the token isn't injected there and API calls would 401. (Tracked as a TODO: inject the token via a Tauri init script.)

3. **Governance pack blocks on a real violation**
   - Run a pipeline with `governance_packs: ["logs-advanced"]` against a service that logs sensitive data (e.g. `log.info(f"pwd={password}")`).
   - Expect: the `log_hygiene` check fails (CRITICAL), the governance gate escalates/blocks, and the run report shows the policy-coverage table + applied pack id@version.

4. **Role separation in the live flow (no spoofing)**
   - With `MOIRA_AUTH_MODE=local` and tokens for different roles, try to approve a `compliance` gate.
   - Expect: a **Developer** token → 403; a **Compliance** token → allowed. The request-body `by` is ignored — the approver is the authenticated subject.

## Only if going to team / enterprise mode

5. **OIDC (team identity)**
   - Set `MOIRA_AUTH_MODE=oidc` + `MOIRA_OIDC_JWKS_URL` / `MOIRA_OIDC_ISSUER` / `MOIRA_OIDC_AUDIENCE` + `MOIRA_OIDC_GROUP_ROLES` (JSON group→role map); `pip install "PyJWT[crypto]"`.
   - Expect: tokens from your IdP are verified via JWKS and mapped to roles. (Only the claim→role mapping is unit-tested; live RS256/JWKS against a real IdP is unverified.)

6. **Postgres multi-runner soak**
   - One or more external runners (`moira_runner.py --mode external`) + `MOIRA_PRIMARY=postgres` under sustained / long-running load.
   - Expect: exactly-once execution, lease heartbeat holds long runs, no double-execution. (Conformance + durable-job tests pass on live Postgres; a full soak is unverified.)

## Cockpit UX (interactive — needs a browser; build + render verified headless)

7. **Node → agent definition jump**
   - Pipelines page → click a producer/verifier node → in the right inspector click **↗ Open definition**.
   - Expect: navigates to the Agents page with that agent's editor drawer open.

8. **Selection remembered across page switches**
   - Open a pipeline (and select a node) → switch to another page → back to Pipelines.
   - Expect: the same pipeline is loaded and the same node re-selected (persisted in `localStorage["moira-ui"]`, survives reload too).
   - Agents page: type a search, open an agent, switch away and back → the search is restored and the last-opened agent card is highlighted/scrolled into view (the editor stays closed — it only opens via the node→agent jump).
