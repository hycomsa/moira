import { marked } from "marked";

// Render trusted (repo-/report-generated) Markdown to HTML. Content is local & first-party
// (run reports, repo artifacts) — not untrusted user input.
export function Markdown({ md }: { md: string }) {
  const html = marked.parse(md || "", { async: false, breaks: true, gfm: true }) as string;
  return <div className="md-body" dangerouslySetInnerHTML={{ __html: html }} />;
}
