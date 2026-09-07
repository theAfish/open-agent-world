import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { ActivityPhase } from "./activity";
import "./effects.css";

/** Decorative only: any positioned surface with a border radius can host this. */
export function ActivityGlow({ phase }: { phase: ActivityPhase }) {
  const ref = useRef<HTMLSpanElement>(null);
  const wasActive = useRef(false);
  const [visible, setVisible] = useState(true);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    let intersects = true;
    const onVisibility = () => setVisible(intersects && !document.hidden);
    const observer = new IntersectionObserver(([entry]) => { intersects = entry.isIntersecting; onVisibility(); });
    if (ref.current) observer.observe(ref.current);
    document.addEventListener("visibilitychange", onVisibility);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", onVisibility); };
  }, []);
  useEffect(() => {
    const finished = phase === "completed" && wasActive.current;
    if (["running", "waiting"].includes(phase)) wasActive.current = true;
    else if (phase !== "idle") wasActive.current = false;
    if (!finished) { setFlash(false); return; }
    setFlash(true);
    const timer = setTimeout(() => setFlash(false), 750);
    return () => clearTimeout(timer);
  }, [phase]);
  return <span ref={ref} aria-hidden="true" className="activity-glow" data-phase={phase}
    data-visible={visible} data-finish={flash}>
    {phase === "running" && [0, 1, 2, 3].map((index) => <i key={index} style={{ "--particle": index } as CSSProperties} />)}
  </span>;
}
