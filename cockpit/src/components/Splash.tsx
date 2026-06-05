import { useEffect, useState } from "react";
import { api } from "../api";

/** Full-screen boot splash using the Moira brand image. Stays up until the
 *  sidecar is healthy (with a minimum display time for a polished feel), then
 *  fades out and unmounts. */
export function Splash() {
  const [phase, setPhase] = useState<"show" | "fade" | "gone">("show");

  useEffect(() => {
    const min = new Promise((r) => setTimeout(r, 1100));
    const ready = (async () => {
      for (let i = 0; i < 40; i++) {
        try { await api.health(); return; } catch { await new Promise((r) => setTimeout(r, 250)); }
      }
    })();
    let alive = true;
    Promise.all([min, ready]).then(() => {
      if (!alive) return;
      setPhase("fade");
      setTimeout(() => alive && setPhase("gone"), 650);
    });
    return () => { alive = false; };
  }, []);

  if (phase === "gone") return null;
  return (
    <div className={"splash" + (phase === "fade" ? " fade" : "")}>
      <img className="splash-img" src="/moira.jpg" alt="Moira" />
      <div className="splash-veil" />
      <div className="splash-foot">
        <div className="splash-bar"><span /></div>
        <div className="splash-sub">AI-native SDLC cockpit · starting…</div>
      </div>
    </div>
  );
}
