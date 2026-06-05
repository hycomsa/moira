// Renders a step's "files changed" list + a collapsible unified diff.
// Shared by the run drill-down (RunsPage) and the gate review (InboxPage).
import { DiffView } from "./DiffView";

interface FileChange {
  path: string;
  status?: string;
  additions?: number | null;
  deletions?: number | null;
}

export function FilesDiff({ output, open = false, codePath }: { output: unknown; open?: boolean; codePath?: string }) {
  const o = (output ?? {}) as Record<string, unknown>;
  const files = (Array.isArray(o.files) ? o.files : []) as FileChange[];
  const patch = typeof o.patch === "string" ? o.patch : "";
  if (files.length === 0) return null;
  return (
    <div className="block">
      <div className="flabel">files changed ({files.length})</div>
      <ul className="files">
        {files.map((f, i) => (
          <li key={i}>
            <span className={"fstat fstat-" + (f.status || "M")}>{f.status || "M"}</span>
            <code>{f.path}</code>
            {f.additions != null && <span className="add">+{f.additions}</span>}
            {f.deletions != null && <span className="del">−{f.deletions}</span>}
            {codePath && <a className="open-ed" title="Open in VS Code"
              href={`vscode://file/${codePath}/${f.path}`}>↗</a>}
          </li>
        ))}
      </ul>
      {patch && (
        <details className="patch" open={open}>
          <summary>diff</summary>
          <DiffView patch={patch} truncated={Boolean(o.truncated)} />
        </details>
      )}
    </div>
  );
}

// True when an audit output carries a captured file diff.
export const hasDiff = (output: unknown): boolean =>
  Array.isArray((output as Record<string, unknown> | null)?.files) &&
  ((output as { files: unknown[] }).files.length > 0);
