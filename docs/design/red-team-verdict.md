---
id: DEC-MOIRA-001
title: Red-team verdict — reposition before building beyond v0.1
type: decision-record
status: open
created: 2026-06-04
author: tomasz.skonieczny
related:
  - intent-cockpit.md
  - intent-zdzira.md
---

# DEC-MOIRA-001: Red-team verdict

> **Source:** Adversarial red-team (32 agents, 6 hostile lenses, steelman + adjudication)
> **Result:** 27 objections → 25 serious → **0 kills_concept, 16 requires_pivot, 8 mitigable, 1 overblown**
> **Verdict:** VIABLE-WITH-PIVOTS

## TL;DR

- The concept is **not fundamentally flawed** — the coordination-cost thesis and the client-gate wedge survive the attack.
- The **deck as written is flawed**: marketing claims contradict Hycom's own repo. Most "unfair advantages" are already shipped by incumbents, unbuilt, or unvalidated.
- **Do not invest in full v0.1 → v0.4 roadmap before running 3 cheap kill-tests.** Each can independently kill the thesis; all are free or ~2 weeks.

## The load-bearing problem

The single most damaging pattern: **the concept's marketing contradicts its own code.**
- `moira-app` = 0 commits. The cockpit, multi-model orchestrator, visual pipeline, Zdzira merge — none exist.
- The mature asset (`ai-sdlc`, 200+ files) is a Markdown/skills governance layer that runs **inside Claude Code** and delegates all code execution to upstream `obra/superpowers` — the exact runtime the team decided NOT to own.

## Top surviving risks (ranked)

| # | Risk | Verdict | Core residual |
|---|------|---------|---------------|
| 1 | **Moat is gone** — GitLab Duo (GA Jan 2026) shipped self-hosted + air-gapped + self-hosted-LLM + agent governance + approval gates. Claim "no tool unifies governed self-hosted orchestration" is falsifiable in one search. | requires_pivot | Marquee advantage is now table stakes from the incumbent already inside target banks. |
| 2 | **Two incompatible businesses conflated.** Low-risk (internal margin, services, pack IP) = capacity-bound services economics, not venture-scale. Venture-scale (platform license) = hardest services→product pivot. | requires_pivot | Cannot pitch both in one canvas. Drop "sell to rival software houses." |
| 3 | **Code Generator node can't be built by wiring LangGraph.** Real coding needs a frontier model → self-hosted/local-Ollama story is FALSE for the one node that matters in v0.1. | requires_pivot | Multi-model pillar collapses for the DEV node. Honest posture = "Claude Code sidecar + cockpit". |
| 4 | **"Months to weeks" unproven, maybe false.** If the bottleneck is org approval cadence + un-SLA'd client gate, compressing inter-gate work can't compress the calendar. | requires_pivot | Recast value as auditability/rework-reduction, not raw speed. |
| 5 | **Governance packs = real IP but liability + unbuilt + sequenced last.** Selling a "KNF Compliance Pack" can shift AI Act liability onto the bank; the one compliance artifact in the repo is an inactive stub. | requires_pivot | Reframe as versioned, dated, expert-maintained decision-support the bank adopts and owns. |

