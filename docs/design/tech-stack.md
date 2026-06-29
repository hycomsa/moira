# Moira Tech Stack Standards

> **Partly historical.** This early stack note predates the durable-runner work — the
> Python sidecar uses a **custom dependency-free DAG engine**, not LangGraph (ADR-002 was
> superseded by [ADR-006](../adr/ADR-006-durable-runner-execution.md)). Treat the LangGraph
> references below as the original plan; the implemented backends are mock / claude_code /
> litellm (see the [ADRs](../adr/README.md)). Frontend/IPC guidance still holds.

## Application Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Tauri Shell (Rust)                                     │
│  ┌──────────────────────┐  ┌─────────────────────────┐  │
│  │  React UI            │  │  Python Sidecar         │  │
│  │  TypeScript          │  │  LangGraph              │  │
│  │  React Flow          │◄─►  LiteLLM               │  │
│  │  (pipeline visual)   │  │  Langfuse SDK           │  │
│  │                      │  │  OTel                   │  │
│  └──────────────────────┘  └─────────┬───────────────┘  │
│                                      │                   │
│                              ┌───────▼──────────┐       │
│                              │  Agent Backends  │       │
│                              │  Claude API      │       │
│                              │  OpenAI API      │       │
│                              │  Ollama (local)  │       │
│                              └──────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

## Frontend Standards

### Stack
- **Framework:** React 18+ with TypeScript (strict mode)
- **Build:** Vite (Tauri default)
- **Styling:** Tailwind CSS
- **Pipeline UI:** React Flow v12+
- **State:** Zustand (lightweight, no Redux overhead)
- **Data fetching:** TanStack Query for async state

### Component conventions
- Functional components only, no class components
- Props typed with TypeScript interfaces (no `any`)
- One component per file, named export
- Component files: `PascalCase.tsx`
- Hooks: `use` prefix, `camelCase.ts`

### Tauri IPC
- Use `@tauri-apps/api/core` invoke for commands
- Use `@tauri-apps/api/event` listen for SSE-style streaming from Python sidecar
- All Tauri commands typed end-to-end

## Backend Standards (Python Sidecar)

### Stack
- **Python:** 3.11+
- **Orchestration:** LangGraph 0.2+
- **Model routing:** LiteLLM 1.40+
- **Observability:** opentelemetry-sdk + langfuse
- **Storage:** SQLite via `sqlite3` (stdlib) or `aiosqlite` for async
- **HTTP (internal):** FastAPI for Tauri↔Python IPC (alternative to direct Tauri commands)

### LangGraph conventions
- Each SDLC phase = one LangGraph node
- Quality gates = interrupt points (`interrupt_before` or `interrupt_after`)
- State typed with TypedDict
- Checkpoints to SQLite for persistence

### Agent conventions
- Each agent class in `agents/` directory
- Agent receives: task, spec context, guardrails, model config
- Agent returns: result, reasoning_trail[], files_changed[], cost_info
- Agents do not call each other directly — orchestrator manages flow

### LiteLLM conventions
- Model config in `config/models.yaml` — not hardcoded
- Always set `metadata` with project_id, agent_id, run_id for cost tracking
- Use `litellm.completion` with fallback list for reliability

## AI SDLC Repo Conventions

### Directory structure
```
.ai/context/
  project-config.md       ← project metadata + tech stack
  state.md               ← current sprint state
  intent-specs/          ← business intentions per module
  adrs/                  ← architecture decision records
  requirements/          ← structured requirements
  func-specs/            ← functional specifications
  references/            ← external docs, designs, APIs
  standards/             ← project-specific coding standards
```

### Spec references in agent prompts
Always reference spec with: `[SPEC-ID §section]` format
Example: "Implement JWT authentication per [INT-MOIRA-cockpit §FR-001]"
