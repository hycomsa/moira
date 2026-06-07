import { useEffect, useRef, useState } from "react";
import { api, getUser, setUser, getWorkspace, getTraceMode, setTraceMode, type InboxItem, type Stats, type Workspace, type TraceMode } from "../api";
import { Input } from "./ui/Input";
import { Select } from "./ui/Select";
import { Button } from "./ui/Button";

const PERSONAS = ["ba", "po", "architect", "lead-dev", "ciso", "client", "engineer"];
const BACKENDS = ["mock", "claude_code", "litellm"];
const MODELS = ["", "opus", "sonnet", "haiku"];
const initials = (n: string) =>
  (n.split(/[\s._@-]+/).filter(Boolean).slice(0, 2).map((s) => s[0]?.toUpperCase()).join("") || "U");

type Health = Awaited<ReturnType<typeof api.health>>;

export function ProfileMenu({ inbox, theme, onTheme, onNavigate }: {
  inbox: InboxItem[]; theme: string; onTheme: (t: string) => void; onNavigate: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [u, setU] = useState(getUser());
  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [wsp, setWsp] = useState<Workspace | null>(null);
  const [traceMode, setTm] = useState<TraceMode>(getTraceMode());
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    api.health().then(setHealth).catch(() => { /* */ });
    api.stats().then(setStats).catch(() => { /* */ });
    api.workspaces().then((d) => setWsp(d.workspaces.find((w) => w.id === getWorkspace()) || null)).catch(() => { /* */ });
  }, [open]);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    if (open) document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  const patch = (p: Partial<typeof u>) => { const nu = { ...u, ...p }; setU(nu); setUser(p); };
  const myGates = inbox.filter((i) => i.persona === u.persona).length;
  const go = (v: string) => { setOpen(false); onNavigate(v); };
  const mobileUrl = `${location.origin}/m`;

  return (
    <div className="profile" ref={ref}>
      <button className="avatar" onClick={() => setOpen((o) => !o)} title={u.name}>{initials(u.name)}</button>
      {open && (
        <div className="profile-pop">
          <div className="pp-head">
            <span className="avatar lg">{initials(u.name)}</span>
            <div><div className="pp-name">{u.name}</div><div className="pp-role">{u.persona}</div></div>
          </div>

          <div className="pp-sec">
            <div className="pp-label">You <span className="muted">· run owner + gate approver in the audit</span></div>
            <label className="pp-field">Display name
              <Input value={u.name} onChange={(e) => patch({ name: e.target.value })} style={{ width: "100%" }} /></label>
            <label className="pp-field">Persona / role
              <Select value={u.persona} onChange={(e) => patch({ persona: e.target.value })} style={{ width: "100%" }}>
                {PERSONAS.map((p) => <option key={p} value={p}>{p}</option>)}</Select></label>
          </div>

          <div className="pp-sec">
            <div className="pp-label">Preferences</div>
            <label className="pp-field">Theme
              <Select value={theme} onChange={(e) => onTheme(e.target.value)} style={{ width: "100%" }}>
                <option value="dark">◐ Dark</option><option value="light">○ Light</option><option value="midnight">● Midnight</option>
              </Select></label>
            <label className="pp-field">Default backend
              <Select value={u.backend} onChange={(e) => patch({ backend: e.target.value })} style={{ width: "100%" }}>
                {BACKENDS.map((b) => <option key={b} value={b}>{b}</option>)}</Select></label>
            <label className="pp-field">Default model
              <Select value={u.model} onChange={(e) => patch({ model: e.target.value })} style={{ width: "100%" }}>
                {MODELS.map((m) => <option key={m} value={m}>{m || "(backend default)"}</option>)}</Select></label>
            <label className="pp-field">Traceability badge
              <Select value={traceMode} onChange={(e) => { const m = e.target.value as TraceMode; setTm(m); setTraceMode(m); }} style={{ width: "100%" }}>
                <option value="both">Both (structural + LLM)</option>
                <option value="structural">Structural only (instant)</option>
                <option value="llm">LLM conformance only</option>
              </Select></label>
          </div>

          <div className="pp-sec pp-rows" onClick={() => go("inbox")} style={{ cursor: "pointer" }}>
            <div className="pp-label">Your decisions</div>
            <div className="pp-row"><span>Waiting for <b>{u.persona}</b></span><span className="pp-pill">{myGates}</span></div>
            <div className="pp-row"><span className="muted">Total pending</span><span className="muted">{inbox.length}</span></div>
          </div>

          {wsp && (
            <div className="pp-sec pp-rows">
              <div className="pp-label">Workspace</div>
              <div className="pp-row"><b>{wsp.name}</b>{stats && <span className="muted">${stats.total_cost_usd} · {stats.total} runs</span>}</div>
              <div className="pp-path" title={wsp.repo_path}>repo: {wsp.repo_path}</div>
              {wsp.code_path && <div className="pp-path" title={wsp.code_path}>code: {wsp.code_path}</div>}
            </div>
          )}

          <div className="pp-sec pp-rows">
            <div className="pp-label">System</div>
            <div className="pp-row"><span className="muted">persistence</span><span>{health?.persistence ?? "…"}</span></div>
            <div className="pp-row"><span className="muted">claude CLI</span><span>{health ? (health.claude ? "✓ available" : "— absent") : "…"}</span></div>
            <div className="pp-row"><span className="muted">backends</span><span>{(health?.backends || []).join(", ") || "…"}</span></div>
          </div>

          <div className="pp-sec">
            <div className="pp-label">Mobile companion</div>
            <a className="ui-btn ui-btn-ghost ui-btn-sm" style={{ textDecoration: "none", width: "100%" }} href={mobileUrl} target="_blank" rel="noreferrer">📱 Open gate inbox on phone</a>
            <div className="pp-path">{mobileUrl}</div>
          </div>

          <div className="pp-foot">
            <Button variant="ghost" size="sm" onClick={() => go("settings")}>Settings</Button>
            <span className="grow1" />
            <span className="muted small">Moira v{health?.version ?? "0.1"} · git-native</span>
          </div>
        </div>
      )}
    </div>
  );
}
