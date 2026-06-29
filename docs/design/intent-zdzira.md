# INT-MOIRA-zdzira-integration: Zdzira as the documentation, ticketing & client-collaboration module

**ID:** INT-MOIRA-zdzira-integration  
**Created:** 2026-06-04  
**Status:** Active — v0.2 scope  
**Owner:** Tomasz Skonieczny / Hycom  
**Depends on:** INT-MOIRA-cockpit (v0.1 MVP)

## Business Intent

### Problem
Moira orchestrates AI agents across the SDLC, but it lacks:
1. **Ticketing** — where are the tasks the agents are working on? Jira is too heavy; there's no need to use it.
2. **Client-facing documentation** — the client (the software buyer) sees raw Markdown files. They need a nice UI to browse documentation, intents, requirements and functional specs.
3. **Client collaboration** — certain quality gates must be accepted by the client: intents, requirements, func-specs. Today there is no mechanism for this.

### Existing solution: Zdzira PM
Zdzira (`/home/tse/hycom/zdzira`) is a desktop app (Tauri 2 + React — **the same stack as Moira**) that already provides:
- Git-native ticketing: tasks/stories/bugs/epics as `.md` + YAML frontmatter in a git repo
- Documentation: TipTap WYSIWYG + react-markdown + Mermaid, sidecar comments
- PM features: Kanban board, reports, PDF export
- Integrations: Jira sync, Confluence sync, Claude Code integration (already has `skills.rs`)
- ~75 Tauri IPC commands for full CRUD over tickets and documents

### Solution Intent
**Zdzira becomes a module of Moira** — not a separate application, but an integral part of the platform.

Three areas of integration:

#### 1. Git-native ticketing for agents
Agents create and update tickets (Markdown + YAML) in the project's git repo.  
The Moira cockpit shows a ticket board — what each agent is doing, the status of every task.  
Not Jira, not an external database — plain `.md` files committed to git. Single source of truth.

#### 2. Documentation in a nice UI (not raw Markdown)
The client / buyer gets access to Moira with limited permissions.  
They see the project documentation (intents, requirements, func-specs) in a beautifully rendered UI — not raw files.  
Mermaid diagrams, comments, version history — all available without any knowledge of git/Markdown.

#### 3. Client collaboration on quality gates
Specific quality gates in the SDLC pipeline require client approval (not just the internal team):
- Business intent (`INT-*`) → the client confirms the AI understood the problem correctly
- Requirements (`REQ-*`) → the client verifies completeness
- Functional spec (`FUNC-*`) → the client accepts the scope and behaviour before implementation

Mechanism: a quality gate changes the document's status to `pending_client_approval` → the client gets a notification → reviews it in Moira → APPROVE/REJECT/COMMENT → the pipeline continues or returns to the agent.

## Scope

### In Scope (v0.2)
- Zdzira ticketing in the Moira cockpit (board view + list of agent tasks)
- Agents create/update tickets automatically while working
- Documentation rendering (docs viewer) in Moira — no raw Markdown
- Client quality gate: `pending_client_approval` → notification → APPROVE/REJECT

### In Scope (v0.3)
- Client portal: Moira with limited permissions (read + approve, no configure)
- Mobile companion: approve quality gates from a phone (client and team lead)

### Out of Scope
- A full rewrite of Zdzira — reuse the code and data format, do not rebuild
- Real-time collaboration (multi-user simultaneous editing)
- Replacing Jira for clients who already have Jira (the Jira sync integration stays)

## Integration Architecture

### Chosen option: Moira as a superset of Zdzira (shared codebase)

```
Moira Desktop App (Tauri)
├── Cockpit module     ← new (agents, pipelines, gates)
├── Docs module        ← from Zdzira (TipTap, react-markdown, Mermaid)
├── Tickets module     ← from Zdzira (Kanban, board, task editor)
├── Git sync           ← from Zdzira (auto-commit, push, merge)
└── AI/Skills          ← merge (Zdzira has skills.rs + Moira's orchestrator)
```

**Why not two separate apps:**
- Same stack (Tauri 2 + React + TypeScript + Zustand) — the code is reusable
- The user does not want to switch between apps for tickets and the agent cockpit
- Single source of truth: one git repo, one data format, one UI

