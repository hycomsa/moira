# ADR-001: Desktop Shell — Tauri

**Date:** 2026-06-04  
**Status:** Accepted  
**Deciders:** Tomasz Skonieczny

## Context
Moira v0.1 is a desktop application. We need to choose between Electron and Tauri as the desktop shell.

## Decision
**Tauri** (Rust core + system WebView + React frontend)

## Rationale
- ~10MB install vs ~150MB for Electron — critical for enterprise adoption ("what are you installing on our machines?")
- No bundled Chromium — uses system WebView, smaller attack surface
- Rust backend provides faster IPC with Python sidecar
- Growing ecosystem, increasingly production-proven
- Python orchestration sidecar works identically in both — no ecosystem loss

## Consequences
- Smaller developer pool familiar with Tauri vs Electron
- System WebView differences across OS (mitigated by targeting Linux/macOS/Windows with tested configs)
- React + TypeScript UI layer is identical — frontend devs unaffected

## Alternatives Considered
- **Electron:** More mature, larger ecosystem, but 150MB install and bundled Chromium overhead
- **Native Rust UI (Tauri without WebView):** Maximum performance, minimal AI ecosystem
