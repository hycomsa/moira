// Readable unified-diff renderer: coloring, line-number gutters, per-file
// headers, a context-lines selector (1 / 3 / 8 / full — default 3) that
// collapses long runs of unchanged lines, plus word-wrap, split (side-by-side)
// view, and lightweight syntax highlighting. The captured patch carries
// generous context (git -U8); this trims it down for the reviewer. Falls back
// to a raw <pre> if the text doesn't parse as a unified diff.
import { useMemo, useState, type ReactNode } from "react";

type LineType = "add" | "del" | "ctx" | "meta";
interface DLine { type: LineType; text: string; oldNo?: number; newNo?: number }
interface Hunk { section: string; lines: DLine[] }
interface DFile { path: string; status: string; renamedFrom?: string; hunks: Hunk[] }

const CONTEXT_CHOICES: Array<[string, number]> = [["1", 1], ["3", 3], ["8", 8], ["full", Infinity]];

function parseDiff(patch: string): { files: DFile[]; trailing: string[] } {
  const files: DFile[] = [];
  const trailing: string[] = [];
  let cur: DFile | null = null;
  let hunk: Hunk | null = null;
  let oldNo = 0, newNo = 0;

  for (const raw of patch.split("\n")) {
    if (raw.startsWith("diff --git")) {
      const m = raw.match(/ a\/(.+?) b\/(.+)$/);
      cur = { path: m ? m[2] : raw.slice(11).trim(), status: "M", hunks: [] };
      files.push(cur); hunk = null; continue;
    }
    if (!cur) { if (raw.trim()) trailing.push(raw); continue; }
    if (raw.startsWith("new file")) { cur.status = "A"; continue; }
    if (raw.startsWith("deleted file")) { cur.status = "D"; continue; }
    if (raw.startsWith("rename from")) { cur.renamedFrom = raw.slice(12).trim(); cur.status = "R"; continue; }
    if (raw.startsWith("rename to")) { cur.path = raw.slice(10).trim(); cur.status = "R"; continue; }
    if (raw.startsWith("index ") || raw.startsWith("old mode") || raw.startsWith("new mode") ||
        raw.startsWith("similarity") || raw.startsWith("copy ")) continue;
    if (raw.startsWith("--- ")) { if (raw === "--- /dev/null") cur.status = "A"; continue; }
    if (raw.startsWith("+++ ")) {
      if (raw === "+++ /dev/null") cur.status = "D";
      else { const p = raw.slice(4).trim(); cur.path = p.startsWith("b/") ? p.slice(2) : p; }
      continue;
    }
    if (raw.startsWith("@@")) {
      const m = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
      oldNo = m ? parseInt(m[1], 10) : 0;
      newNo = m ? parseInt(m[2], 10) : 0;
      hunk = { section: m ? m[3].trim() : "", lines: [] };
      cur.hunks.push(hunk); continue;
    }
    if (!hunk) { if (raw.trim()) trailing.push(raw); continue; }
    if (raw.startsWith("+")) { hunk.lines.push({ type: "add", text: raw.slice(1), newNo }); newNo++; }
    else if (raw.startsWith("-")) { hunk.lines.push({ type: "del", text: raw.slice(1), oldNo }); oldNo++; }
    else if (raw.startsWith(" ")) { hunk.lines.push({ type: "ctx", text: raw.slice(1), oldNo, newNo }); oldNo++; newNo++; }
    else if (raw.startsWith("\\")) { hunk.lines.push({ type: "meta", text: raw }); }
    else if (raw === "") { /* trailing-newline artifact from split */ }
    else { hunk.lines.push({ type: "meta", text: raw }); }
  }
  return { files, trailing };
}

// Within a hunk, keep at most `ctx` unchanged lines on each side of a change;
// collapse the rest into a single "gap" marker the reviewer can click to expand.
type Row = { t: "line"; l: DLine } | { t: "gap"; n: number; start: number; key: string };

function layoutHunk(h: Hunk, fi: number, hi: number, ctx: number): Row[] {
  if (!isFinite(ctx)) return h.lines.map((l) => ({ t: "line" as const, l }));
  const n = h.lines.length;
  const keep = new Array(n).fill(false);
  h.lines.forEach((l, i) => {
    if (l.type !== "ctx") {                 // changes + meta always shown
      keep[i] = true;
      for (let d = 1; d <= ctx; d++) { if (i - d >= 0) keep[i - d] = true; if (i + d < n) keep[i + d] = true; }
    }
  });
  const rows: Row[] = [];
  let run = 0;
  for (let i = 0; i < n; i++) {
    if (keep[i]) {
      if (run > 0) { rows.push({ t: "gap", n: run, start: i - run, key: `${fi}-${hi}-${i}` }); run = 0; }
      rows.push({ t: "line", l: h.lines[i] });
    } else { run++; }
  }
  if (run > 0) rows.push({ t: "gap", n: run, start: n - run, key: `${fi}-${hi}-end` });
  return rows;
}

