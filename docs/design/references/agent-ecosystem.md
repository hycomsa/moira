# Agent ecosystem — borrowed approaches & how Moira maps them

**Date:** 2026-06-04

Moira's agents are git-native YAML (`.ai/context/agents/*.yml`). The wider
coding-agent ecosystem is large and converging on a format Moira can consume
directly, so we (a) ship a broad curated pack, (b) import any collection, and
(c) lean on skill/workflow frameworks rather than re-implementing them.

## Sources analysed

| Source | What it is | What we borrow |
|--------|-----------|----------------|
| [VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) (100+) | Curated subagents by category | Role taxonomy; importable via `.md`→YAML |
| [wshobson/agents](https://github.com/wshobson/agents) (192) | Multi-harness agent/skill marketplace | Category structure; SDLC role coverage |
| [0xfurai](https://github.com/0xfurai/claude-code-subagents) (100+) | Production subagents | Importable |
| [obra/superpowers](https://github.com/obra/superpowers) | Skills framework: brainstorming, TDD (red-green-refactor), **subagent-driven-development** (fresh subagent/task + auto code-review between steps), debugging | The coding-node *runtime* + `skill_refs`; validates Moira's producer→verifier→gate model |
| [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done) | Spec-driven, context-engineering, long-horizon system; `pattern-mapper` agent; opt-in TDD pipeline | `codebase-pattern-mapper` agent; spec-driven + TDD pipeline templates |

## Format mapping (Claude Code subagent → Moira AgentDefinition)

Subagent `.md` = YAML frontmatter + system-prompt body. The importer
(`orchestrator/import_agents.py`, also `POST /api/agents/import`) converts:

| Subagent frontmatter | Moira agent field |
|---|---|
| `name` | `id` + `name` |
| `description` | `description` |
| `model` | `model` (hint; per-node override survives) |
| `tools` | `tools_policy` — `coding` if Write/Edit/Bash present, else `reasoning` |
| body (after 2nd `---`) | `system_prompt` |
| (inferred from name/desc) | `type` (review/audit/test/scan ⇒ verifier) · `category` |

So a team can `git clone` any collection and import the subset they want into a
workspace — no lock-in, fully git-native.

## Current-trend wiring (built into the curated pack + demonstrator pipeline)

- **Plan-strong / execute-cheap:** `project-planner` defaults to a **stronger model** (`model: opus`); implementers use the run's default.
- **Cross-model verification (LLM-as-judge):** `code-reviewer` and
  `spec-conformance-verifier` are verifiers pinned to a **different/stronger model**
  than the coder, giving independent judgment. Moira's per-node `model` (honored by
  both the LiteLLM and Claude Code backends) makes this real.
- **Parallel checks + join:** the `sdlc-full-crosschecked` pipeline fans out
  {code-reviewer, security-auditor, auto-tests} off `implement` and joins them at a
  hybrid gate — the DAG engine runs them concurrently.
- **subagent-driven-development (superpowers)** ≈ Moira's producer→verifier→gate
  with isolation per node; we cite it rather than rebuild it.

## How superpowers/gsd are leveraged (not re-implemented)

- `claude_code` producer nodes are intended to run inside a **superpowers-equipped
  harness** (ADR-004 delegation); their `skill_refs` name superpowers skills
  (`brainstorming`, `test-driven-development`, `requesting-code-review`,
  `subagent-driven-development`).
- gsd's spec-driven + pattern-mapper ideas appear as the `codebase-pattern-mapper`
  agent and the spec-driven/TDD pipeline shapes.

## Attribution

We authored Moira's curated agent prompts ourselves (no verbatim copying) to avoid
licensing entanglement; imported agents retain their source content and license —
attribute the upstream collection when redistributing imported agents.
