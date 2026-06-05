#!/usr/bin/env bash
#
# Moira — one-shot desktop launcher.
# Builds the cockpit frontend, compiles the Tauri shell, and launches the native
# window. The app spawns the Python orchestration sidecar (moira_api.py) on
# port 8765 automatically. Everything needed to really run, in one command.
#
#   ./run-desktop.sh
#
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

say() { printf "\n\033[1;34m[moira]\033[0m %s\n" "$*"; }
die() { printf "\n\033[1;31m[moira] %s\033[0m\n" "$*" >&2; exit 1; }

# ---- prerequisites -------------------------------------------------------- #
command -v python3 >/dev/null || die "python3 not found"
command -v npm     >/dev/null || die "npm not found (needed to build the cockpit)"
command -v cargo   >/dev/null || die "cargo not found (needed to build the Tauri shell)"
[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || say "WARNING: no DISPLAY/WAYLAND_DISPLAY — a GUI session is required for the window."

# ---- free port 8765 (stale sidecar from a previous run) ------------------- #
pkill -f "moira_api.py" 2>/dev/null || true
sleep 0.3

# ---- 1) build the cockpit frontend ---------------------------------------- #
say "Building cockpit frontend…"
if [ ! -d cockpit/node_modules ]; then
  ( cd cockpit && npm install )
fi
( cd cockpit && npm run build )

# ---- 2) compile the Tauri shell (re-embeds the freshly built dist) -------- #
say "Compiling Tauri desktop shell (first build pulls webkit deps — be patient)…"
( cd src-tauri && cargo build )

# ---- 3) launch ------------------------------------------------------------ #
say "Launching Moira. The window opens and the Python sidecar starts on :8765."
say "Close the window to quit (the sidecar is killed automatically)."
cd "$ROOT"
exec ./src-tauri/target/debug/moira
