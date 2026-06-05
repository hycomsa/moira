// Renders an evaluation scorecard (quality or conformance): an overall score
// ring, per-criterion bars with verdict colours, and a missing-items list.
// Shared by the run drill-down (#1) and the conformance badge (#3).
import type { Scorecard } from "../api";

const pct = (v: number) => Math.round(v * 100);

export function ScoreBadge({ score, label }: { score: number; label?: string }) {
  const v = pct(score);
  const cls = score >= 0.8 ? "sb-pass" : score >= 0.5 ? "sb-warn" : "sb-fail";
  return <span className={"score-badge " + cls} title={label}>{label ? label + " " : ""}{v}%</span>;
}

export function ScorecardView({ sc }: { sc: Scorecard }) {
  if (!sc.parsed && sc.criteria.length === 0) {
    return (
      <div className="scorecard">
        <div className="sc-unparsed">⚠ The evaluator didn't return a readable scorecard.</div>
        {sc.summary && <p className="sc-summary">{sc.summary}</p>}
      </div>
    );
  }
  const cls = sc.overall >= 0.8 ? "sc-pass" : sc.overall >= 0.5 ? "sc-warn" : "sc-fail";
  return (
    <div className="scorecard">
      <div className="sc-head">
        <div className={"sc-ring " + cls} style={{ ["--p" as string]: pct(sc.overall) }}>
          <span>{pct(sc.overall)}<small>%</small></span>
        </div>
        <div className="sc-headtext">
          <div className="sc-kind">{sc.kind === "conformance" ? "Spec ↔ code conformance" : sc.kind === "compliance" ? "Regulatory compliance" : "Quality"}</div>
          {sc.summary && <p className="sc-summary">{sc.summary}</p>}
        </div>
      </div>
      <div className="sc-crit">
        {sc.criteria.map((c, i) => (
          <div className="sc-row" key={i}>
            <span className="sc-name" title={c.note}>{c.name}</span>
            <span className="sc-bar"><span className={"sc-fill v-" + c.verdict} style={{ width: pct(c.score) + "%" }} /></span>
            <span className={"sc-verdict v-" + c.verdict}>{pct(c.score)}%</span>
          </div>
        ))}
      </div>
      {sc.criteria.some((c) => c.note) && (
        <ul className="sc-notes">
          {sc.criteria.filter((c) => c.note).map((c, i) => (
            <li key={i}><b>{c.name}:</b> {c.note}</li>
          ))}
        </ul>
      )}
      {sc.findings && sc.findings.length > 0 && (
        <div className="sc-findings">
          <div className="sc-findings-h">Compliance findings ({sc.findings.length})</div>
          {[...sc.findings].sort((a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity)).map((f, i) => (
            <div className="sc-finding" key={i}>
              <div className="sc-finding-top">
                <span className={"sev sev-" + f.severity}>{f.severity}</span>
                <span className="sc-finding-title">{f.title}</span>
              </div>
              {f.regulation && <div className="sc-finding-reg">{f.regulation}</div>}
              <div className="sc-finding-meta">
                {f.location && <code>{f.location}</code>}
                {f.recommendation && <span className="sc-finding-rec">→ {f.recommendation}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      {sc.missing.length > 0 && (
        <div className="sc-missing">
          <div className="sc-missing-h">{sc.kind === "conformance" ? "Uncovered acceptance criteria" : sc.kind === "compliance" ? "Uncovered requirements" : "Gaps / weak points"}</div>
          <ul>{sc.missing.map((m, i) => <li key={i}>{m}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

const SEV_ORDER = ["BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO"];