Other requires_pivot: quality gates re-import coordination cost (and EU AI Act mandates them anyway); reasoning "traceability" via OTel is post-hoc narration not causal; skills layer is BA/architect tooling not dev tooling (KA#4 tests wrong user); client gate degrades to rubber-stamp on large diffs; Zdzira shared-codebase superset doubles maintenance (wrap, don't swallow); client portal+mobile is a second product sequenced ahead of core ROI proof.

## What is genuinely defensible (survived)

1. **Coordination-cost thesis** — 60-80% of regulated delivery cost is handoffs; no incumbent's engineer-centric platform attacks it end-to-end.
2. **CLIENT-as-gate-persona** — external non-technical stakeholder approving intent/requirements/func-specs before code exists. The one job GitLab/IBM/Augment structurally don't do. A feature, not yet a moat — but the right wedge.
3. **Decision provenance + attestation** — deterministic git-native artifact lineage (INT→REQ→FUNC→AC→IMPL→ADR) with author/reviewer attribution. Already exists, more than LangSmith/Langfuse offer.
4. **Dogfooding channel** — Hycom as first customer/auditor, selling delivery-with-tool-behind-it through a trusted integrator relationship. Sidesteps "unknown vendor at a bank."
5. **Architecture-as-delegation** — concede the coding runtime to best-of-breed harnesses; Moira = governance/orchestration layer above them.

## Decided pivots

1. **Kill the false moat claims.** Demote "self-hosted" from unfair-advantage to table-stakes. Reposition ABOVE GitLab/GitHub/Azure (orchestrate their agents, SCM-neutral), not against them.
2. **Pick ONE investment thesis** — either ring-fenced vertical-SaaS entity OR honest margin/win-rate lever for Hycom services. Not both.
3. **Reframe value:** "compress engineering work + rework between governance milestones, cut cost of each approval" — not "months to weeks."
4. **Reposition governance packs** as versioned/dated decision-support with EULA disclaiming conformance warranty; named human gate is the accountable control.
5. **Re-sequence roadmap:** pull governance-pack vertical slice + accountability controls (SSO-bound identity, agent-decision audit trail, risk-tiered review depth) FORWARD. Demote mobile (no moat value).
6. **Zdzira: wrap, don't swallow** — consume its data format, vendor only docs-render/ticket/git-sync as a library. Exclude Confluence/Jira sync, export, skills.rs/ai.rs.
7. **"DEV execution is delegated, not re-implemented"** as an explicit ADR; distinct "external runtime" node type for the coding step; pin + vendor superpowers with SBOM + DORA exit path.

## The 3 kill-tests (gate v0.1 spend)

1. **Incumbent-coverage kill-test** (3 CISO calls, ~2 weeks, free): GitLab Duo Self-Managed + watsonx vs Moira on on-prem + self-hosted-LLM + audit. If 2 of 3 say an incumbent covers 80% at zero new-vendor risk → moat dead.
2. **Leadership commitment test** (one meeting, free): written commitment to a named full-time product team (PM + 3 eng) ring-fenced from billing 12 months + board decision "tools or delivery." If they won't pull engineers off billable → stays an internal tool forever.
3. **Orchestrator spike** (1-2 weeks): drive one real FUNC spec spec→code→passing tests→review through a LangGraph graph WITHOUT shelling to Claude Code, LiteLLM-routed models only, 20 runs. If unattended completion <~30% → honest architecture is "Claude Code sidecar + cockpit", multi-model pillar collapses for DEV node.

**Bottom line: fund the experiments, not the roadmap.** Concept earns v0.1 investment only after kill-tests 1-3 come back positive.

## Owner clarifications (2026-06-04)

1. **Orchestration:** LangGraph was the assistant's recommendation, not a requirement. Owner wants **frontier models (Claude, Codex) for coding/reasoning/analysis**, with local models as an **anti-lock-in option** — NOT local-only coding. → Resolved in ADR-004. Multi-model pillar = *model-agnostic / no vendor lock-in*, not *everything local*. Softens risk #3.

2. **GitLab Duo (risk #1):** Owner's intent is to **build Hycom's own alternative** — GitLab Duo is a **reference/inspiration, not a competitor to beat or a license to pay**. Driver = own the tooling, avoid per-seat license fees to a US vendor, keep control.
   - This **strengthens the internal-tool / build-own case** (cost avoidance + control + dogfooding margin lever — the low-risk thesis the steelman already defended).
   - It does **NOT** by itself restore an *external product* moat vs GitLab. The strategic fork stands: **internal owned platform** (strong, low-risk) vs **licensed product competing with incumbents** (where the moat critique bites). Don't conflate the build-own rationale with a market moat in any external pitch.

3. **Next step chosen:** Build re-scoped v0.1 = orchestrator spike (= kill-test #3). See `v0.1-scope-and-plan.md`.

## Owner reframing on the business lens (2026-06-04)

The red-team's "pick one investment thesis / two incompatible businesses" (risk #2) was a VC-investability critique. **Owner explicitly de-prioritizes that lens right now.** The orientation:

- **Build a genuinely good solution for software houses.** Hycom is the first/main user at the start — but do NOT hard-assume "internal tool only." Keep the door open.
- **Not worrying about build cost or market positioning at this stage.** The driving question is *"is this a great solution for the job?"*, not *"is this venture-investable?"*.

→ Therefore the VC framing is **parked, not resolved**. The red-team findings that still matter are the ones about **building it well**, independent of business model:
- Architecture honesty (ADR-004: delegation, frontier + local) — *resolved*.
- Gates becoming rubber-stamp or bottleneck — *design concern for v0.1+*.
- Client-as-gate-persona is the real wedge — *build toward it*.
- Traceability must be real provenance, not post-hoc theater — *design concern*.
- "Months to weeks" — *measure it in the v0.1 spike, don't claim it*.
- Governance-pack liability — *v0.4 concern, validate with one control later*.

## Still open (parked, not blocking the build)

- [ ] Business kill-tests 1-2 (CISO calls, leadership commitment) — useful signal, but NOT a gate on building v0.1. Run when convenient.
- [ ] External productization vs internal — decide later, with evidence from a working v0.1.
