# Moira — User Guide

Moira is an **AI-native SDLC cockpit**. It drives AI agents across the whole
software lifecycle — **from shaping intents/requirements/func-specs (before any
code) through to coding, review and QA** — with **configurable human quality
gates**, a **tamper-evident audit trail**, **full traceability**, and
**model-agnostic execution**. You stay in control: agents work autonomously
between the gates you define, and every step is recorded git-natively.

Everything is grounded in your **AI SDLC repo** (the single source of truth) — not
a free-form prompt box.

**Two things you'll do in Moira:**
1. **Discovery / BA work** — shape and refine intents, requirements and func-specs
   *before coding*, by driving your repo's AI SDLC skills (gated & audited).
2. **SDLC runs** — run a pipeline of agents against a func-spec (analyze → design →
   implement → review/test), with gates and a git-native audit/report.

---

## 1. Prerequisites

| Need | For | Check |
|------|-----|-------|
| Python 3.11+ | orchestrator + API sidecar (stdlib core, no deps) | `python3 --version` |
| Node 18+ / npm | building the cockpit UI | `npm --version` |
| `claude` CLI (logged in) | the **real** agent/skill backend | `claude --version` |
| Docker + `psycopg` | only for the central **PostgreSQL** store (optional) | see `orchestrator/PERSISTENCE.md` |
| `cargo` + webkit2gtk | the native **desktop** app (optional) | `cargo --version` |

Default **mock** mode needs nothing beyond Python + Node. Real agent work needs the
`claude` CLI.

## 2. Run it

From `moira-app/`:
```bash
./run-cockpit.sh            # builds the UI, serves it + API on http://127.0.0.1:8765
./run-desktop.sh            # native desktop window (needs cargo + webkit2gtk)

# point at a specific AI SDLC repo:
MOIRA_DB=.moira/moira.sqlite python3 orchestrator/moira_api.py \
  --repo /path/to/ai-sdlc-repo --static cockpit/dist --port 8765
```
Open it → you land on the **Overview** ("mission control"). Left nav:
**Overview · Runs · Inbox · Pipelines · Agents · Discovery · Files · Traceability ·
Activity · Settings**. Top-right: workspace switcher · Inbox badge · **profile menu**.

> **Persistence** is configurable by env (`MOIRA_PRIMARY=sqlite|postgres`,
> `MOIRA_GIT_EXPORT=1` for the git mirror, `MOIRA_PG_DSN=…`). Default is a local
> SQLite file. See `orchestrator/PERSISTENCE.md`.

### Authentication & RBAC

Moira enforces **default-deny RBAC** when authentication is on (`MOIRA_AUTH_MODE`):

| Mode | When | How identity is obtained |
|---|---|---|
| `off` | offline single-user; **desktop (`run-desktop.sh`) uses this** | every caller is treated as a local admin (no enforcement) |
| `local` | **web cockpit (`run-cockpit.sh`) default** | the sidecar self-issues a session token and injects it into the served page — no IdP, no login |
| `oidc` | team / enterprise | a real IdP issues the JWT (verified via JWKS); IdP groups → roles via `MOIRA_OIDC_GROUP_ROLES` |

**Five roles** (separation of duties): **Admin** (everything), **Developer** (launch/edit + approve dev gates: `ba`,`lead-dev`,`architect`,`qa`), **Compliance** (approve `compliance`/`ciso` gates + governance overrides), **Client** (read + approve `client` gates), **Viewer** (read-only). A gate is approvable only by a role that covers its persona; the approver recorded in the audit is the **authenticated identity**, never a value the caller can type. `GET /api/ready` is always public.

## 3. The AI SDLC repo (the heart of Moira)

Moira reads/writes a **git repo** that holds the "what & why & how-we-work" for a
system. Code lives in a *separate* software repo.
```
ai-sdlc-repo/
  .ai/context/
    intent-specs/   INT-*    requirements/  REQ-*    func-specs/  FUNC-*
    adrs/           ADR-*    standards/     coding rules the agents follow
    agents/         agent definitions (YAML)   pipelines/  pipeline definitions (YAML)
  .agents/skills/   the BA/dev/arch skills Moira drives (ba@…, arch@…, dev@…)
```
You don't "import" it — you create a **workspace** pointing at the folder, and
Moira reads it live and writes artifacts/audit back to it (git-native).

