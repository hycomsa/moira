# Contributing to Moira

Thanks for working on Moira. This is a single mono-repo for the product:

```
orchestrator/   Python sidecar — dependency-free DAG engine, gates, audit (hash chain),
                pluggable persistence (SQLite/Postgres/git), HTTP API, pluggable backends
                (mock / claude_code / litellm). Drives AI SDLC skills for discovery.
cockpit/        React + TypeScript + Vite frontend (the cockpit UI + mobile gate inbox).
src-tauri/      Tauri v2 desktop shell that spawns the Python sidecar.
docs/           Marketing landing pages and assets.
```

The **AI SDLC framework content** (intents, requirements, func-specs, ADRs, agents, skills)
and any **target application code** live in *separate* repositories — Moira reads/writes them
as a *workspace*, but they are not part of this product repo.

## Prerequisites

| Need | For |
|------|-----|
| Python 3.11+ | orchestrator + API (stdlib only — no pip install needed) |
| Node 18+ / npm | building the cockpit |
| `claude` CLI (logged in) | the real agent/skill backend (optional — `mock` works offline) |
| `cargo` + webkit2gtk | the native desktop app (optional) |

## Run it

```bash
./run-cockpit.sh                 # builds the cockpit, serves UI + API on http://127.0.0.1:8765
# dev mode (hot reload), two terminals:
python3 orchestrator/moira_api.py --repo /path/to/ai-sdlc-repo   # API on :8765
npm --prefix cockpit run dev                                     # UI on :5173 (proxies /api)
./run-desktop.sh                 # native desktop shell (needs cargo + webkit2gtk)
```

## Tests & build (run before opening a PR)

```bash
python3 -m unittest discover -s orchestrator/tests -q     # orchestrator (must stay green)
npm --prefix cockpit run build                            # type-check + production build
```

CI (GitHub Actions) runs both on every push/PR. The Tauri/Rust build is **not** in CI (it
needs webkit2gtk system deps); build it locally with `cargo tauri build` when touching `src-tauri/`.

Repo-reader resolution tests are opt-in: point `MOIRA_CSL_REPO` at a real AI SDLC repo to run
them, otherwise they skip.

## Optional: Superpowers coding backend

