# exAI — UX/IA inspiration mapped to Moira

**Date:** 2026-06-04  
**Source:** exAI Cloud screenshots (`/home/tse/hycom/sdlc/inspiracje/`)  
**Purpose:** extract concrete UX patterns; decide adopt / differ / skip for Moira. exAI is the closest UX reference (full-SDLC multi-agent cockpit).

## What exAI's UI shows

| Surface | What it does |
|---------|--------------|
| **Orchestrator — "Wire your agents"** | Drag-drop canvas; left palette of agent nodes grouped by category (Code Quality, Security, Collaboration); "Drop your first agent"; right panel = node detail (severity, category). Top bar: name, Save, Execute. |
| **Execution view (3 columns)** | Left = Execution plan (coordinator → code-architect → executor → reviewer, with sub-agents + status). Middle = Live activity (streaming log of agent actions/assignments). Right = Session files (generated artifacts, e.g. `/Frontend/task-notify.js`). |
| **Project header** | "Project: … OBJECTIVE: …" + progress bars (completed %, backend/frontend/hybrid) + tech-stack badges. |
| **Overview dashboard** | "Welcome back" + metric tiles (active/completed projects) + Live sessions table + Workspace cards ("Clone IDE"). |
| **Workspace / IDE** | Embedded VSCode-like editor + terminal + "Ask the workspace agent" chat. |
| **Create multi-agent project wizard (5 steps)** | 01 Project Setup (Create New / Clone Repo / Use Existing + name + workspace) → 02 Requirements → 03 Tech Stack → 04 Agents (grid of agent cards tagged analysis/execution/transformation/generation/orchestration + Collaboration Mode: Sequential/Parallel/Hybrid + default model + timeout) → 05 Review & Start. |
| **Admin dashboard** | Quick Actions (Create User/Team, Generate Report, Settings); More Actions (**SBOM Report, Directory Sync SCIM, Security Audit, API Keys, Billing, API Status, Backup, Extensions**); License Management. |

## ADOPT (strong patterns, fit Moira)

| Pattern | How Moira uses it |
|---------|-------------------|
| **5-step project wizard** | Same flow, but Moira pulls Requirements + Tech Stack **from the AI SDLC repo** (single source of truth) instead of typing them. "Use Existing / Clone / New" maps to linking the software repo + AI SDLC repo. |
| **Agent palette grouped by category** | Maps directly to our producer/verifier taxonomy. Categories: analysis · design · implementation (execution) · generation · transformation · **security** · orchestration. Card UI with category tag. |
| **Collaboration Mode (Sequential/Parallel/Hybrid)** | Adopt as pipeline execution mode. |
| **3-column execution view** | This is Moira's cockpit. Left = Execution plan **with gates inline** (a gate is a visible node that pauses). Middle = Live activity. Right = Session files / artifacts + diffs. **Add Moira's edge:** per-step audit record (input/output/tools/decisions/approvals/cost/time/owner) on click. |
| **Project header with progress + tech badges** | Helicopter summary per project. |
| **Overview dashboard (live sessions + projects)** | Multi-project home. |
| **Admin: SBOM, SCIM, Security Audit, API keys, License** | Validates our operating-model pillars (RBAC, secrets, audit). This is Moira's admin/governance surface (later phases). exAI shipping these confirms regulated buyers expect them. |

## DIFFER (same surface, Moira does it differently — our wedge)

| exAI | Moira |
|------|-------|
| Cloud SaaS (airgapped option) | **Self-hosted first**, git-native AI SDLC repo as single source of truth |
| Agents type requirements in wizard | Requirements/specs **read from AI SDLC repo** — INT→REQ→FUNC→ADR lineage |
| Gates implied, autonomy-forward | **Configurable human/auto/hybrid/off gates per node** + **CLIENT gate** (external approval of intent/req/func-spec) — exAI shows no external-client persona |
| "Session files" = output view | Output view **+ per-step audit record** as the defensible core (DEC-MOIRA-001) |
| Builds its own coding execution | **Delegates coding to frontier backends** (Claude Code CLI / Codex) per ADR-004 — Moira is the governance layer above |

## SKIP (for v0.1, maybe never)

- **Embedded full VSCode IDE + workspace agent.** Heavy. Per delegation architecture, Moira doesn't need to own an IDE — the "Session files / diffs" read view is enough for v0.1. The developer keeps their own editor; Moira orchestrates + audits. Revisit only if a real need appears.

## Implication for v0.1 cockpit

The **3-column execution view is higher value-per-effort than the drag-drop editor** and is the better v0.1 cockpit target:
- Left: execution plan with gates inline (read-only graph — React Flow optional here, a list/tree works).
- Middle: live activity stream (SSE from the Python sidecar).
- Right: session files + click-through to per-step audit record.

The drag-drop "Wire your agents" editor stays **P2** (demo candy, deferred per red-team) — the execution/monitoring view is what proves the spike and demos the differentiator (gates + audit).
