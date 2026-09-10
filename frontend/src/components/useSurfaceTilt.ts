import { useEffect, useRef, type PointerEvent } from "react";

/** Measure the stationary hit area; animate only its decorative surface. */
export function useSurfaceTilt(strength = 9) {
  const frame = useRef(0);
  const element = useRef<HTMLElement | null>(null);
  const reduced = useRef(false);
  const reset = () => {
    cancelAnimationFrame(frame.current);
    frame.current = 0;
    for (const property of ["--surface-rx", "--surface-ry", "--light-x", "--light-y"]) element.current?.style.removeProperty(property);
    element.current?.removeAttribute("data-tilting");
  };
  useEffect(() => {
    const preference = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const update = () => { reduced.current = preference?.matches ?? false; if (reduced.current) reset(); };
    update();
    preference?.addEventListener("change", update);
    return () => { cancelAnimationFrame(frame.current); preference?.removeEventListener("change", update); };
  }, []);
  const move = (event: PointerEvent<HTMLElement>) => {
    if (reduced.current || event.pointerType === "touch") return;
    const target = event.currentTarget;
    element.current = target;
    const box = target.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const x = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    const y = Math.max(0, Math.min(1, (event.clientY - box.top) / box.height));
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      target.style.setProperty("--surface-rx", `${(0.5 - y) * strength * 2}deg`);
      target.style.setProperty("--surface-ry", `${(x - 0.5) * strength * 2}deg`);
      target.style.setProperty("--light-x", `${x * 100}%`);
      target.style.setProperty("--light-y", `${y * 100}%`);
      target.setAttribute("data-tilting", "true");
      frame.current = 0;
    });
  };
  return { onPointerMove: move, onPointerLeave: reset, onPointerCancel: reset, onBlur: reset };
}
