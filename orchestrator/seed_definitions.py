"""Seed the AI SDLC repo with git-native agent + pipeline definitions.

Materializes the 6 built-in agent roles and the 2 built-in pipelines as YAML
under <repo>/.ai/agents and <repo>/.ai/pipelines, so the system is data-driven.
Idempotent. Usage: python3 seed_definitions.py [repo_path]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from moira_core.repo_reader import AISdlcRepo

AGENTS = [
    # ---- planning (planner on a STRONGER model — plan-strong / execute-cheap) ----
    dict(id="project-planner", name="Project Planner", type="producer",
         category="planning", role="project-planner", backend="claude_code", model="opus",
         description="Breaks the intent/spec into an ordered, testable implementation plan. Runs on a stronger model.",
         tools_policy="reasoning", skill_refs=["brainstorming", "writing-plans"]),
    dict(id="codebase-pattern-mapper", name="Codebase Pattern Mapper", type="producer",
         category="planning", role="codebase-pattern-mapper", backend="claude_code",
         description="Maps existing conventions/patterns in the repo so new code matches (gsd-inspired).",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="requirements-analyst", name="Requirements Analyst", type="producer",
         category="planning", role="requirements-analyst", backend="claude_code",
         description="Parses a FUNC spec into structured, testable requirements.",
         tools_policy="reasoning", skill_refs=["ba@discover-requirements", "ba@shape-func-spec"]),
    # ---- design ----
    dict(id="solution-architect", name="Solution Architect", type="producer",
         category="design", role="solution-architect", backend="claude_code",
         description="Produces the design and architecture decisions (ADRs) for the spec.",
         tools_policy="reasoning", skill_refs=["arch@shape-adr"]),
    dict(id="api-designer", name="API Designer", type="producer",
         category="design", role="api-designer", backend="claude_code",
         description="Designs the API surface: endpoints, contracts, error model, versioning.",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="data-modeler", name="Data Modeler", type="producer",
         category="design", role="data-modeler", backend="claude_code",
         description="Designs the data model / schema and a safe migration strategy.",
         tools_policy="reasoning", skill_refs=[]),
    # ---- implementation ----
    dict(id="code-generator", name="Code Generator", type="producer",
         category="implementation", role="code-generator", backend="claude_code",
         description="Implements the spec as code. Delegated to a frontier coding backend.",
         tools_policy="coding", skill_refs=["dev@implement", "test-driven-development", "subagent-driven-development"]),
    dict(id="frontend-developer", name="Frontend Developer", type="producer",
         category="implementation", role="frontend-developer", backend="claude_code",
         description="Implements UI/components per the design and frontend standards.",
         tools_policy="coding", skill_refs=[]),
    dict(id="backend-developer", name="Backend Developer", type="producer",
         category="implementation", role="backend-developer", backend="claude_code",
         description="Implements services/APIs per the design and backend standards.",
         tools_policy="coding", skill_refs=[]),
    dict(id="refactorer", name="Refactorer", type="producer",
         category="implementation", role="refactorer", backend="claude_code",
         description="Improves structure/readability without changing behavior; keeps tests green.",
         tools_policy="coding", skill_refs=[]),
    # ---- generation ----
    dict(id="test-author", name="Test Author", type="producer",
         category="generation", role="test-author", backend="claude_code",
         description="Generates tests (unit + negative paths) for the implementation.",
         tools_policy="coding", skill_refs=["test-driven-development"]),
    dict(id="docs-writer", name="Docs Writer", type="producer",
         category="generation", role="docs-writer", backend="claude_code",
         description="Writes user/dev docs and changelog from the shipped change.",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="migration-writer", name="Migration Writer", type="producer",
         category="generation", role="migration-writer", backend="claude_code",
         description="Writes safe, reversible data/schema migrations.",
         tools_policy="coding", skill_refs=[]),
    # ---- security (verifiers) ----
    dict(id="security-auditor", name="Security Auditor", type="verifier",
         category="security", role="security", backend="claude_code", model="opus",
         description="Audits for OWASP/common vulns; emits findings with severity (no manual pentest).",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="dependency-scanner", name="Dependency Scanner", type="verifier",
         category="security", role="security", backend="claude_code",
         description="Flags vulnerable / outdated dependencies.",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="secrets-scanner", name="Secrets Scanner", type="verifier",
         category="security", role="security", backend="claude_code",
         description="Detects hardcoded secrets / credentials.",
         tools_policy="reasoning", skill_refs=[]),
    # ---- testing (verifiers) ----
    dict(id="qa-runner", name="QA / Test Runner", type="verifier",
         category="testing", role="code-quality", backend="claude_code",
         description="Runs the suite and reports pass/fail + coverage (real runs use an Auto Check node).",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="e2e-tester", name="E2E Tester", type="verifier",
         category="testing", role="code-quality", backend="claude_code",
         description="Authors/checks end-to-end scenarios against acceptance criteria.",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="perf-tester", name="Performance Tester", type="verifier",
         category="testing", role="code-quality", backend="claude_code",
         description="Checks performance budgets / latency against NFRs.",
         tools_policy="reasoning", skill_refs=[]),
    # ---- quality / cross-model verification (LLM-as-judge on a DIFFERENT model) ----
    dict(id="code-quality", name="Code Quality Reviewer", type="verifier",
         category="quality", role="code-quality", backend="claude_code",
         description="Reviews code against standards; emits findings with confidence.",
         tools_policy="reasoning", skill_refs=["dev@review-code"]),
    dict(id="code-reviewer", name="Code Reviewer (cross-model judge)", type="verifier",
         category="quality", role="code-quality", backend="claude_code", model="opus",
         description="Independent review on a DIFFERENT/stronger model (LLM-as-judge): correctness, standards, maintainability.",
         tools_policy="reasoning", skill_refs=["requesting-code-review"]),
    dict(id="spec-conformance-verifier", name="Spec Conformance Verifier", type="verifier",
         category="quality", role="code-quality", backend="claude_code", model="opus",
         description="Independently verifies the output meets the FUNC spec's acceptance criteria (different model).",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="accessibility-auditor", name="Accessibility Auditor", type="verifier",
         category="quality", role="code-quality", backend="claude_code",
         description="Checks WCAG/a11y compliance of UI changes.",
         tools_policy="reasoning", skill_refs=[]),
    dict(id="debugger", name="Debugger", type="producer",
         category="quality", role="debugger", backend="claude_code",
         description="Roots out failures via systematic 4-phase debugging; proposes the fix.",
         tools_policy="coding", skill_refs=[]),
]

PIPELINES = [
    dict(id="sdlc-slice-v0.1", name="SDLC Slice (v0.1 spike)", nodes=[
        dict(id="analyze", agent="requirements-analyst", spec_ref="", max_retries=2),
        dict(id="gate-analysis", type="gate",
             gate=dict(mode="auto", persona="ba"), on_reject_goto="analyze"),
        dict(id="design", agent="solution-architect", max_retries=2),
        dict(id="implement", agent="code-generator", max_retries=2),
        dict(id="verify-quality", agent="code-quality", max_retries=2),
        dict(id="verify-security", agent="security", max_retries=2),
        dict(id="gate-impl", type="gate",
             gate=dict(mode="hybrid", persona="lead-dev",
                       consumes=["verify-quality", "verify-security"],
                       high_cutoff=0.85, low_cutoff=0.5),
             on_reject_goto="implement"),
        dict(id="test", agent="test-author", max_retries=2),
    ]),
    dict(id="sdlc-parallel", name="SDLC — Parallel Checks + Auto-Test", nodes=[
        dict(id="analyze", agent="requirements-analyst"),
        dict(id="design", agent="solution-architect", depends_on=["analyze"]),
        dict(id="implement", agent="code-generator", depends_on=["design"]),
        # three checks run IN PARALLEL off implement:
        dict(id="verify-quality", agent="code-quality", depends_on=["implement"]),
        dict(id="verify-security", agent="security", depends_on=["implement"]),
        dict(id="auto-tests", type="auto_check", check_kind="test_exec", depends_on=["implement"]),
        # gate waits on all three (DAG join):
        dict(id="gate-impl", type="gate", depends_on=["verify-quality", "verify-security", "auto-tests"],
             gate=dict(mode="hybrid", persona="lead-dev",
                       consumes=["verify-quality", "verify-security", "auto-tests"],
                       high_cutoff=0.85, low_cutoff=0.5),
             on_reject_goto="implement"),
        dict(id="ship", agent="test-author", depends_on=["gate-impl"]),
    ]),
    dict(id="sdlc-client-gated", name="SDLC + Client Gate", nodes=[
        dict(id="analyze", agent="requirements-analyst", spec_ref="", max_retries=2),
        dict(id="gate-client", type="gate",
             gate=dict(mode="human", persona="client", reviews=["analyze"], audience="client"),
             on_reject_goto="analyze"),
        dict(id="design", agent="solution-architect", max_retries=2),
        dict(id="implement", agent="code-generator", max_retries=2),
        dict(id="verify-quality", agent="code-quality", max_retries=2),
        dict(id="gate-impl", type="gate",
             gate=dict(mode="hybrid", persona="lead-dev", consumes=["verify-quality"],
                       high_cutoff=0.85, low_cutoff=0.5),
             on_reject_goto="implement"),
        dict(id="test", agent="test-author", max_retries=2),
    ]),
    # demonstrator: planner(strong) -> design+patterns(parallel) -> implement(join)
    #   -> cross-checks(parallel: reviewer on a DIFFERENT model + security + auto-tests)
    #   -> join gate(hybrid) -> docs
    dict(id="sdlc-full-crosschecked", name="SDLC — Full (cross-checked, multi-model)", nodes=[
        dict(id="plan", agent="project-planner"),                       # strong model
        dict(id="design", agent="solution-architect", depends_on=["plan"]),
        dict(id="patterns", agent="codebase-pattern-mapper", depends_on=["plan"]),
        dict(id="implement", agent="code-generator", depends_on=["design", "patterns"]),
        dict(id="review", agent="code-reviewer", depends_on=["implement"]),     # diff model (judge)
        dict(id="security", agent="security-auditor", depends_on=["implement"]),
        dict(id="auto-tests", type="auto_check", check_kind="test_exec", depends_on=["implement"]),
        dict(id="gate-impl", type="gate", depends_on=["review", "security", "auto-tests"],
             gate=dict(mode="hybrid", persona="lead-dev",
                       consumes=["review", "security", "auto-tests"],
                       high_cutoff=0.85, low_cutoff=0.5),
             on_reject_goto="implement"),
        dict(id="docs", agent="docs-writer", depends_on=["gate-impl"]),
    ]),
]


def main() -> int:
    repo_path = sys.argv[1] if len(sys.argv) > 1 else "../../ai-sdlc"
    repo = AISdlcRepo(repo_path)
    for a in AGENTS:
        repo.save_agent(a)
    for p in PIPELINES:
        repo.save_pipeline_def(p)
    print(f"Seeded {len(AGENTS)} agents -> {repo.agents_dir}")
    print(f"Seeded {len(PIPELINES)} pipelines -> {repo.pipelines_dir}")
    # verify build
    for p in PIPELINES:
        built = repo.build_pipeline(repo.get_pipeline_def(p["id"]))
        print(f"  build {built.id}: {len(built.nodes)} nodes "
              f"({sum(1 for n in built.nodes if n.type.value=='gate')} gates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
