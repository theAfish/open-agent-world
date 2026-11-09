import { useLayoutEffect, useRef, type RefObject } from "react";
import { tutorialAllowsDrop } from "../onboarding/interactionGuard";
import type { PaletteDragPayload } from "./dragPayload";
import "./pointerDrag.css";
import { createDiscardPreview, discardProximity } from './discardPreview';

export interface PalettePointerItem {
  payload?: PaletteDragPayload;
  entry: { kind: "node" | "legion" | "legion_instance"; id: string; sourceDeckId?: string };
}
export interface PalettePointerLocation {
  clientX: number;
  clientY: number;
  target: Element;
}
interface DropTarget {
  accepts(item: PalettePointerItem, point: PalettePointerLocation): boolean;
  over?(item: PalettePointerItem, point: PalettePointerLocation): void;
  leave?(): void;
  drop(item: PalettePointerItem, point: PalettePointerLocation): void;
}
const targets = new Map<Element, DropTarget>();

/** Register actual surfaces, so overlays and nested controls retain hit-test ownership. */
export function usePaletteDropTarget(ref: RefObject<HTMLElement>, handlers: DropTarget) {
  const latest = useRef(handlers);
  useLayoutEffect(() => { latest.current = handlers; });
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    targets.set(element, {
      accepts: (item, point) => latest.current.accepts(item, point),
      over: (item, point) => latest.current.over?.(item, point),
      leave: () => latest.current.leave?.(),
      drop: (item, point) => latest.current.drop(item, point),
    });
    return () => { targets.delete(element); };
  }, [ref]);
}

let cancelCurrent: (() => void) | undefined;

