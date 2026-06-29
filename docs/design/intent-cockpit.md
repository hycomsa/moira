# INT-MOIRA-cockpit: Moira AI SDLC Cockpit

**ID:** INT-MOIRA-cockpit  
**Created:** 2026-06-04  
**Status:** Active  
**Owner:** Tomasz Skonieczny / Hycom

## Business Intent

### Problem
Custom enterprise software delivery is dominated by coordination cost, not development cost. Every handoff in the SDLC chain (requirements → design → dev → QA → deploy) destroys context and generates delays — regardless of team competence. Most budget goes to coordination, not code.

Existing AI tools (vibe-coding: Cursor, Claude Code) are fast but lack governance, traceability, and enterprise compliance. They cannot be adopted in regulated environments (financial sector, healthcare, legal).

No existing tool combines: visual governance pipeline + agent orchestration cockpit + knowledge transfer in a single self-hosted product.

### Solution Intent
Moira is a **standardized engineering console for AI agents in software development** — the place where development teams configure, run, monitor, and control AI agents across the full SDLC.

Key principle: **AI works autonomously between human quality gates. Humans define where they want to decide — not react to every agent step.**

### Target Users
1. **Software house teams** (primary) — deliver projects faster with AI, maintain quality
2. **Enterprise IT / CTO teams** — self-service AI development with built-in governance
3. **Regulated industry teams** — financial, healthcare, legal — compliance-first AI SDLC

### Value Proposition
> **v0.1 honest claims:** less rework · better audit trail · faster handoff — in environments where SaaS dev tooling is restricted.

Do NOT claim "months to weeks" or "no compliance compromises" at this stage — unproven. Speed is *measured* in the spike, not asserted. Long-term aspiration remains delivery compression; the *sold* value now is rework reduction + auditability + handoff speed.

**5 Value Pillars:**
- Accuracy AI: guardrails + quality gates at every SDLC phase
- Measurable ROI: cost/time/quality metrics per project
- Rapid Iteration: AI between gates, feedback loop in minutes not days
- Knowledge Transfer: skills as Markdown, governance packs as reusable IP
- Human-AI Collaboration: visual pipeline, clear AI vs human responsibilities

## Scope

### In Scope (MVP v0.1)
- Visual SDLC Pipeline Builder (React Flow-based, per-project configuration)
- Agent Cockpit: live status, start/pause/resume/cancel/retry per agent
- 3 core agents: Requirements Analyst, Solution Architect, Code Generator
- Multi-model support via LiteLLM (Claude + OpenAI + Ollama)
- Quality Gate flow: APPROVE / REJECT (with feedback) / MODIFY
- AI SDLC Repo integration (reads Markdown specs from git)
- Per-step audit record (input · output · tools · decisions · approvals · cost · time · owner) + git-native lineage — NOT model-reasoning tracking
- Cost tracker (tokens + $ per agent/project)

### Out of Scope (v0.2+)
- Governance packs (KNF, RODO, SOX compliance)
- Full 8-agent SDLC suite
- Langfuse observability integration
- Team collaboration (multi-user)
- Cloud/SaaS deployment
- Skill marketplace
- Advanced ROI analytics dashboard

## Success Metrics
- First Hycom project delivered using Moira v0.1 (internal pilot)
- Time-to-delivery reduction: target -40% vs baseline
- Gate approval rate: >70% first-pass (quality of agents)
- Agent failure rate: <20% requiring human intervention between gates


---

## Evidence

### Research Findings (Deep Research, 2026-06-03)

### Market Gap Confirmed
No single platform integrates: multi-model routing + agent team coordination + OTel observability in one unified SDLC cockpit. Gap is real and confirmed by 112-agent adversarial research.

### Closest Competitor: IBM Bob (GA April 2026)
- Enterprise SDLC, dynamic multi-model routing (Claude + Mistral + IBM Granite)
- **Weaknesses:** IBM lock-in, no EU/PL governance packs, no visual pipeline, no self-hosted
- IBM Bob confirms the market exists and enterprise will pay for AI-native SDLC

### Rate-Limit as #1 Production Problem
60% of LLM call errors in February 2026 were rate-limit failures (Datadog State of AI Engineering 2026). No orchestration cockpit manages this at infrastructure layer. Moira's smart queuing with priority lanes and provider fallback is a differentiator.

### OTel GenAI SemConv — Right Foundation
OpenTelemetry has defined agent span schemas (create_agent, invoke_agent, invoke_workflow) covering Anthropic, OpenAI, AWS Bedrock, Azure. Experimental status but production-ready enough to build on.

### Protocol Stack
No single protocol covers full orchestration. Recommended: MCP (tool access) → A2A (agent coordination). Build on this, don't fight it.

### Hybrid Local/Cloud Routing Proven
60-80% of queries routable to local models (Ollama) with sub-20ms latency. 40-50% cost/latency reduction. Pattern proven architecturally.

## Competitive Landscape Analysis (2026-06-04)

### exAI Cloud — Full SDLC SaaS competitor
- 26 typed agents, typed DAG orchestrator, Plan-Diff-Apply with human gates
- Durable checkpointing every 60s with byte-identical replay; per-run token budgets with hard ceilings
- Native integrations: GitHub, GitLab, Linear, Jira, Datadog, Sentry
- Claims 66–82% delivery timeline compression
- **Moira differentiator:** self-hosted, visual pipeline configurable by user, governance packs, not SaaS lock-in

### Cosmos (Augment Code) — "OS for AI agents"
- Multi-platform: CLI + web + **mobile** (validates mobile companion direction)
- Organizational memory: new agents inherit full team history — knowledge compounds across projects
- Learning loops capture incident insights for continuous improvement
- Cross-agent context sharing, pluggable backends
- **Moira differentiator:** self-hosted, EU/PL governance, Hycom domain knowledge packs

### Cezar (open-mercato/cezar) — GitHub-focused cockpit
- Open source, React cockpit + Supabase Realtime, `.ai/skills/` Markdown playbooks
- 15 built-in Actions (triage, duplicate detection, security flagging)
- Run controls: pause/cancel/resume/retry; human gates; multi-backend (Anthropic + Claude Code CLI)
- **Role for Moira:** Cezar = one GitHub-automation skill within Moira ecosystem, not competitor

### 10Clouds AIConsole — Regulated sector services + platform
- Enterprise platform for orchestrating agents, workflows, integrations in financial sector
- Governance: decision audit trails, confidence scoring, output provenance per decision
- Production monitoring: model performance, bias metrics, drift over time
- **Validates:** regulated sector will pay for governance-native AI SDLC; confirms compliance audit trail as core requirement

### Updated Competitive Map
```
                    CLOUD SaaS          SELF-HOSTED
                ────────────────────────────────────
FULL SDLC       exAI Cloud             MOIRA ← clear gap
                Cosmos (Augment)
                IBM Bob

PARTIAL         Devin (coding only)    Claude Code
(no governance) Cursor                 (no cockpit)

OBSERVABILITY   LangSmith              Langfuse (OSS)
ONLY            Langfuse Cloud
```

## Validation Assumptions (to test in pilot)
1. Enterprise financial sector accepts AI in SDLC **if** governance is auditable
2. Hycom can encode domain knowledge (KNF, RODO) as reusable governance packs
3. Quality gates don't slow down enough to negate AI speed advantage
4. Dev teams (junior and senior) use pre-built skills instead of own prompts
5. Self-hosted is a hard requirement for banks (assumption: yes)
6. Mobile companion app (approve gates, monitor agents) is a real use case for team leads