## 4. Workspaces & your profile

- **Workspace switcher** (top-right) scopes everything (runs, inbox, specs, agents,
  pipelines) to one project. **+ New workspace…** asks for a name, the **AI SDLC
  repo path**, and an optional **software repo path** (`code_path`, where coding
  agents write). A "Default" workspace exists out of the box.
- **Profile menu** (avatar, top-right): set your **display name + persona/role** —
  this prefills the **owner** of runs you start. The **gate approver** recorded in
  the audit comes from your **authenticated identity** (when auth is on), not a
  free-text field — so an approval cannot be spoofed. Also: theme, default backend/model (prefill
  forms), your pending decisions, workspace paths & spend, system status, and a
  link to the **mobile companion**.

## 5. Discovery / BA — work on the data *before* coding

Open **Discovery**. This drives your repo's AI SDLC skills to author/refine
artifacts (intents, requirements, func-specs, ADRs) — gated, audited, git-native.

- **Run a single skill** — each `ba@…`/`arch@…` skill has a **▶ Run**: give it an
  **input** (topic / REQ-ID / notes path / artifact id) and a **prompt
  elaboration** (specialize how it runs), pick a **review-gate persona**, Run. The
  skill runs in the AI SDLC repo and writes the artifact, then pauses for your
  review in the **Inbox**.
- **Run a discovery pipeline** — presets chain skills with a gate between each:
  *Intent → Requirements → Func-spec*, *Shape → Validate → Fix*, *PO + Architect
  review*. Give a shared **topic** + elaboration and a persona per step. Step 1
  uses the topic; later steps **auto-inherit the artifact id** the previous step
  produced.
- Reviewing in the Inbox, each gate shows a **📄 Authored** chip → click to read the
  produced artifact + its **provenance orbit**. Approve to continue the chain;
  reject to send feedback back and re-run that step.
- **Discovery is a pipeline under the hood** — the chain compiles to an *author → gate* DAG on the same
  engine, so these authoring skills are also available on the **Pipelines** page (e.g. `sdlc-discovery`:
  *Intent → Requirements → Func-spec*, gated at each step) and editable like any pipeline.

## 6. SDLC runs — pipelines against a func-spec

**Runs → ✨ Guided run** (recommended): a wizard grounded in the repo — pick a
**func-spec**, a **pipeline**, a **backend**, review (with a **pre-flight orbit** of
the context the model will analyze), Start. Or use the quick form below it.

**Backends:** `mock` (instant, free, offline — try the flow) · `claude_code` (the
real `claude` CLI under your login; writes code into `code_path`; real cost) ·
`litellm` (model-agnostic / local).

