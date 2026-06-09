// Typed client for the Moira sidecar API.

export interface RunSummary {
  run_id: string;
  pipeline_id: string;
  owner: string;
  status: string;
  created_at: number;
  updated_at: number;
  // rollup metrics (added by /api/runs)
  usd?: number;
  tokens?: number;
  duration?: number;
  model?: string;
}
export type RunMetrics = { run_id: string; status: string; usd?: number; tokens?: number; duration?: number; model?: string };

export interface EventRow {
  seq: number;
  kind: string;
  node_id: string;
  message: string;
  ts: number;
}

export interface AuditRow {
  step_id: string;
  node_id: string;
  node_name: string;
  owner: string;
  status: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  tools: string[];
  decisions: string[];
  approvals: { decision: string; by: string; confirmed: string; feedback?: string }[];
  cost: { tokens_in?: number; tokens_out?: number; usd?: number };
  duration: number;
  lineage: string[];
}

export interface PipelineNode {
  id: string;
  name: string;
  type: string;
  gate?: { mode: string; persona: string } | null;
}

export interface RunDetail {
  run: RunSummary;
  pipeline: { id: string; name: string; nodes: PipelineNode[] };
  events: EventRow[];
  audit: AuditRow[];
  cost: { tokens_in: number; tokens_out: number; usd: number };
  state?: Record<string, string>;  // live per-node status (pending/running/succeeded/waiting_gate/…)
}

export interface InboxItem {
  run_id: string;
  owner: string;
  message: string;
  node_id: string;
  persona?: string;
  audience?: string;
  consumes?: string[];
  review?: Record<string, Record<string, unknown>>;
  gate_review?: {
    func_id: string;
    coverage?: { level: string; ac: { total: number; in_tasks: number; done: number; tested: number }; tasks: { total: number; done: number } } | null;
    conformance?: { overall: number } | null;
  } | null;
}

// Under the Tauri shell the frontend is served from the embedded asset protocol,
// so relative /api would not reach the Python sidecar — use an absolute base.
// In the browser (vite dev or python-served) same-origin "" works (CORS is open).
export const isTauri =
  typeof window !== "undefined" &&
  ("__TAURI_INTERNALS__" in window || window.location.hostname === "tauri.localhost");
const BASE = isTauri ? "http://127.0.0.1:8765" : "";

// Native OS folder dialog (desktop only): invokes our Rust `pick_folder` command. The cockpit
// loads a remote origin, so this only works when remote IPC is enabled in the Tauri build;
// THROWS when unavailable so the caller falls back to the in-app browser (web + desktop fallback).
export async function nativePickFolder(title = "Select folder"): Promise<string | null> {
  const internals = (typeof window !== "undefined" ? (window as unknown as { __TAURI_INTERNALS__?: { invoke?: (c: string, a: unknown) => Promise<unknown> } }).__TAURI_INTERNALS__ : undefined);
  if (!internals?.invoke) throw new Error("native IPC unavailable");
  const res = await internals.invoke("pick_folder", { title });
  return typeof res === "string" && res ? res : null;
}

export interface DirEntry { name: string; path: string; is_repo: boolean; }
export interface DirListing { path: string; parent: string | null; dirs: DirEntry[]; is_repo: boolean; }
const u = (p: string) => `${BASE}${p}`;

// active workspace (multi-workspace scoping)
let activeWs = "default";
export const setWorkspace = (id: string) => { activeWs = id; };
export const getWorkspace = () => activeWs;
const ws = (p: string) => `${BASE}${p}${p.includes("?") ? "&" : "?"}ws=${encodeURIComponent(activeWs)}`;