### Data format — the Zdzira format is the standard
```
.project-config.md        ← project config (Zdzira)
.docs.config.yaml         ← docs config (Zdzira)
tickets/                  ← Markdown tickets (Zdzira format)
  PROJ-001.md
  epic-feature-x/
    epic-feature-x.md
    PROJ-002.md
docs/                     ← project documentation (Zdzira)
  intent-specs/
  requirements/
  func-specs/
.ai/context/              ← AI SDLC context (Moira/framework)
```

### Client quality gate — mechanism

```
Pipeline node: [CLIENT GATE — Func Spec Approval]
  ↓
Agent finishes the func-spec → status: pending_client_approval
  ↓
Moira sends a notification to the client (email / push)
  ↓
Client opens Moira (limited permissions)
  ↓
Sees the beautifully rendered func-spec (not raw Markdown)
  ↓
Adds comments (sidecar .comments.yaml)
  ↓
APPROVE → status: approved → pipeline continues
REJECT  → status: rejected + comment → agent gets feedback → iterates
  ↓
Git commit: status change + comments → traceability in the history
```

### Permissions — roles in Moira

| Role | Sees | Can do |
|------|------|--------|
| **Team Lead / Admin** | Everything | Configure + orchestrate + approve + edit |
| **Developer** | Cockpit + tickets + docs | Monitor + approve (dev gates) + edit docs |
| **Client** | Docs + func-specs + status | Read + comment + approve (client gates) |
| **Viewer** | Docs + status | Read only |

Implementation: roles in `project-config.md` + git-level access control (SSH keys per role).

## Success Metrics
- An agent creates a ticket automatically for every task (no manual entry)
- The client approves a func-spec without opening a Markdown file (via the Moira UI)
- Time-to-client-approval < 24h (the client gets a notification and approves the same day)
- Zero "where is the documentation" questions — everything in one place

---

## Evidence

### Zdzira analysis (2026-06-04)

#### Stack — identical to Moira
- **Tauri 2** (Rust core) — the same shell as Moira
- **React 19 + TypeScript** — the same frontend
- **Zustand** — the same state management
- **Git CLI** — the same synchronization strategy
- Conclusion: code integration is natural and does not require a rewrite

#### Zdzira covers documentation and ticketing — fully
~75 Tauri IPC commands cover:
- Full CRUD for tickets/tasks/epics (Markdown + YAML frontmatter)
- Full CRUD for documentation (TipTap, Mermaid, sidecar comments, attachments)
- Git: auto-commit, push, pull, merge-conflict resolution
- Jira sync (bidirectional), Confluence sync
- AI integration: OpenAI-compatible API, Claude Code skills (`skills.rs`)
- PDF export, CSV export, search

#### Data format — ready for agents
A ticket = `.md` + YAML frontmatter. Agents can write/update tickets via `write_markdown_file`.
Frontmatter: `id`, `title`, `type`, `status`, `assignee`, `priority`, `labels`, `comments`, `task_links`.
Status changes are committed to git automatically → full traceability.

#### Missing pieces in Zdzira (Moira must add them)
1. **Role/permissions system** — Zdzira has no roles. Permissions via Git SSH keys (partial).
2. **Client portal** — no limited-access mode for the client.
3. **Quality-gate notifications** — no `pending_client_approval` mechanism.
4. **Web/mobile access** — desktop only; the client must install the app.
5. **Azure/Entra ID** — the dependencies are present (`@azure/msal-browser`), implementation unfinished.

#### Synergy with the AI SDLC framework repo
Zdzira's `.docs.config.yaml` + documentation are consistent with Hycom's current AI SDLC framework repo.
`intent-specs/`, `requirements/`, `func-specs/` in Zdzira's docs = the same artifacts as `.ai/context/`.
They can be combined: `.ai/context/` (AI context for agents) + `docs/` (rendered for the client) on the same repo.

## Validation of the client-collaboration concept
Cosmos (Augment Code) has a client-facing portal with limited permissions — confirms the market expects this.
10Clouds AIConsole has governance audit trails and per-decision confidence scoring — confirms that clients in regulated sectors want to see "why the AI did this".

## Open questions to validate
1. Does the client prefer to install a desktop app (Moira) or web access (browser)?
2. What is the minimal feature set for the client — only read+approve, or comments too?
3. Does the client want to see the agents' reasoning trail, or only the final artifacts (func-specs, ADRs)?
4. Does Zdzira's Jira sync conflict with, or complement, the workflow of clients who already have Jira?