/** Only this disposable visual moves. No world node or React position state exists yet. */
export function startPalettePointerDrag(event: PointerEvent, source: HTMLElement, item: PalettePointerItem,
  callbacks: { start(): void; end(): void; cancelClick?(): void; discardTarget?(): HTMLElement | null }): () => void {
  if (event.button !== 0 || !event.isPrimary) return () => {};
  cancelCurrent?.();
  const pointerId = event.pointerId;
  const touch = event.pointerType === 'touch';
  const origin = { x: event.clientX, y: event.clientY };
  let x = origin.x, y = origin.y;
  let offsetX = 0, offsetY = 0;
  let preview: HTMLElement | undefined;
  let shred: ((progress: number) => void) | undefined;
  let discardTarget: HTMLElement | null = null;
  const clearDiscard = () => {
    discardTarget?.removeAttribute('data-discard-near');
    discardTarget?.style.removeProperty('--discard-progress');
    discardTarget = null;
  };
  let active = false, ended = false, frame = 0;
  let hovered: DropTarget | undefined;
  const resolve = () => {
    const target = document.elementFromPoint(x, y);
    if (!target || !tutorialAllowsDrop(target)) return;
    const point = { clientX: x, clientY: y, target };
    for (let element: Element | null = target; element; element = element.parentElement) {
      const destination = targets.get(element);
      if (destination?.accepts(item, point)) return { destination, point };
    }
  };
  const update = () => {
    frame = 0;
    const hit = resolve();
    if (hovered !== hit?.destination) { hovered?.leave?.(); hovered = hit?.destination; }
    hovered?.over?.(item, hit!.point);
    const candidate = callbacks.discardTarget?.() ?? null;
    const box = candidate?.getBoundingClientRect();
    const visible = candidate && box && box.width > 0 && box.height > 0 && tutorialAllowsDrop(candidate)
      && candidate.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2));
    if (discardTarget !== (visible ? candidate : null)) clearDiscard();
    discardTarget = visible ? candidate : null;
    const progress = discardTarget && box ? discardProximity(x, y, box) : 0;
    shred?.(progress);
    discardTarget?.toggleAttribute('data-discard-near', progress > 0);
    discardTarget?.style.setProperty('--discard-progress', String(progress));
    // The source card remains intact; only this disposable preview moves.
    if (preview) preview.style.transform = `translate3d(${x - offsetX}px, ${y - offsetY}px, 0)`;
  };
  const finish = (drop = false) => {
    if (ended) return;
    ended = true;
    cancelAnimationFrame(frame);
    const hit = active && drop && source.isConnected ? resolve() : undefined;
    window.removeEventListener("pointermove", move, true);
    window.removeEventListener("pointerup", up, true);
    window.removeEventListener("pointercancel", cancelPointer, true);
    window.removeEventListener("pointerdown", otherPointer, true);
    window.removeEventListener("keydown", key, true);
    window.removeEventListener("blur", cancel);
    document.removeEventListener("visibilitychange", visibility);
    source.removeEventListener("lostpointercapture", cancelPointer);
    if (source.hasPointerCapture(pointerId)) source.releasePointerCapture(pointerId);
    preview?.remove();
    clearDiscard();
    document.body.classList.remove("is-palette-dragging");
    hovered?.leave?.();
    if (cancelCurrent === cancel) cancelCurrent = undefined;
    try { if (hit) hit.destination.drop(item, hit.point); }
    finally { if (active) callbacks.end(); }
  };
  const cancel = () => finish();
  const cancelPointer = (next: PointerEvent) => { if (next.pointerId === pointerId) cancel(); };
  const otherPointer = (next: PointerEvent) => { if (next.pointerType === 'touch' && next.pointerId !== pointerId) cancel(); };
  const move = (next: PointerEvent) => {
    if (next.pointerId !== pointerId) return;
    if (!source.isConnected || !(next.buttons & 1)) { cancel(); return; }
    x = next.clientX; y = next.clientY;
    if (!active) {
      const dx = x - origin.x, dy = y - origin.y;
      if (Math.hypot(dx, dy) < (touch ? 10 : 5)) return;
      // Horizontal motion belongs to the native card row; only an upward pull
      // starts placement. Never turn browsing/downward dismissal into discard.
      if (touch && (Math.abs(dx) >= Math.abs(dy) || dy >= 0)) {
        callbacks.cancelClick?.(); cancel(); return;
      }
      const visual = source.querySelector<HTMLElement>("[data-deck-visual]");
      if (!visual) { cancel(); return; }
      const box = visual.getBoundingClientRect();
      const width = visual.offsetWidth, height = visual.offsetHeight;
      offsetX = Math.max(0, Math.min(width, origin.x - box.left));
      offsetY = Math.max(0, Math.min(height, origin.y - box.top));
      preview = document.createElement("div");
      preview.className = "component-palette palette-drag-preview";
      preview.setAttribute("aria-hidden", "true");
      preview.style.width = `${width}px`; preview.style.height = `${height}px`;
      const copy = visual.cloneNode(true) as HTMLElement;
      copy.removeAttribute("data-deck-visual");
      copy.style.transform = "none";
      copy.style.width = `${width}px`; copy.style.height = `${height}px`;
      preview.append(copy);
      if (callbacks.discardTarget) shred = createDiscardPreview(preview, copy);
      document.body.append(preview);
      document.body.classList.add("is-palette-dragging");
      active = true;
      if (touch) source.setPointerCapture(pointerId);
      callbacks.start();
    }
    next.preventDefault();
    if (!frame) frame = requestAnimationFrame(update);
  };
  const up = (next: PointerEvent) => {
    if (next.pointerId !== pointerId) return;
    x = next.clientX; y = next.clientY;
    finish(true); // Use the release coordinates even if the next frame has not run.
  };
  const key = (next: KeyboardEvent) => {
    if (next.key === "Escape") { next.preventDefault(); next.stopImmediatePropagation(); cancel(); }
  };
  const visibility = () => { if (document.hidden) cancel(); };
  if (!touch) source.setPointerCapture(pointerId);
  window.addEventListener("pointermove", move, { capture: true, passive: false });
  window.addEventListener("pointerup", up, true);
  window.addEventListener("pointercancel", cancelPointer, true);
  window.addEventListener("pointerdown", otherPointer, true);
  // Cancel the preview before canvas hotkeys see Escape.
  window.addEventListener("keydown", key, true);
  window.addEventListener("blur", cancel);
  document.addEventListener("visibilitychange", visibility);
  source.addEventListener("lostpointercapture", cancelPointer);
  cancelCurrent = cancel;
  return cancel;
}