// local user profile (single-user app; persisted in localStorage, no real auth).
// Drives run `owner` and gate-approval `by` so the audit reflects the real person.
export interface UserProfile { name: string; persona: string; backend: string; model: string; }
const DEFAULT_USER: UserProfile = { name: "tomasz.skonieczny", persona: "lead-dev", backend: "mock", model: "" };
let _user: UserProfile = (() => {
  try { return { ...DEFAULT_USER, ...JSON.parse(localStorage.getItem("moira-user") || "{}") }; }
  catch { return { ...DEFAULT_USER }; }
})();
export const getUser = (): UserProfile => _user;
export const setUser = (u: Partial<UserProfile>) => {
  _user = { ..._user, ...u };
  try { localStorage.setItem("moira-user", JSON.stringify(_user)); } catch { /* */ }
};
export const approver = () => `${_user.name} (${_user.persona})`;

const j = async (r: Response) => {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
};

const POST = (p: string, body: unknown) =>
  fetch(u(p), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(j);

const DELETE = (p: string) =>
  fetch(ws(p), { method: "DELETE" }).then(j);

export interface AgentDef {
  id: string; name: string; type: string; category: string; role?: string;
  backend: string; model?: string; description?: string;
  tools_policy?: string; system_prompt?: string; skill_refs?: string[];
}
export interface PipelineNodeDef {
  id: string; agent?: string; type?: string; name?: string; spec_ref?: string;
  max_retries?: number; on_reject_goto?: string | null;
  depends_on?: string[]; check_cmd?: string;
  backend?: string; model?: string;   // per-node overrides (cross-model wiring)
  gate?: { mode: string; persona?: string; consumes?: string[]; reviews?: string[];
           audience?: string; high_cutoff?: number; low_cutoff?: number };
}
export interface PipelineDefRaw { id: string; name: string; nodes: PipelineNodeDef[]; }

export interface Stats {
  total: number; succeeded: number; waiting_gate: number;
  failed: number; running: number; total_cost_usd: number;
}
export interface Skill {
  name: string; group: string; description: string; source: string; status: string;
}
export interface PipelineDef {
  id: string; name: string;
  nodes: { id: string; name: string; type: string; role?: string; depends_on?: string[];
           gate?: { mode: string; persona: string } | null; on_reject_goto?: string | null }[];
}
export interface ActivityRow {
  seq: number; run_id: string; kind: string; node_id: string; message: string; ts: number;
}
export interface SimBuckets { approve: number[]; escalate: number[]; reject: number[]; }
export interface Workspace { id: string; name: string; repo_path: string; code_path: string | null; }
export interface FuncSpec { id: string; title: string; lineage: string[]; }
export interface Completeness {
  func_id: string; has_epic: boolean;
  tasks: { total: number; done: number; by_status: Record<string, number> };
  ac: { total: number; in_tasks: number; done: number; tested: number };
  build_pct: number; level: "complete" | "partial" | "none";
}
export interface TraceFunc { id: string; title: string; lineage: string[]; runs: RunMetrics[]; completeness?: Completeness; conformance?: { overall: number } | null; }
export interface Traceability {
  available: boolean; func_id: string | null;
  spec?: { present: boolean; title: string | null };
  tests?: { present: boolean; ac_covered: number; ac_total: number };
  tasks?: Completeness | null;
  lineage?: { present: boolean; refs: string[]; resolved: number };
  conformance?: { run_id: string; overall: number; summary: string; criteria: ScoreCriterion[]; missing: string[]; parsed: boolean } | null;
}
export interface ReportResult { markdown: string; committed: boolean; path: string | null; }
export interface ChainStatus { ok: boolean; sealed: boolean; length: number; broken_at: number | null; head: string; }
export interface Artifact { id: string; type: string; title: string; text: string; lineage?: string[]; }
export interface ScoreCriterion { name: string; score: number; verdict: "pass" | "warn" | "fail"; note: string; }
export type Severity = "BLOCKER" | "HIGH" | "MEDIUM" | "LOW" | "INFO";
export interface Finding { severity: Severity; title: string; regulation: string; location: string; recommendation: string; }
export interface Scorecard {
  kind: string; criteria: ScoreCriterion[]; overall: number;
  missing: string[]; findings?: Finding[]; summary: string; parsed: boolean;
}
export interface EvalResult { run_id: string; status: string; kind: string; scorecard: Scorecard; }
export interface Regulation { id: string; title: string; triggers: string[]; severity_policy: string; }
export interface SpendRollup {
  total_usd: number; runs: number; month: string; month_usd: number;
  by_model: { label: string; usd: number }[];
  by_owner: { label: string; usd: number }[];
}
export interface FileEntry { name: string; type: "dir" | "file"; size: number; }
export interface DirListing { root: string; path: string; entries: FileEntry[]; }
export interface FileContent { path: string; abs: string; binary: boolean; text: string; truncated: boolean; }

export const api = {
  health: (): Promise<{ ok: boolean; backends: string[]; repo: string | null; persistence?: string; claude?: boolean; version?: string }> =>
    fetch(u("/api/health")).then(j),
  workspaces: (): Promise<{ workspaces: Workspace[] }> => fetch(u("/api/workspaces")).then(j),
  createWorkspace: (name: string, repo: string, code?: string) =>
    POST("/api/workspaces", { name, repo, code }),
  cloneWorkspace: (name: string, url: string, dest: string) =>
    POST("/api/workspaces/clone", { name, url, dest }),
  funcs: (): Promise<{ funcs: FuncSpec[] }> => fetch(ws("/api/funcs")).then(j),
  traceability: (): Promise<{ funcs: TraceFunc[] }> => fetch(ws("/api/traceability")).then(j),
  runs: (): Promise<{ runs: RunSummary[] }> => fetch(ws("/api/runs")).then(j),
  run: (id: string): Promise<RunDetail> => fetch(u(`/api/runs/${id}`)).then(j),
  report: (id: string): Promise<ReportResult> => POST(`/api/runs/${id}/report`, {}),
  verify: (id: string): Promise<ChainStatus> => fetch(u(`/api/runs/${id}/verify`)).then(j),
  runDiscovery: (body: { skill: string; input?: string; elaboration?: string; persona?: string }):
    Promise<{ run_id: string; status: string }> => POST("/api/discovery", { owner: _user.name, ...body, workspace_id: activeWs }),
  runDiscoveryPipeline: (steps: { skill: string; input?: string; elaboration?: string; persona?: string }[], name?: string):
    Promise<{ run_id: string; status: string }> => POST("/api/discovery", { owner: _user.name, steps, name, workspace_id: activeWs }),
  artifact: (id: string): Promise<Artifact> => fetch(ws(`/api/artifact/${id}`)).then(j),
  files: (path = "", root = "code"): Promise<DirListing> =>
    fetch(ws(`/api/files?path=${encodeURIComponent(path)}&root=${root}`)).then(j),
  file: (path: string, root = "code"): Promise<FileContent> =>
    fetch(ws(`/api/file?path=${encodeURIComponent(path)}&root=${root}`)).then(j),
  inbox: (): Promise<{ inbox: InboxItem[] }> => fetch(ws("/api/inbox")).then(j),
  stats: (): Promise<Stats> => fetch(ws("/api/stats")).then(j),
  skills: (): Promise<{ skills: Skill[] }> => fetch(ws("/api/skills")).then(j),
  pipelines: (): Promise<{ pipelines: PipelineDef[] }> => fetch(ws("/api/pipelines")).then(j),
  agents: (): Promise<{ agents: AgentDef[] }> => fetch(ws("/api/agents")).then(j),
  saveAgent: (def: AgentDef) => POST("/api/agents", { ...def, workspace_id: activeWs }),
  deleteAgent: (id: string) => DELETE(`/api/agents/${id}`),
  importAgents: (source_dir: string) => POST("/api/agents/import", { source_dir, workspace_id: activeWs }),
  pipelineDef: (id: string): Promise<PipelineDefRaw> => fetch(ws(`/api/pipelines/${id}`)).then(j),
  savePipeline: (def: PipelineDefRaw) => POST("/api/pipelines", { ...def, workspace_id: activeWs }),
  deletePipeline: (id: string) => DELETE(`/api/pipelines/${id}`),
  activity: (): Promise<{ activity: ActivityRow[] }> => fetch(ws("/api/activity")).then(j),
  simulate: (high_cutoff: number, low_cutoff: number): Promise<{ buckets: SimBuckets }> =>
    POST("/api/gate/simulate", { high_cutoff, low_cutoff }),
  start: (body: { func_id: string; pipeline_id?: string; pipeline?: string; backend?: string; owner?: string; analysis_gate?: string; impl_gate?: string }) =>
    POST("/api/runs", { owner: _user.name, ...body, workspace_id: activeWs }),
  rerun: (id: string): Promise<{ run_id: string; status: string }> =>
    POST(`/api/runs/${id}/rerun`, { owner: _user.name }),
  approve: (id: string, by: string, confirm: string) =>
    POST(`/api/runs/${id}/approve`, { by, confirm }),
  reject: (id: string, by: string, feedback: string) =>
    POST(`/api/runs/${id}/reject`, { by, feedback }),
  // Evals & quality harness: an evaluation is itself an audited one-node run.
  evalQuality: (run_id: string, model?: string): Promise<EvalResult> =>
    POST("/api/eval", { owner: _user.name, kind: "quality", run_id, model, workspace_id: activeWs }),
  evalConformance: (func_id: string, model?: string): Promise<EvalResult> =>
    POST("/api/eval", { owner: _user.name, kind: "conformance", func_id, model, workspace_id: activeWs }),
  evalCompliance: (references: string[], opts?: { func_id?: string; model?: string }): Promise<EvalResult> =>
    POST("/api/eval", { owner: _user.name, kind: "compliance", references, ...opts, workspace_id: activeWs }),
  regulations: (): Promise<{ regulations: Regulation[] }> => fetch(ws("/api/regulations")).then(j),
  spend: (): Promise<SpendRollup> => fetch(ws("/api/spend")).then(j),
  logs: (tail = 400): Promise<{ path: string | null; log: string }> =>
    fetch(u(`/api/logs?tail=${tail}`)).then(j),
  liveRun: (id: string, from = 0): Promise<LiveState> =>
    fetch(u(`/api/runs/${id}/live?from=${from}`)).then(j),
  debugBundle: (id: string): Promise<Record<string, unknown>> =>
    fetch(u(`/api/runs/${id}/debug`)).then(j),
  runTraceability: (id: string): Promise<Traceability> =>
    fetch(u(`/api/runs/${id}/traceability`)).then(j),
  browse: (path = ""): Promise<DirListing> =>
    fetch(u(`/api/browse?path=${encodeURIComponent(path)}`)).then(j),
};

// Traceability badge mode (client-side, like the profile). "structural" is instant/deterministic;
// "llm" reuses the conformance scorecard. Default: both.
export type TraceMode = "structural" | "llm" | "both";
export const getTraceMode = (): TraceMode => {
  try { return (localStorage.getItem("moira-trace-mode") as TraceMode) || "both"; } catch { return "both"; }
};
export const setTraceMode = (m: TraceMode) => {
  try { localStorage.setItem("moira-trace-mode", m); } catch { /* */ }
};

export interface LiveRecord { t: number; node: string; kind: string; text: string; tokens_in?: number; tokens_out?: number; }
export interface LiveState {
  events: LiveRecord[]; next: number; tokens_in: number; tokens_out: number;
  elapsed: number; active_node: string | null; status: string;
}

// per-workspace monthly budget (alert-only v1; kept client-side like the profile)
export const getBudget = (): number => {
  try { return Number(localStorage.getItem(`moira-budget-${activeWs}`)) || 0; } catch { return 0; }
};
export const setBudget = (usd: number) => {
  try { localStorage.setItem(`moira-budget-${activeWs}`, String(usd || 0)); } catch { /* */ }
};
