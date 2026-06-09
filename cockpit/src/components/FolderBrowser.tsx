import { useEffect, useState } from "react";
import { Modal } from "./Modal";
import { Button } from "./ui/Button";
import { api, type DirListing } from "../api";

// In-app folder picker (web mode + desktop fallback): navigates the sidecar's directory listing.
export function FolderBrowser({ title, start, onPick, onClose }: {
  title: string; start?: string; onPick: (path: string) => void; onClose: () => void;
}) {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [loading, setLoading] = useState(true);
  const load = (path: string) => {
    setLoading(true);
    api.browse(path).then(setListing).catch(() => { /* */ }).finally(() => setLoading(false));
  };
  useEffect(() => { load(start || ""); /* eslint-disable-next-line */ }, []);

  return (
    <Modal eyebrow="Choose a folder" title={title} onClose={onClose}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <span className="grow1" />
        <Button variant="primary" disabled={!listing} onClick={() => { if (listing) { onPick(listing.path); onClose(); } }}>
          Use this folder
        </Button>
      </>}>
      <div className="fb-crumb">
        <code>{listing?.path || "…"}</code>
        {listing?.is_repo && <span className="fb-repo">AI SDLC repo ✓</span>}
      </div>
      <div className="fb-list">
        {listing?.parent && <button className="fb-row fb-up" onClick={() => load(listing.parent!)}>⬆ ..</button>}
        {loading && <div className="muted small" style={{ padding: 8 }}>loading…</div>}
        {!loading && listing?.dirs.map((d) => (
          <button key={d.path} className="fb-row" onClick={() => load(d.path)}
                  onDoubleClick={() => { onPick(d.path); onClose(); }} title="open · double-click to pick">
            <span className="fb-ic">{d.is_repo ? "📦" : "📁"}</span>
            <span className="fb-name">{d.name}</span>
            {d.is_repo && <span className="fb-repo small">repo</span>}
          </button>
        ))}
        {!loading && listing && listing.dirs.length === 0 && <div className="muted small" style={{ padding: 8 }}>No subfolders here.</div>}
      </div>
      <div className="hint" style={{ marginTop: 8 }}>Click a folder to open it · double-click to pick it · or “Use this folder” for the current path.</div>
    </Modal>
  );
}