// Resolve a hunk into the items actually shown, expanding any gaps the reviewer opened.
type Item = { t: "line"; l: DLine } | { t: "gap"; n: number; key: string };
function itemsFor(h: Hunk, fi: number, hi: number, ctx: number, expanded: Set<string>): Item[] {
  const out: Item[] = [];
  for (const r of layoutHunk(h, fi, hi, ctx)) {
    if (r.t === "line") out.push(r);
    else if (expanded.has(r.key)) h.lines.slice(r.start, r.start + r.n).forEach((l) => out.push({ t: "line", l }));
    else out.push({ t: "gap", n: r.n, key: r.key });
  }
  return out;
}

// ---- lightweight syntax highlighting (subtle, dependency-free) ------------ //
const C_KW = new Set(("const let var function return if else for while do switch case break continue class " +
  "interface type enum import export from default new async await try catch finally throw extends implements " +
  "public private protected static readonly void null undefined true false this super typeof instanceof " +
  "package struct impl fn pub use match mod trait where").split(" "));
const PY_KW = new Set(("def class return if elif else for while break continue import from as pass raise with " +
  "yield lambda None True False and or not in is global nonlocal try except finally async await del assert").split(" "));
const HASH_LANGS = new Set(["py", "python", "yaml", "yml", "sh", "bash", "zsh", "rb", "toml", "ini", "conf", "cfg", "tf"]);

function langOf(path: string): string { return (path.split(".").pop() || "").toLowerCase(); }

function highlight(text: string, lang: string): ReactNode {
  if (!text) return " ";
  const hash = HASH_LANGS.has(lang);
  const kw = hash ? PY_KW : C_KW;
  const cmt = hash ? "#.*$" : "\\/\\/.*$";
  const re = new RegExp(`(${cmt})|("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|\`(?:[^\`\\\\]|\\\\.)*\`)|(\\b\\d[\\w.]*)|([A-Za-z_$][\\w$]*)`, "g");
  const nodes: ReactNode[] = [];
  let last = 0, k = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1]) nodes.push(<span className="tk-cmt" key={k++}>{m[1]}</span>);
    else if (m[2]) nodes.push(<span className="tk-str" key={k++}>{m[2]}</span>);
    else if (m[3]) nodes.push(<span className="tk-num" key={k++}>{m[3]}</span>);
    else if (m[4]) nodes.push(kw.has(m[4]) ? <span className="tk-kw" key={k++}>{m[4]}</span> : m[4]);
    last = re.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const gut = (v?: number) => (v == null ? "" : String(v));

