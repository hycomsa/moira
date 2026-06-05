import { useCallback, useEffect, useState } from "react";
import { api, setWorkspace, type InboxItem, type Workspace } from "./api";
import { Overview } from "./pages/Overview";
import { RunsPage } from "./pages/RunsPage";
import { InboxPage } from "./pages/InboxPage";
import { PipelinesPage } from "./pages/PipelinesPage";
import { AgentsPage } from "./pages/AgentsPage";
import { SkillsPage } from "./pages/SkillsPage";
import { FilesPage } from "./pages/FilesPage";
import { TraceabilityPage } from "./pages/TraceabilityPage";
import { Select } from "./components/ui/Select";
import { ProfileMenu } from "./components/ProfileMenu";
import { ActivityPage } from "./pages/ActivityPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WorkspaceWizard } from "./components/WorkspaceWizard";
import { Splash } from "./components/Splash";

type View = "overview" | "runs" | "inbox" | "pipelines" | "agents" | "skills" | "files" | "trace" | "activity" | "settings";

const NAV: { id: View; label: string; icon: string }[] = [
  { id: "overview", label: "Overview", icon: "▦" },
  { id: "runs", label: "Runs", icon: "▶" },
  { id: "inbox", label: "Inbox", icon: "⚑" },
  { id: "pipelines", label: "Pipelines", icon: "◇" },
  { id: "agents", label: "Agents", icon: "✦" },
  { id: "skills", label: "Discovery", icon: "❖" },
  { id: "files", label: "Files", icon: "▤" },
  { id: "trace", label: "Traceability", icon: "⇄" },
  { id: "activity", label: "Activity", icon: "≣" },
  { id: "settings", label: "Settings", icon: "⚙" },
];

const initialView = (): View => {
  const v = new URLSearchParams(window.location.search).get("view") as View | null;
  return (["overview", "runs", "inbox", "pipelines", "agents", "skills", "activity", "settings"]
    .includes(v ?? "") ? (v as View) : "overview");
};

export function App() {
  const [view, setView] = useState<View>(initialView);
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWs, setActiveWs] = useState("default");
  const [wsKey, setWsKey] = useState(0); // bump to remount pages on workspace switch
  const [showWizard, setShowWizard] = useState(() => new URLSearchParams(window.location.search).get("wizard") === "1");
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || "dark");

  const changeTheme = (t: string) => {
    document.documentElement.dataset.theme = t;
    localStorage.setItem("moira-theme", t);
    setTheme(t);
  };

  const refreshInbox = useCallback(async () => {
    try { setInbox((await api.inbox()).inbox); } catch { /* sidecar starting */ }
  }, []);

  const loadWorkspaces = useCallback(async () => {
    try { setWorkspaces((await api.workspaces()).workspaces); } catch { /* */ }
  }, []);

  useEffect(() => {
    loadWorkspaces();
    refreshInbox();
    const t = setInterval(refreshInbox, 3000);
    return () => clearInterval(t);
  }, [refreshInbox, loadWorkspaces]);

  const switchWs = (id: string) => {
    if (id === "__new__") { setShowWizard(true); return; }
    setWorkspace(id); setActiveWs(id); setWsKey((k) => k + 1);
  };

  return (
    <div className="app">
      <Splash />
      <header className="topbar">
        <div className="brand">
          <span className="logo">◇</span> Moira
          <span className="tagline">AI-native SDLC cockpit · v0.1</span>
        </div>
        <div className="topbar-right">
          <Select style={{ width: 168 }} value={activeWs} onChange={(e) => switchWs(e.target.value)} title="Workspace">
            {workspaces.map((w) => <option key={w.id} value={w.id}>⬢ {w.name}</option>)}
            <option value="__new__">+ New workspace…</option>
          </Select>
          <div className="inbox-badge" onClick={() => setView("inbox")} style={{ cursor: "pointer" }}>
            Inbox <span className="count">{inbox.length}</span>
          </div>
          <ProfileMenu inbox={inbox} theme={theme} onTheme={changeTheme} onNavigate={(v) => setView(v as View)} />
        </div>
      </header>

      <div className="shell">
        <nav className="nav">
          {NAV.map((n) => (
            <div
              key={n.id}
              className={"nav-item" + (view === n.id ? " active" : "")}
              onClick={() => setView(n.id)}
            >
              <span className="nav-icon">{n.icon}</span>
              <span>{n.label}</span>
              {n.id === "inbox" && inbox.length > 0 && <span className="nav-count">{inbox.length}</span>}
            </div>
          ))}
        </nav>

        <main className="content" key={wsKey}>
          {view === "overview" && <Overview onNavigate={(v) => setView(v as View)} />}
          {view === "runs" && <RunsPage onDecided={refreshInbox} />}
          {view === "inbox" && <InboxPage inbox={inbox} onDecided={refreshInbox} />}
          {view === "pipelines" && <PipelinesPage />}
          {view === "agents" && <AgentsPage />}
          {view === "skills" && <SkillsPage />}
          {view === "files" && <FilesPage />}
          {view === "trace" && <TraceabilityPage />}
          {view === "activity" && <ActivityPage />}
          {view === "settings" && <SettingsPage />}
        </main>
      </div>

      {showWizard && (
        <WorkspaceWizard
          onClose={() => setShowWizard(false)}
          onCreated={async (id) => {
            await loadWorkspaces();
            if (id) { setWorkspace(id); setActiveWs(id); setWsKey((k) => k + 1); }
            setShowWizard(false);
          }}
        />
      )}
    </div>
  );
}
