# ADR-003: Model Routing — LiteLLM

**Date:** 2026-06-04  
**Status:** Accepted  
**Deciders:** Tomasz Skonieczny

## Context
Moira must support multiple LLM backends from day one: Claude (Anthropic), OpenAI, and local models via Ollama. Each agent in a pipeline should be configurable to use a specific model.

## Decision
**LiteLLM** as the unified model routing layer.

## Rationale
- Single API for 100+ LLM providers including Ollama (local models)
- Handles authentication, retry, rate-limit management per provider
- Supports accuracy-mode vs cost-mode routing patterns
- Provider fallback built-in — critical given 60% of LLM errors are rate-limits (Datadog 2026)
- Python-native, fits perfectly with LangGraph sidecar
- Per-agent model assignment: each pipeline node specifies its model

## Routing Strategy
```
Accuracy-mode:  complex decisions → Claude Opus / GPT-4o
Cost-mode:      routine tasks → Claude Haiku / Llama3 local
Local-first:    privacy-sensitive → Ollama (no data leaves machine)
```

## Consequences
- LiteLLM is a dependency that introduces its own versioning risks
- Local model quality (Ollama) varies — quality gates compensate for this
- Rate-limit management delegated to LiteLLM reduces custom infrastructure

## Alternatives Considered
- **Direct Anthropic SDK:** Only Claude, no multi-model
- **Custom router:** Full control, significant build cost, ongoing maintenance