The run streams in **Runs**: the **execution plan** (per-node status), per-step
**audit** (right panel), run **metrics** (model · time · cost · tokens), the
**context orbit** (what was in the model's analysis), and an **🛡 audit verified**
badge (tamper-evident chain). Buttons: **⤓ Report** (git-native Markdown report).

## 7. Build pipelines (Pipelines page)

A modern node editor (your pipelines are YAML in the repo):
- **Palette (left)** — search + grouped agents; **drag** an agent / Gate / Auto-check
  onto the canvas (or click to add).
- **Wire** — drag a node's right port → another's left port (dependencies);
  set a **reject → goto** rework edge; click an edge to delete.
- **Node Settings (right)** — agent, **per-node model + backend override**
  (cross-model wiring), retries, **per-node budget** (timeout / max-turns) for heavy steps;
  for gates: segmented **mode** (auto/hybrid/human/off) + persona + confidence sliders; for
  auto-checks: a shell command **or** a built-in check (`ac_coverage`, `test_exec`, `log_hygiene`).
- **Skill nodes** — a node can drive an AI SDLC **skill** (authoring), not just an agent. Authoring
  pipelines run against the AI SDLC repo; coding pipelines against the code repo.
- **Run vs** picks the func-spec; **Save** writes the YAML; **▶ Run** launches it.
- **Validation** — **Save** and **▶ Run** both validate the pipeline first: unknown
  agents, an unknown built-in check, dangling edges / reject-targets, a gate with no
  config, or a dependency cycle are rejected (`400`) — no silent failure mid-run.

### Governance packs (compliance as enforceable controls)

A **governance pack** is a versioned JSON policy bundle, project-owned in the AI SDLC
repo under `.ai/standards/compliance/packs/` (samples ship in the repo: `gdpr-basic`,
`wcag2.2`, `logs-advanced`). Attach packs to a run (`governance_packs: [id,…]`) and Moira
compiles them onto the pipeline: **deterministic checks** (e.g. `log_hygiene` — sensitive
data in logs = CRITICAL) that **block** the gate on HIGH/CRITICAL, plus an LLM review that
is recorded as *qualitative, advisory* evidence, plus a governance **gate** (approvable
only by the pack's persona — see RBAC). The applied pack `id@version` + content hash is
**sealed into the audit**, and the run report shows a **policy-coverage table**
(passed / failed / waived / N/A). Browse via `GET /api/governance/packs`.

## 8. Gates & the Inbox ("Pending decisions")

Gates are checkpoints. Modes: **auto** (verifier verdict; HIGH/CRITICAL escalate) ·
**hybrid** (confidence-routed: high→accept, low→deny, middle→human) · **human**
(a persona approves) · **off**. A waiting gate appears in the **Inbox** as a
decision card showing:
- a **decision-ready chip** — AC-coverage (`✓ AC 15/15` / `⚠ AC 3/15`) and the latest **⚖ LLM
  conformance** %, so you judge completeness before approving,
- a **verdict banner** (✓ all checks green / ⚠ N checks failing),
- the **checks feeding the gate** (verifier / `AUTO_CHECK` results — `ac_coverage` ensures every AC has a
  task, `test_exec` runs the test suite — failures in red),
- **📄 Authored** artifacts (discovery) and **Proposed changes** (the file diff),
- a **decision note** (recorded in the audit) + **Approve** / **Reject & rework**.

If a step **failed** (e.g. an agent timed out) the run escalates here: the card shows **why** (the
timeout / retry / escalate events) and an **Open run →** link to the full execution plan. A failed
step offers a third decision, **↻ Retry** — it re-runs the step with a fresh attempt budget, and
your note becomes guidance for the next attempt. **Approve** on a failed step accepts the gap
explicitly (the run continues *without* that step's output, recorded in the audit); **Reject**
follows the pipeline's rework edge or ends the run (ADR-013). The same three buttons appear on
the mobile inbox (`/m`).

When a gate **rejects on its own** (auto mode with escalation off, or a hybrid
auto-deny), the rework step is not blind: Moira serializes the blocking findings
(deterministic checks like `test_exec` first, LLM findings after) into the
feedback the producer sees on its next attempt — the same channel your Inbox
"Reject & rework" note uses, recorded in the audit (ADR-009).

Automatic rework is also **bounded**: each gate may issue at most **max_loop**
system rejects (default 3, editable on the gate in the pipeline editor; 0 =
never auto-reject). When the budget is spent, the gate escalates to its persona
instead — the card says *"rework budget exhausted"* and you decide: approve,
redirect, or reject once more (your own rejects are never limited) (ADR-010).

**Client gate**: a business-language approval for a non-technical client (summary +
requirements, never code). Tune hybrid thresholds under **Settings**.

**Backend readiness**: **Settings → Backends** shows live install/login probes
(version, login status, and the copy-paste fix command). Starting a run, a
discovery chain, or an eval on a backend that is definitely unusable (CLI not
installed / logged out) fails immediately with that fix command instead of
failing minutes later inside the run; an *unknown* login state never blocks
(ADR-012).

## 9. Audit, report & traceability

- Every step writes an **audit record**: input · output · tools · decisions ·
  approvals · cost · time · **owner/authenticated subject** + lineage. The per-run
  audit is a **tamper-evident hash chain** (🛡 verified / ⚠ broken) — verify it via
  `GET /api/runs/{id}/verify`. With `MOIRA_GIT_EXPORT=1` the **same sealed records**
  are mirrored to `.moira-runs/<run>/audit/*.json`, so the git artifact a reviewer
  opens carries the chain too (and a silent edit there is detected).
- **⤓ Report** renders a run to git-native Markdown (committed into the repo's
  `.moira-runs/`), incl. the audit chain status (length + head hash), any
  **governance policy-coverage table**, and file diffs.
- **Traceability** — every func-spec ↔ its lineage (INT/REQ/ADR) ↔ the runs that
  targeted it, as a **List** or a **Graph**; click an artifact to read it. The
  **provenance orbit** (also in artifact views and run pre-flight) shows where an
  artifact came from / what's in the model's context.
- **Completeness** — Moira measures **Spec ↔ Tests ↔ Tasks ↔ Code** per func-spec deterministically from
  the repo (ACs decomposed into tasks, ACs covered by a test plan, tasks done), shown as a badge + panel
  on the run and as a **delivery-health dashboard** on **Overview** (per-FUNC decomposed / tested / built).
  An optional **LLM conformance** scorecard (spec ↔ code) sits beside it as a second, qualitative signal.

## 10. Other pages

- **Agents** — browse/create/edit agent definitions (YAML in the repo); default
  backend is `claude_code`. Import collections of Claude Code subagents.
- **Files** — read-only viewer of the **dev repo** or the **AI SDLC repo**
  (expandable tree), with **Open in VS Code** + copy-path.
- **Activity** — the event feed across the workspace. **Overview** — KPIs, pipeline
  status, recent runs, pending decisions, live activity.

## 11. Mobile companion

`/m` is a lightweight **gate inbox** — review the checks/diff and **Approve / Reject**.
Open it at **`http://127.0.0.1:8765/m`** on the machine running Moira.

> **Cross-device/phone access is not available yet.** The sidecar binds `127.0.0.1`
> (loopback) only, so `http://<desktop-ip>:8765/m` is unreachable from a phone today.
> LAN/mobile access needs an explicit bind address **and** real identity
> (`MOIRA_AUTH_MODE=oidc`) — tracked as future work (ADR-008). Auto-handing a session
> token to any device on the network would defeat the auth model, so it is deliberately
> not done.

## 12. Troubleshooting

- **Blank page / "connection refused"** — the sidecar isn't up; check
  `python3 orchestrator/moira_api.py --repo <repo>` starts cleanly.
- **Port 8765 in use** — `pkill -f moira_api` (or `fuser -k 8765/tcp`).
- **Empty Discovery/Agents/Files** — the workspace `repo_path` is wrong, or the
  repo lacks `.agents/skills` / `.ai/context`. Fix it in the workspace.
- **`claude_code` / a skill step fails** — ensure `claude --version` works and
  you're logged in; some framework skills are interactive and may not run cleanly
  headless (Moira escalates the node to a human gate as a safety net) — refine the
  prompt elaboration or use `mock` to dry-run the flow.
- **PostgreSQL** — see `orchestrator/PERSISTENCE.md` (Docker + `pip install
  "psycopg[binary]"` in a venv).

## 13. Glossary

- **Producer / Verifier** — agent that creates an artifact / assesses one (emits findings).
- **Skill** — an AI SDLC framework playbook (`ba@shape-func-spec`, …) Moira drives in the repo.
- **Gate** — a checkpoint (auto/hybrid/human/off). **Confidence routing** — hybrid auto-accepts high, auto-denies low, sends the middle to a human.
- **Lineage / provenance orbit** — the chain of repo artifacts a step derives from, shown radially.
- **Audit record / hash chain** — the per-step facts Moira stores; chained so tampering is detectable.
- **Workspace** — a project = an AI SDLC repo (+ optional code repo).