export function DiffView({ patch, truncated }: { patch: string; truncated?: boolean }) {
  const [ctx, setCtx] = useState(3);
  const [wrap, setWrap] = useState(false);
  const [split, setSplit] = useState(false);
  const [syntax, setSyntax] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const { files, trailing } = useMemo(() => parseDiff(patch), [patch]);

  if (files.length === 0) return <pre className="diff">{patch}</pre>; // not a recognizable diff
  const expand = (key: string) => setExpanded((s) => new Set(s).add(key));
  const code = (text: string, lang: string): ReactNode => (syntax ? highlight(text, lang) : (text === "" ? " " : text));

  const gapBtn = (key: string, n: number, ri: number) => (
    <button className="dv-gap" key={ri} onClick={() => expand(key)} title="Show hidden lines">
      ⋯ {n} unchanged {n === 1 ? "line" : "lines"} — show
    </button>
  );

  const renderUnified = (items: Item[], lang: string) =>
    items.map((it, ri) => it.t === "gap"
      ? gapBtn(it.key, it.n, ri)
      : <DiffRow key={ri} l={it.l} code={code} lang={lang} />);

  const renderSplit = (items: Item[], lang: string) => {
    const out: ReactNode[] = [];
    let dels: DLine[] = [], adds: DLine[] = [], ri = 0;
    const flush = () => {
      const m = Math.max(dels.length, adds.length);
      for (let i = 0; i < m; i++) out.push(<SplitRow key={"s" + ri++} left={dels[i] || null} right={adds[i] || null} code={code} lang={lang} />);
      dels = []; adds = [];
    };
    for (const it of items) {
      if (it.t === "gap") { flush(); out.push(gapBtn(it.key, it.n, ri++)); continue; }
      const l = it.l;
      if (l.type === "del") dels.push(l);
      else if (l.type === "add") adds.push(l);
      else if (l.type === "meta") { flush(); out.push(<div className="dv-row dv-meta" key={"m" + ri++}><span className="dv-code">{l.text}</span></div>); }
      else { flush(); out.push(<SplitRow key={"s" + ri++} left={l} right={l} code={code} lang={lang} />); }
    }
    flush();
    return out;
  };

  const cls = "dv" + (wrap ? " dv-wrapped" : "") + (split ? " dv-split" : "");
  return (
    <div className={cls}>
      <div className="dv-bar">
        <span className="dv-lbl">context lines</span>
        <div className="dv-ctxsel">
          {CONTEXT_CHOICES.map(([label, v]) => (
            <button key={label} className={ctx === v ? "on" : ""} onClick={() => setCtx(v)}>{label}</button>
          ))}
        </div>
        <div className="dv-toggles">
          <button className={wrap ? "on" : ""} onClick={() => setWrap((w) => !w)} title="Wrap long lines">⤶ wrap</button>
          <button className={split ? "on" : ""} onClick={() => setSplit((s) => !s)} title="Side-by-side view">◫ split</button>
          <button className={syntax ? "on" : ""} onClick={() => setSyntax((s) => !s)} title="Syntax highlighting">Aa</button>
        </div>
        {truncated && <span className="dv-trunc">diff truncated</span>}
      </div>
      {files.map((f, fi) => {
        const lang = langOf(f.path);
        return (
          <div className="dv-file" key={fi}>
            <div className="dv-fhead">
              <span className={"fstat fstat-" + f.status}>{f.status}</span>
              <code>{f.path}</code>
              {f.renamedFrom && <span className="dv-ren">← {f.renamedFrom}</span>}
            </div>
            <div className="dv-table">
              {f.hunks.map((h, hi) => {
                const items = itemsFor(h, fi, hi, ctx, expanded);
                return (
                  <div key={hi}>
                    {(hi > 0 || h.section) && <div className="dv-hunk">{h.section || "⋯"}</div>}
                    {split ? renderSplit(items, lang) : renderUnified(items, lang)}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
      {trailing.length > 0 && (
        <div className="dv-table"><div className="dv-row dv-meta"><span className="dv-code">{trailing.join("\n")}</span></div></div>
      )}
    </div>
  );
}

type CodeFn = (text: string, lang: string) => ReactNode;

function DiffRow({ l, code, lang }: { l: DLine; code: CodeFn; lang: string }) {
  if (l.type === "meta") return <div className="dv-row dv-meta"><span className="dv-code">{l.text}</span></div>;
  const sign = l.type === "add" ? "+" : l.type === "del" ? "−" : " ";
  return (
    <div className={"dv-row dv-" + l.type}>
      <span className="dv-gut">{gut(l.oldNo && l.type !== "add" ? l.oldNo : undefined)}</span>
      <span className="dv-gut">{gut(l.newNo && l.type !== "del" ? l.newNo : undefined)}</span>
      <span className="dv-sign">{sign}</span>
      <span className="dv-code">{code(l.text, lang)}</span>
    </div>
  );
}

function SplitRow({ left, right, code, lang }: { left: DLine | null; right: DLine | null; code: CodeFn; lang: string }) {
  return (
    <div className="dv-srow">
      {side(left, "old", code, lang)}
      {side(right, "new", code, lang)}
    </div>
  );
}

function side(l: DLine | null, which: "old" | "new", code: CodeFn, lang: string): ReactNode {
  if (!l) return <div className="dv-side dv-empty" />;
  const cls = l.type === "ctx" ? "dv-ctx" : which === "old" ? "dv-del" : "dv-add";
  const sign = l.type === "ctx" ? " " : which === "old" ? "−" : "+";
  const no = which === "old" ? l.oldNo : l.newNo;
  return (
    <div className={"dv-side " + cls}>
      <span className="dv-gut">{gut(no)}</span>
      <span className="dv-sign">{sign}</span>
      <span className="dv-code">{code(l.text, lang)}</span>
    </div>
  );
}
