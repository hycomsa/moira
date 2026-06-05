import { useEffect, useState } from "react";
import { api, type ActivityRow } from "../api";

export function ActivityPage() {
  const [rows, setRows] = useState<ActivityRow[]>([]);
  useEffect(() => {
    const load = () => api.activity().then((d) => setRows(d.activity)).catch(() => { /* */ });
    load(); const t = setInterval(load, 3000); return () => clearInterval(t);
  }, []);

  return (
    <div className="page">
      <h2>Activity <span className="muted">· recent events across all runs</span></h2>
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
    </div>
  );
}
