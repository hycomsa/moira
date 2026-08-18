# ADR-015: Fail-loud model identity — no silent model substitution

- **Status**: Accepted (implemented)
- **Date**: 2026-08-18
- **Author**: tomasz.skonieczny (with Claude)
- **Related**: ADR-003 (model routing via LiteLLM), ADR-005 (sealed audit),
  `m-c-research/` QW6

## Context

`LiteLLMBackend` silently substituted `gpt-4o-mini` whenever a node reached it
without a model (or with the `Node` dataclass default `"mock"`). The sealed
audit record's `input.model` then said `"(default)"` or `"mock"` while a
different model actually did the work and incurred the cost — the record lied
about what ran. For a product whose thesis is a defensible audit, a silent
model substitution is not a convenience default; it is an integrity defect
(research file 12: "rekord `cost` kłamie o tym, co naprawdę policzyło").

## Decision

1. **The backend refuses loudly.** `LiteLLMBackend` has no default model at
   all (the attribute is gone). A node without an explicit model gets an
   actionable error (`"litellm requires an explicit model … — no silent
   default"`) **before** any provider call is attempted.
2. **The configuration fails at save/launch, not mid-run.**
   `validate_pipeline` rejects a litellm PRODUCER/VERIFIER node without an
   explicit model — validation already runs on pipeline save and on
   `POST /api/runs`, so the cockpit editor shows the error immediately
   (per the research's risk note about typo'd YAMLs: the message names the
   node and gives examples).
3. **Scope**: `claude_code` keeps accepting an empty model — that resolves to
   the CLI's own default under the user's login, the CLI itself fails loudly
   on an unknown model name, and the audit's `"(default)"` is truthful there.
   `mock` is exempt by nature. No provider/model format whitelist is
   attempted: providers change too fast for a static list, and litellm's own
   resolution error is loud enough once the model is explicit.

## Consequences

- Positive: `input.model` in the sealed audit is now always the model that
  ran (explicit for litellm; the CLI-default marker only where that is the
  truth). Misconfigured pipelines fail in the editor with the node named,
  not minutes into a run.
- Negative: existing YAML pipelines that relied on the silent default now
  fail validation until a model is set — intended, visible, and a one-line
  fix; the ADR-012 probes-and-blockers pattern set the precedent that
  configuration errors surface before work starts.

## Alternatives Considered

- **Env-configurable default (`MOIRA_LITELLM_MODEL`)** — rejected: still a
  substitution the pipeline author didn't write; it would just move the lie
  from code to environment.
- **Record the resolved model into the audit instead of failing** — rejected:
  fixes the record but keeps the surprise (cost/capability of an unchosen
  model); explicitness is the product stance.
- **Provider/model format validation** — rejected as unmaintainable; loud
  failure with an explicit model is sufficient.

## References

- `orchestrator/moira_core/backends/litellm_backend.py`
- `orchestrator/moira_core/validation.py`
- `orchestrator/tests/test_model_identity.py` — 6 tests
- `m-c-research/23-rekomendacje-state-of-the-art.md` — QW6
