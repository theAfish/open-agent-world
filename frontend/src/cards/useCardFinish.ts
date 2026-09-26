import { useCallback, useEffect, useRef, type PointerEvent as ReactPointerEvent } from "react";
import { normalizeCardFinish, type CardFinish } from "./cardFinish";
import type { CardFinishQuality } from "./CardFinishLayer";

const LIGHT_PROPERTIES = [
  "--pointer-x", "--pointer-y", "--card-angle-x", "--card-angle-y",
  "--finish-light-x", "--finish-light-y", "--finish-shift-x", "--finish-shift-y", "--finish-angle", "--finish-active",
];

/** Opt-in tilt for collectible faces; canvas hosts keep their existing geometry. */
export function useCardFinish(finish?: CardFinish, quality: CardFinishQuality = "standard", tilt = false) {
  const active = tilt || (normalizeCardFinish(finish) !== "normal" && quality !== "thumbnail");
  const state = useRef<{
    node: HTMLElement | null;
    bounds: DOMRect | null;
    frame: number | null;
    x: number;
    y: number;
    motion: MediaQueryList | null;
  }>({ node: null, bounds: null, frame: null, x: 0, y: 0, motion: null });

  const reset = useCallback(() => {
    const current = state.current;
    if (current.frame !== null) cancelAnimationFrame(current.frame);
    if (current.node) {
      LIGHT_PROPERTIES.forEach(property => current.node!.style.removeProperty(property));
      current.node.removeAttribute("data-finish-active");
      current.node.removeAttribute("data-card-tilting");
    }
    current.motion?.removeEventListener?.("change", reset);
    window.removeEventListener("scroll", reset, true);
    window.removeEventListener("resize", reset);
    window.removeEventListener("blur", reset);
    current.node = null;
    current.bounds = null;
    current.frame = null;
    current.motion = null;
  }, []);

  useEffect(() => reset, [finish, quality, tilt, reset]);

  const queueLight = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const current = state.current;
    if (!current.node || !current.bounds) return;
    if (current.motion?.matches || event.pointerType === "touch" || event.buttons) { reset(); return; }
    const { left, top, width, height } = current.bounds;
    current.x = Math.max(-1, Math.min(1, 2 * (event.clientX - left) / Math.max(1, width) - 1));
    current.y = Math.max(-1, Math.min(1, 2 * (event.clientY - top) / Math.max(1, height) - 1));
    if (current.frame !== null) return;
    current.frame = requestAnimationFrame(() => {
      current.frame = null;
      if (!current.node) return;
      const { x, y } = current;
      const style = current.node.style;
      style.setProperty("--pointer-x", x.toFixed(3));
      style.setProperty("--pointer-y", y.toFixed(3));
      if (tilt) {
        const strength = quality === "showcase" ? 7 : 5;
        style.setProperty("--card-angle-x", `${(-y * strength).toFixed(2)}deg`);
        style.setProperty("--card-angle-y", `${(x * strength).toFixed(2)}deg`);
        current.node.setAttribute("data-card-tilting", "true");
      }
      // A fixed studio light: only the broad reflection changes with orientation.
      style.setProperty("--finish-light-x", "32%");
      style.setProperty("--finish-light-y", "24%");
      style.setProperty("--finish-shift-x", `${(50 - x * 12).toFixed(2)}%`);
      style.setProperty("--finish-shift-y", `${(50 - y * 12).toFixed(2)}%`);
      style.setProperty("--finish-angle", `${(124 + x * 8 + y * 4).toFixed(2)}deg`);
      style.setProperty("--finish-active", "1");
      current.node.setAttribute("data-finish-active", "true");
    });
  }, [quality, reset, tilt]);

  const enter = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    reset();
    if (!active || event.pointerType === "touch" || event.buttons) return;
    const motion = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    if (motion?.matches) return;
    const current = state.current;
    current.node = event.currentTarget;
    current.bounds = event.currentTarget.getBoundingClientRect();
    current.motion = motion;
    motion?.addEventListener?.("change", reset);
    window.addEventListener("scroll", reset, true);
    window.addEventListener("resize", reset);
    window.addEventListener("blur", reset);
    queueLight(event);
  }, [active, queueLight, reset]);

  return {
    onPointerEnter: active ? enter : undefined,
    onPointerMove: active ? queueLight : undefined,
    onPointerLeave: active ? reset : undefined,
    onPointerCancel: active ? reset : undefined,
  };
}
