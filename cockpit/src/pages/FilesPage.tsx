import { useCallback, useEffect, useState } from "react";
import { api, type FileContent, type FileEntry } from "../api";
import { Button } from "../components/ui/Button";

// Read-only file viewer with a persistent expandable tree (expanding one folder
// does NOT collapse the others). Moira is not an editor — it links out to your
// IDE via vscode://file deep-links.
export function FilesPage() {
  const [root, setRoot] = useState<"code" | "repo">("code");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [children, setChildren] = useState<Record<string, FileEntry[]>>({});
  const [file, setFile] = useState<FileContent | null>(null);
  const [err, setErr] = useState("");

  const loadDir = useCallback(async (path: string, r: "code" | "repo") => {
    setErr("");
    try {
      const d = await api.files(path, r);
      setChildren((c) => ({ ...c, [path]: d.entries }));
    } catch { setErr(`Cannot list ${path || "/"}`); }
  }, []);

  useEffect(() => {
    setExpanded(new Set()); setChildren({}); setFile(null);
    loadDir("", root);
  }, [root, loadDir]);

  const toggle = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else { next.add(path); if (!(path in children)) loadDir(path, root); }
      return next;
    });
  };
  const openFile = async (path: string) => {
    try { setFile(await api.file(path, root)); } catch { setErr("Cannot read file."); }
  };
  const copyPath = () => { if (file) navigator.clipboard?.writeText(file.abs); };

  const renderDir = (dirPath: string, depth: number) => {
    const entries = children[dirPath];
    if (!entries) return null;
    return entries.map((e) => {
      const full = dirPath ? `${dirPath}/${e.name}` : e.name;
      const pad = { paddingLeft: depth * 14 + 6 };
      if (e.type === "dir") {
        const open = expanded.has(full);
        return (
          <div key={full}>
            <div className="file-row dir" style={pad} onClick={() => toggle(full)}>
              <span className="fi">{open ? "▾" : "▸"}</span><span className="fn">{e.name}</span>
            </div>
            {open && renderDir(full, depth + 1)}
          </div>
        );
      }
      return (
        <div key={full} className={"file-row file" + (file?.path === full ? " sel" : "")}
             style={pad} onClick={() => openFile(full)}>
          <span className="fi">·</span><span className="fn">{e.name}</span>
          <span className="fsz">{e.size}</span>
        </div>
      );
    });
  };

  return (
    <div className="cols2 files-page">
      <aside className="c-left">
        <section className="panel">
          <div className="files-roots">
            <button className={root === "code" ? "on" : ""} onClick={() => setRoot("code")}>dev repo</button>
            <button className={root === "repo" ? "on" : ""} onClick={() => setRoot("repo")}>AI SDLC repo</button>
          </div>
          <div className="file-tree">
            {renderDir("", 0)}
            {err && <div className="empty">{err}</div>}
          </div>
        </section>
      </aside>

      <main className="c-center">
        {!file ? <div className="placeholder">Select a file to view (read-only).</div> : (
          <section className="panel grow">
            <div className="file-head">
              <code>{file.path}</code>
              <span className="grow1" />
              <a className="ui-btn ui-btn-ghost ui-btn-sm" href={`vscode://file/${file.abs}`} style={{ textDecoration: "none" }}>↗ Open in VS Code</a>
              <Button variant="ghost" size="sm" onClick={copyPath}>⧉ Copy path</Button>
            </div>
            {file.binary
              ? <div className="empty">Binary file — open it in your editor.</div>
              : <pre className="file-view">{file.text}{file.truncated && "\n… (truncated)"}</pre>}
          </section>
        )}
      </main>
    </div>
  );
}
