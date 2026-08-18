import { useEffect, useRef, useState } from "react";
import type { Workspace } from "../api";

/** Topbar workspace picker with DETAILS: every workspace shows which
 *  AI SDLC (spec) repo and code repo it points at, so you know what you are
 *  switching to before you switch. Replaces the bare <select>, whose options
 *  could only show a name. */
export function WorkspacePicker({ workspaces, activeWs, onSelect, onNew }: {
  workspaces: Workspace[]; activeWs: string;
  onSelect: (id: string) => void; onNew: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    if (open) document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  const active = workspaces.find((w) => w.id === activeWs);
  const pick = (id: string) => { setOpen(false); onSelect(id); };

  return (
    <div className="wsp" ref={ref}>
      <button className="wsp-btn" onClick={() => setOpen((o) => !o)}
        title={active ? `spec: ${active.repo_path}\ncode: ${active.code_path || "—"}` : "Workspace"}>
        <span className="wsp-hex">⬢</span>
        <span className="wsp-name">{active?.name || activeWs}</span>
        <span className="wsp-caret">▾</span>
      </button>
      {open && (
        <div className="wsp-pop">
          <div className="wsp-label">Workspaces <span className="muted">· spec repo + code repo</span></div>
          {workspaces.map((w) => (
            <button key={w.id} className={"wsp-row" + (w.id === activeWs ? " active" : "")}
              onClick={() => pick(w.id)}>
              <div className="wsp-row-head">
                <span className="wsp-hex">⬢</span>
                <span className="wsp-row-name">{w.name}</span>
                {w.id !== w.name && <span className="wsp-id muted">{w.id}</span>}
                {w.id === activeWs && <span className="wsp-check">✓</span>}
              </div>
              <div className="wsp-path" title={w.repo_path}>
                <span className="wsp-k">spec</span><code>{w.repo_path || "—"}</code>
              </div>
              <div className="wsp-path" title={w.code_path || ""}>
                <span className="wsp-k">code</span><code>{w.code_path || "—"}</code>
              </div>
            </button>
          ))}
          <button className="wsp-row wsp-new" onClick={() => { setOpen(false); onNew(); }}>
            + New workspace…
          </button>
        </div>
      )}
    </div>
  );
}
