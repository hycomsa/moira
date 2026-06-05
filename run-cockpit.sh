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
exec python3 orchestrator/moira_api.py \
  --port 8765 \
  --repo ../ai-sdlc \
  --static cockpit/dist
