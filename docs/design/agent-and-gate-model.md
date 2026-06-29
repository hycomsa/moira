# Moira — Agent & Gate Model (Configure layer)

**Date:** 2026-06-04  
**Status:** Active design  
**Basis:** owner decisions + DEC-MOIRA-001 (gate design principles)

## Two orthogonal concepts

Moira's "Configure" layer rests on two independent ideas that compose:

1. **Agents** do the work at each SDLC stage.
2. **Gates** are configurable checkpoints between/after stages — human, auto, or off.

## Agent types

### Producer agents (create artifacts)

| Agent | Phase | Produces |
|-------|-------|----------|
| Requirements Analyst | Intent → Requirements | structured requirements, gap flags |
| Functional Spec Author (BA) | Analysis | func-specs (user perspective) |
| Solution Architect | Design | architecture, ADRs |
| Code Generator | Implementation | code — **delegated to frontier backend (ADR-004)** |
| Test Author | Test | test suites |

### Verifier agents (assess → produce a verdict that feeds a gate)

| Agent | Checks | Output |
|-------|--------|--------|
| Code Quality Reviewer | standards, maintainability, architecture adherence | findings + pass/fail |
| Security / Pentest Agent | **automated security probing**: SAST, dependency scan, secrets, DAST-style | findings + severity |
| Compliance Checker | industry-regulation adherence vs encoded standards | flags (NOT a conformance warranty) |
| Legal / Privacy Checker | law, data protection (RODO/GDPR, etc.) | flags |
| Test Runner / QA | executes tests, coverage | results + coverage |

**Security agent scope (decided):** automated security probing — SAST, dependency/secrets scanning, DAST-style. It does **not** replace a manual human penetration test; that remains a separate specialist activity.

**Verifier liability rule:** verifier agents produce *findings + flags against encoded standards*, never "you are compliant/secure." The accountable verdict belongs to the human at the gate (see liability note in DEC-MOIRA-001).

## Gate model — fully configurable (human or not)

Each gate consumes verifier verdicts and is configured independently:

```yaml
gate:
  id: post-implementation
  consumes: [code-quality, security, compliance]   # which verifier verdicts
  mode: auto | human | hybrid | off
  persona: lead-dev | architect | ciso | compliance-officer | client | none
  escalate_on: [any-fail, high-risk]               # hybrid trigger
  required_approvals: 1
```

| mode | behavior |
|------|----------|
| `auto` | verifier verdict decides pass/fail — no human |
| `hybrid` | auto-check runs; human involved ONLY on flag / high-risk |
| `human` | named persona must approve |
| `off` | no gate |

### Confidence-driven routing (from Cezar — proven pattern)

`hybrid` mode is operationalized via a **confidence threshold** (Cezar reference, `cezar-feature-analysis.md`):

```
confidence ≥ high cutoff   → auto-accept
medium                     → queue to Inbox for the named persona
confidence < low cutoff    → auto-deny (back to agent)
```

- Configured with a **slider + live preview** of which findings would route where, plus a **dry-run simulation** before enabling. This is how risk-tiered depth becomes operable instead of theoretical — and how we avoid both rubber-stamping (only medium-confidence reaches humans) and bottleneck (high-confidence auto-flows).
- Gates surface to humans as an **Inbox queue** (pending decisions, filterable by persona/confidence/type).

The same verifier agent always runs; the **gate** decides whether its verdict passes automatically or wakes a named human. This is how the bottleneck-vs-rubber-stamp paradox is resolved per stage.

## Gate design principles (so gates are signal, not theater)

1. **Show the decision-relevant DELTA, not the whole artifact.** e.g. "3 architectural decisions; 1 deviates from ADR-003 — here's which and why." Not 800 lines of diff.
2. **Risk-tiered depth.** Step risk classification sets gate mode: low → auto-check only; high → human gate with full context. Scarce reviewers (e.g. single Compliance Officer) only see high-risk.
3. **Reviewer gets an adversarial position, not a blank page.** A critic agent surfaces *what is questionable* ("scrutinize Y") rather than reassuring — to avoid automation bias.
4. **Capture WHAT was confirmed, not just "approved."** e.g. "Approved — reviewer confirmed Y, aware of ADR-003 deviation." This makes the audit trail real provenance, not a stamp.

## SDLC map

```
INTENT      → Requirements Analyst          →[GATE: client / human?]
ANALYSIS    → Functional Spec Author          →[GATE: BA / client approval]
DESIGN      → Solution Architect (ADR)        →[GATE: architect human?]
CODE        → Code Generator (frontier)       →
            ├ Code Quality Reviewer ┐
            ├ Security/Pentest Agent ┼─verdicts→[GATE: lead-dev / auto?]
            └ Compliance + Legal     ┘
TEST        → Test Author + Test Runner       →[GATE: QA auto-pass?]
DEPLOY      → Deploy Agent                     →[GATE: release manager human]
```

Every `[GATE]` is independently configured (mode + persona + risk-tier).

## Audit record — the defensible alternative to "reasoning trail"

We do NOT track the model's reasoning (it's not introspectable; OTel captures spans/narration, not causation). Instead Moira builds a per-step **audit record** from concrete, deterministic facts:

| Field | Captures |
|-------|----------|
| **input** | what the step received — spec refs, context inputs |
| **output** | artifacts produced / changed |
| **tools used** | backends, tools, MCP servers invoked |
| **decisions** | choices made (e.g. library, pattern) + the option taken |
| **approvals** | gate decisions, what the reviewer confirmed, by whom |
| **cost** | tokens + $ |
| **time** | timestamps + duration |
| **owner** | accountable human / persona |

Plus **artifact lineage** (code → IMPL → FUNC §x → REQ → INT) — git-native, deterministic.

Model rationale narration is **optional**, labeled "model-generated", and is never the basis of the audit. This is more defensible than reasoning-tracking: **every field is a verifiable fact, not an inferred mental state.**

## v0.1 vertical slice (subset of this roster)

The spike needs the minimum chain to prove the thesis end-to-end:
`Requirements Analyst → Solution Architect → Code Generator → Test Author/Runner → (Code Quality + Security verifiers) → one human gate`.
Full roster + per-gate config is the target vision, not all in v0.1.
