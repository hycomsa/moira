#!/usr/bin/env bash
# Build the cockpit frontend and serve it + the orchestration API on one origin.
# Open http://127.0.0.1:8765  (no Tauri needed — runs as a local web cockpit).
#
# For the desktop (Tauri) shell instead:  cargo tauri dev   (needs tauri-cli + webkit2gtk)
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/2] building cockpit frontend…"
npm --prefix cockpit install --silent
npm --prefix cockpit run build

echo "[2/2] starting Moira API + cockpit on http://127.0.0.1:8765"
# Auth ON for the web cockpit: the sidecar self-issues a local session token and
# injects it (+ a fetch wrapper) into the served index.html, so the cockpit
# authenticates with no IdP. For team mode set MOIRA_AUTH_MODE=oidc + the OIDC_* vars.
export MOIRA_AUTH_MODE="${MOIRA_AUTH_MODE:-local}"
exec python3 orchestrator/moira_api.py \
  --port 8765 \
  --repo ../ai-sdlc \
  --static cockpit/dist
