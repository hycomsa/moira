# PyInstaller spec — freeze the Moira orchestrator into a single self-contained
# binary (the Tauri "externalBin" sidecar). The orchestrator is dependency-free
# stdlib Python, so this is small. The built cockpit (../cockpit/dist) is embedded
# so the sidecar serves the UI + API on :8765 with no source tree and no system
# Python required by end users.
#
# Build (from the repo root, after `npm run build --prefix cockpit`):
#   pyinstaller orchestrator/moira-sidecar.spec
# -> dist/moira-sidecar  (rename to moira-sidecar-<target-triple>[.exe] for Tauri)
import os
from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = os.path.abspath(SPECPATH)                     # orchestrator/ (SPECPATH is the spec's dir)
ROOT = os.path.dirname(SPEC_DIR)                          # repo root
DIST = os.path.join(ROOT, "cockpit", "dist")

datas = []
if os.path.isdir(DIST):
    datas.append((DIST, "cockpit_dist"))

a = Analysis(
    [os.path.join(SPEC_DIR, "moira_api.py")],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("moira_core"),
    hookspath=[],
    runtime_hooks=[],
    excludes=["psycopg", "psycopg2", "psycopg_binary", "tkinter", "litellm"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="moira-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # headless server; no window
    disable_windowed_traceback=False,
    target_arch=None,      # native arch of the build host (CI builds per-OS/arch)
    codesign_identity=None,
    entitlements_file=None,
)
