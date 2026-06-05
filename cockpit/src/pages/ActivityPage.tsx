import { useEffect, useState } from "react";
import { api, type ActivityRow } from "../api";

export function ActivityPage() {
  const [tab, setTab] = useState<"events" | "logs">("events");
  const [rows, setRows] = useState<ActivityRow[]>([]);
  const [logText, setLogText] = useState("");
  const [logPath, setLogPath] = useState<string | null>(null);

  useEffect(() => {
    if (tab === "events") {
      const load = () => api.activity().then((d) => setRows(d.activity)).catch(() => { /* */ });
      load(); const t = setInterval(load, 3000); return () => clearInterval(t);
    }
    const load = () => api.logs().then((d) => { setLogText(d.log); setLogPath(d.path); }).catch(() => { /* */ });
    load(); const t = setInterval(load, 3000); return () => clearInterval(t);
  }, [tab]);

  return (
    <div className="page">
      <h2>Activity <span className="muted">· what the orchestrator is doing</span></h2>
      <div className="toolbar" style={{ gap: 6 }}>
        <button className={"seg-btn" + (tab === "events" ? " on" : "")} onClick={() => setTab("events")}>Events</button>
        <button className={"seg-btn" + (tab === "logs" ? " on" : "")} onClick={() => setTab("logs")}>Sidecar logs</button>
        {tab === "logs" && logPath && <span className="muted small" style={{ marginLeft: "auto" }}>{logPath}</span>}
      </div>

      {tab === "events" ? (
        <div className="panel grow">
          <div className="log">
            {rows.map((e) => (
              <div className={"log-row k-" + e.kind.split(".")[0]} key={`${e.run_id}-${e.seq}`}>
                <span className="kind">{e.kind}</span>
                <span className="run-ref">{e.run_id.replace("run-", "")}</span>
                <span className="lmsg">{e.message}</span>
              </div>
            ))}
            {rows.length === 0 && <div className="empty">No activity yet.</div>}
          </div>
        </div>
      ) : (
        <div className="panel grow">
          <pre className="log" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{logText || "No logs yet."}</pre>
        </div>
      )}
    </div>
  );
}
