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

## Conventions

- Keep the orchestrator **dependency-free** (stdlib only) — it ships as the sidecar.
- **Secrets** stay in the OS keychain / `.env` (gitignored) — never in the repo or logs.
- Local state (`*.sqlite`, `.moira/`, `dist/`, `node_modules/`, `target/`) is gitignored — don't commit it.
- End commit messages with a `Co-Authored-By:` trailer when pairing.