Moira can drive a coding node with [Claude Code **Superpowers**](https://github.com/obra/superpowers)
(plan → TDD → systematic debugging → code review) instead of the custom `dev@*` skills — they
**coexist**, you opt in per run:

```bash
git clone https://github.com/obra/superpowers ~/.moira/plugins/superpowers
export MOIRA_SUPERPOWERS_DIR=~/.moira/plugins/superpowers   # enables the opt-in
```

Then pick the **`sdlc-superpowers`** pipeline (or the **`superpowers-coder`** agent in the editor).
The backend loads the plugin *for that run only* via `claude --plugin-dir` (role `superpowers-coder`),
so every other run — `dev@*`, discovery, eval, compliance — is untouched. With the env var unset,
behaviour is unchanged. Heavy coding roles get a larger turn/time budget automatically.

## Releases (macOS / Windows / Linux)

Releases are produced by `.github/workflows/release.yml` — **push a tag `v*`** (e.g. `v0.1.0`) and CI
builds installers for all platforms and publishes them (+ updater `latest.json`) to this repo's
**Releases**. In-app auto-update is enabled.

**How the Python dependency ships:** the orchestrator (zero-dep stdlib) is frozen with **PyInstaller**
into a single self-contained binary per OS/arch — with the built cockpit `dist` embedded — and bundled
as a Tauri **`externalBin`** sidecar. End users need **no system Python**. (Real agent backends still need
the `claude` CLI installed; `mock` works offline.) macOS ships **per-arch** (arm64 + Intel) because
PyInstaller can't cross-compile a universal binary.

Required GitHub secrets (Settings → Secrets and variables → Actions):

| Secret | Purpose |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` (+ `_PASSWORD`) | signs updater artifacts (`tauri signer generate`; public key is in `tauri.conf.json`) |
| `APPLE_CERTIFICATE` (+ `_PASSWORD`) | Developer ID cert (`.p12`, base64) for macOS signing |
| `APPLE_SIGNING_IDENTITY`, `APPLE_TEAM_ID` | macOS code-signing identity |
| `APPLE_API_KEY`, `APPLE_API_ISSUER`, `APPLE_API_KEY_CONTENT` | App Store Connect API key for notarization |

Windows installers are currently unsigned (SmartScreen will warn) — add a code-signing cert later if needed.

**Cut a release:** bump `version` in `src-tauri/tauri.conf.json` (and optionally `cockpit/package.json`),
commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. Watch the **Actions** tab.

## Debugging / logs

Runs launch **non-blocking**: `POST /api/runs`, `/api/discovery`, and gate approve/reject return a
`run_id` immediately and drive the pipeline on a background thread — the cockpit then streams progress
(Runs/Inbox poll every ~2.5 s). So a real `claude` step no longer freezes the "Start"/decision button.

Where to look when something misbehaves:
- **Live, per run:** open **Runs**, select the run → execution plan (per-node status) + a streaming
  activity log. Node failures show as `retry` / `node.escalate` events carrying the error (e.g. `claude`
  stderr or timeout).
- **Across runs:** the **Activity** page → **Events** tab (all runs) and **Sidecar logs** tab (the
  orchestrator logfile, tailed live).
- **Logfile on disk:** `MOIRA_LOG` (default next to `MOIRA_DB` → `<app-data>/moira.log` in the desktop
  app; the path is also in `GET /api/health` and `GET /api/logs?tail=N`).
- **Dev:** run `./run-cockpit.sh` (or `python3 orchestrator/moira_api.py …`) in a terminal — the sidecar
  logs to stdout there too. For the UI, use the Tauri webview devtools.

Note: `POST /api/eval` (quality/conformance/compliance scorecards) is intentionally **synchronous** — it
returns the scorecard in the response, so that call does block until the judge finishes.

### Reproducing a run: `MOIRA_DEBUG` + debug bundle

- Set **`MOIRA_DEBUG=1`** (any value but `0`/`false`/empty) before launching the sidecar to make the
  `claude_code` backend record, as a `debug` live record per node, the **exact command + full prompt** it
  hands the model (plus `cwd`, role and the chosen timeout), and the **stderr/exit code on failure**. No
  secrets are on the cmdline — the `claude` CLI authenticates from the keychain — so this is safe to keep.
  Whether it's on is shown in `GET /api/health` (`config.debug`).
- Every run drill-down has a **🐞 Debug bundle** button → downloads `moira-debug-<run_id>.json`:
  run + pipeline + events + audit + cost + per-node state, the **live stream** (including the
  command/prompt above when `MOIRA_DEBUG=1`), and the slice of the sidecar log for that run. Same payload
  as `GET /api/runs/{id}/debug`. Attach it to a bug report and the run is fully reproducible offline.

## Tuning: agent timeouts & retries

The `claude_code` backend runs in three budget tiers, all env-configurable (sane defaults). Skills fail
fast so a headless-broken `ba@*` escalates to a human gate in minutes instead of grinding; coding gets a
big budget. Current values are shown in `GET /api/health` (`config`).

| Env var | Default | Applies to |
|---|---|---|
| `MOIRA_CLAUDE_SKILL_TIMEOUT` | `300` s | discovery/authoring skill nodes (`ba@*`/`pm@*`/`qa@*`) |
| `MOIRA_CLAUDE_SKILL_MAX_TURNS` | `20` | skill nodes — enough turns for multi-file authoring (decompose/test-plan) |
| `MOIRA_SKILL_RETRIES` | `1` | skill retries before escalating (1 = 2 attempts) |
| `MOIRA_CLAUDE_TIMEOUT` / `MOIRA_CLAUDE_MAX_TURNS` | `600` s / `12` | default (analysts, verifiers, evals) |
| `MOIRA_CLAUDE_HEAVY_TIMEOUT` / `MOIRA_CLAUDE_HEAVY_MAX_TURNS` | `1800` s / `40` | coding (`code-generator`, `superpowers-coder`) |

So a broken skill escalates after `skill_timeout × (skill_retries + 1)` ≈ 10 min. The turn budget (20)
doesn't affect a *hung* skill — the watchdog kills on time — so raising turns keeps fail-fast intact while
letting healthy multi-file authoring skills finish. Set e.g. `MOIRA_CLAUDE_SKILL_TIMEOUT=120
MOIRA_SKILL_RETRIES=0` for aggressive fail-fast, or bump `MOIRA_CLAUDE_SKILL_MAX_TURNS` for very large specs.

## Conventions

- Keep the orchestrator **dependency-free** (stdlib only) — it ships as the sidecar.
- **Secrets** stay in the OS keychain / `.env` (gitignored) — never in the repo or logs.
- Local state (`*.sqlite`, `.moira/`, `dist/`, `node_modules/`, `target/`) is gitignored — don't commit it.
- End commit messages with a `Co-Authored-By:` trailer when pairing.
