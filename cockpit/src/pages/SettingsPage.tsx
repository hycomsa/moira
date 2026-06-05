import { useEffect, useState } from "react";
import { api, type SimBuckets } from "../api";

export function SettingsPage() {
  const [high, setHigh] = useState(0.85);
  const [low, setLow] = useState(0.5);
  const [buckets, setBuckets] = useState<SimBuckets | null>(null);

  useEffect(() => {
    const h = Math.max(high, low + 0.01);
    api.simulate(h, low).then((d) => setBuckets(d.buckets)).catch(() => { /* */ });
  }, [high, low]);

  const total = buckets ? buckets.approve.length + buckets.escalate.length + buckets.reject.length : 0;
  const pct = (n: number) => (total ? Math.round((n / total) * 100) : 0);

  return (
    <div className="page">
      <h2>Settings <span className="muted">· gate acceptance (hybrid confidence routing)</span></h2>

      <div className="panel">
        <h3>Confidence thresholds — live preview</h3>
        <p className="muted">Hybrid gates auto-accept high-confidence findings, auto-deny low, and queue the middle band to a human. Tune where the human is actually needed.</p>

        <div className="slider-row">
          <label>High cutoff (≥ auto-accept): <b>{high.toFixed(2)}</b></label>
          <input type="range" min={0.5} max={0.99} step={0.01} value={high}
                 onChange={(e) => setHigh(parseFloat(e.target.value))} />
        </div>
        <div className="slider-row">
          <label>Low cutoff (&lt; auto-deny): <b>{low.toFixed(2)}</b></label>
          <input type="range" min={0.1} max={0.84} step={0.01} value={low}
                 onChange={(e) => setLow(parseFloat(e.target.value))} />
        </div>

        {buckets && (
          <>
            <div className="sim-bar">
              {buckets.approve.map((c, i) => <span key={"a" + i} className="seg approve" title={`approve ${c}`} />)}
              {buckets.escalate.map((c, i) => <span key={"e" + i} className="seg escalate" title={`human ${c}`} />)}
              {buckets.reject.map((c, i) => <span key={"r" + i} className="seg reject" title={`reject ${c}`} />)}
            </div>
            <div className="sim-legend">
              <span className="approve">■ auto-accept {buckets.approve.length} ({pct(buckets.approve.length)}%)</span>
              <span className="escalate">■ → human {buckets.escalate.length} ({pct(buckets.escalate.length)}%)</span>
              <span className="reject">■ auto-deny {buckets.reject.length} ({pct(buckets.reject.length)}%)</span>
            </div>
            <p className="muted small">Simulated over a representative spread of finding confidences (sidecar `simulate_routing`).</p>
          </>
        )}
      </div>

      <div className="panel">
        <h3>Backends</h3>
        <ul className="plain">
          <li><b>mock</b> — deterministic (tests/offline demo)</li>
          <li><b>claude_code</b> — frontier coding/reasoning via the claude CLI under your own login (ADR-004)</li>
          <li><b>litellm</b> — model-agnostic (frontier + local ollama/*), no vendor lock-in (ADR-003)</li>
        </ul>
      </div>
    </div>
  );
}
