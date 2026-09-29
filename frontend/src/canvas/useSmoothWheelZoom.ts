import { useCallback, useEffect, useRef, type RefObject } from 'react';
import { useReactFlow, useStoreApi, type Viewport } from '@xyflow/react';

export const MIN_CANVAS_ZOOM = 0.12;
export const MAX_CANVAS_ZOOM = 2.2;
const ZOOM_DURATION = 180;
const sameViewport = (a: Viewport, b: Viewport) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + Math.abs(a.zoom - b.zoom) < 0.00001;

// Match XYFlow's wheel normalization, including line/page wheels and Mac pinch.
export function wheelZoomTarget(zoom: number, event: Pick<WheelEvent, 'deltaY' | 'deltaMode' | 'ctrlKey'>, mac: boolean) {
  const unit = event.deltaMode === 1 ? 0.05 : event.deltaMode ? 1 : 0.002;
  return Math.max(MIN_CANVAS_ZOOM, Math.min(MAX_CANVAS_ZOOM,
    zoom * 2 ** (-event.deltaY * unit * (event.ctrlKey && mac ? 10 : 1))));
}

export function useSmoothWheelZoom(
  wrapper: RefObject<HTMLDivElement>,
  isScrollable: (target: EventTarget | null, boundary: HTMLElement) => boolean,
  onFinish: (viewport: Viewport) => void,
) {
  const { getViewport, setViewport } = useReactFlow();
  const store = useStoreApi();
  const lastWritten = useRef<Viewport>();
  const finish = useRef(onFinish);
  finish.current = onFinish;
  // XYFlow defers move-end with setTimeout(0). Match the actual write instead
  // of a synchronous flag, without swallowing another control's camera change.
  const consumeMoveEnd = useCallback((viewport: Viewport) => {
    const own = lastWritten.current;
    if (!own || !sameViewport(own, viewport)) return false;
    lastWritten.current = undefined;
    return true;
  }, []);

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let interruptedAt = -Infinity;
    let pending: { from: Viewport; to: Viewport; applied: Viewport; start: number; direction: number } | undefined;
    const stop = () => {
      cancelAnimationFrame(frame); frame = 0;
      if (pending) { pending = undefined; finish.current(getViewport()); }
    };
    const interrupt = () => { interruptedAt = performance.now(); stop(); };
    const apply = (viewport: Viewport) => {
      // Public XYFlow API updates nodes, hit testing and terrain together.
      lastWritten.current = viewport;
      void setViewport(viewport);
    };
    const tick = () => {
      frame = 0;
      if (!pending) return;
      if (!sameViewport(getViewport(), pending.applied)) { stop(); return; }
      // RAF's timestamp may predate a wheel handler when the main thread stalls.
      // Advance by input age; never run an easing curve backwards or restart a
      // fresh 180 ms tail for an event that was already waiting in the queue.
      const progress = Math.max(0, Math.min(1, (performance.now() - pending.start) / ZOOM_DURATION));
      const eased = 1 - (1 - progress) ** 3;
      const { from, to } = pending;
      const next = {
        x: from.x + (to.x - from.x) * eased,
        y: from.y + (to.y - from.y) * eased,
        zoom: from.zoom + (to.zoom - from.zoom) * eased,
      };
      apply(next);
      pending.applied = next;
      if (progress === 1) stop();
      else frame = requestAnimationFrame(tick);
    };
    const wheel = (event: WheelEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const flow = target?.closest('.react-flow');
      if (event.defaultPrevented || flow?.id !== 'oaw-world-map' || !event.deltaY
        || target?.closest('.nowheel, [inert]') || store.getState().userSelectionActive) return;
      if (isScrollable(event.target, element)) { stop(); event.stopPropagation(); return; }
      event.preventDefault();
      event.stopPropagation();
      const now = performance.now();
      const inputTime = event.timeStamp > 0 && event.timeStamp <= now ? event.timeStamp : now;
      if (inputTime < interruptedAt) return;
      const current = getViewport();
      if (pending && !sameViewport(current, pending.applied)) stop();
      // Interrupt an in-flight fit/button transition before taking ownership.
      if (!pending) apply(current);
      const direction = Math.sign(event.deltaY);
      // Reverse from the displayed camera, dropping the previous direction's
      // unfinished animation. Same-direction events keep their original gain.
      const base = pending?.direction === direction ? pending.to.zoom : current.zoom;
      const zoom = wheelZoomTarget(base, event, navigator.userAgent.includes('Mac'));
      const bounds = flow.getBoundingClientRect();
      const x = event.clientX - bounds.left, y = event.clientY - bounds.top;
      const to = { zoom, x: x - (x - current.x) * zoom / current.zoom, y: y - (y - current.y) * zoom / current.zoom };
      if (media.matches) { pending = undefined; apply(to); finish.current(to); return; }
      pending = { from: current, to, applied: current, start: inputTime, direction };
      if (!frame) frame = requestAnimationFrame(tick);
    };
    element.addEventListener('wheel', wheel, { capture: true, passive: false });
    // Direct manipulation or another control takes ownership immediately.
    document.addEventListener('pointerdown', interrupt, true);
    document.addEventListener('keydown', interrupt, true);
    document.addEventListener('visibilitychange', interrupt);
    window.addEventListener('blur', interrupt);
    media.addEventListener('change', interrupt);
    return () => {
      stop();
      element.removeEventListener('wheel', wheel, true);
      document.removeEventListener('pointerdown', interrupt, true);
      document.removeEventListener('keydown', interrupt, true);
      document.removeEventListener('visibilitychange', interrupt);
      window.removeEventListener('blur', interrupt);
      media.removeEventListener('change', interrupt);
    };
  }, [wrapper, isScrollable, getViewport, setViewport, store]);
  return consumeMoveEnd;
}
