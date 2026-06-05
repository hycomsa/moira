import { useState } from "react";
import { Modal } from "./Modal";
import { api } from "../api";
import { Button } from "./ui/Button";

export function WorkspaceWizard({ onClose, onCreated }: {
  onClose: () => void; onCreated: (id: string) => void;
}) {
  const [mode, setMode] = useState<"existing" | "clone">("existing");
  const [name, setName] = useState("");
  const [repo, setRepo] = useState("");
  const [code, setCode] = useState("");
  const [url, setUrl] = useState("");
  const [dest, setDest] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const create = async () => {
    if (!name.trim()) { setErr("Workspace name is required."); return; }
    setBusy(true); setErr("");
    try {
      const res = mode === "clone"
        ? await api.cloneWorkspace(name, url, dest)
        : await api.createWorkspace(name, repo, code || undefined);
      const ws = (res as { workspace?: { id: string }; id?: string });
      onCreated(ws.workspace?.id || ws.id || "");
    } catch (e) {
      setErr(String((e as Error)?.message || e)); setBusy(false);
    }
  };

  return (
    <Modal eyebrow="New workspace" title="Connect a project" onClose={onClose}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <span className="grow1" />
        <Button variant="primary" disabled={busy} onClick={create}>
          {busy ? "Working…" : "Create workspace"}
        </Button>
      </>}>
      <div className="card-row">
        <div className={"opt-card" + (mode === "existing" ? " sel" : "")} onClick={() => setMode("existing")}>
          <div className="oc-icon">⬢</div>
          <div className="oc-title">Use existing</div>
          <div className="oc-sub">Point at an AI SDLC repo you already have</div>
        </div>
        <div className={"opt-card" + (mode === "clone" ? " sel" : "")} onClick={() => setMode("clone")}>
          <div className="oc-icon">⎘</div>
          <div className="oc-title">Clone from Git</div>
          <div className="oc-sub">Clone an AI SDLC repo from a URL</div>
        </div>
      </div>

      <div className="field-lg">
        <label>Workspace name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Bank Portal" autoFocus />
      </div>

      {mode === "existing" ? (
        <>
          <div className="field-lg">
            <label>AI SDLC repo path</label>
            <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="/path/to/your/ai-sdlc-repo" />
            <div className="hint">Folder containing <code>.ai/context</code> (intents, specs, agents, pipelines).</div>
          </div>
          <div className="field-lg">
            <label>Software repo path <span className="muted">(optional)</span></label>
            <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="where agents write code" />
            <div className="hint">Leave empty for analysis-only / mock runs.</div>
          </div>
        </>
      ) : (
        <>
          <div className="field-lg">
            <label>Git URL</label>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://… or git@…" />
          </div>
          <div className="field-lg">
            <label>Destination folder</label>
            <input value={dest} onChange={(e) => setDest(e.target.value)} placeholder="/path/to/clone/into" />
            <div className="hint">The repo is cloned here, then registered as this workspace.</div>
          </div>
        </>
      )}

      {err && <div style={{ color: "#f85149", fontSize: 12, marginTop: 6 }}>{err}</div>}
    </Modal>
  );
}
