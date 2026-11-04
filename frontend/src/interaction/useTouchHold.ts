import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { hapticFeedback, TOUCH_HOLD_DELAY, TOUCH_HOLD_SLOP } from './feedback';

export interface TouchPoint { clientX: number; clientY: number; pointerId: number }
interface Options {
  disabled?: boolean;
  /** Compact card grips also accept a mouse when device emulation is off. */
  allowMouse?: boolean;
  onHold(point: TouchPoint): void;
  onMove?(point: TouchPoint): void;
  onRelease?(point: TouchPoint): void;
  onCancel?(): void;
}
type Phase = 'idle' | 'pending' | 'active';

/** Opt-in recognizer for a non-text touch surface. It owns the pointer only after
 * recognition, then preserves that same pointer through the following drag. */
export function useTouchHold(options: Options) {
  const latest = useRef(options); latest.current = options;
  const [phase, setPhase] = useState<Phase>('idle');
  const gesture = useRef<{ point: TouchPoint; target: HTMLElement; timer: ReturnType<typeof setTimeout>; active: boolean }>();
  const suppressClick = useRef(false);
  const finish = (cancelled: boolean, point?: TouchPoint, mounted = true) => {
    const current = gesture.current;
    if (!current) return;
    gesture.current = undefined;
    clearTimeout(current.timer);
    if (current.target.hasPointerCapture?.(current.point.pointerId)) current.target.releasePointerCapture(current.point.pointerId);
    if (mounted) setPhase('idle');
    if (current.active) {
      if (cancelled) latest.current.onCancel?.(); else latest.current.onRelease?.(point ?? current.point);
    }
  };
  const finishRef = useRef(finish); finishRef.current = finish;

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const current = gesture.current;
      if (!current || event.pointerId !== current.point.pointerId) return;
      if (!current.active) {
        if (Math.hypot(event.clientX - current.point.clientX, event.clientY - current.point.clientY) > TOUCH_HOLD_SLOP) finishRef.current(true);
      } else {
        event.preventDefault(); event.stopPropagation();
        latest.current.onMove?.(event);
      }
    };
    const up = (event: PointerEvent) => {
      const current = gesture.current;
      if (!current || event.pointerId !== current.point.pointerId) return;
      if (current.active) { event.preventDefault(); event.stopPropagation(); }
      finishRef.current(event.type === 'pointercancel', event);
    };
    const otherPointer = (event: PointerEvent) => {
      if (!gesture.current && event.isPrimary) suppressClick.current = false;
      if (gesture.current && event.pointerId !== gesture.current.point.pointerId) finishRef.current(true);
    };
    const cancel = () => finishRef.current(true);
    const visibility = () => { if (document.hidden) cancel(); };
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') cancel(); };
    window.addEventListener('pointermove', move, { capture: true, passive: false });
    window.addEventListener('pointerup', up, true);
    window.addEventListener('pointercancel', up, true);
    window.addEventListener('blur', cancel);
    document.addEventListener('pointerdown', otherPointer, true);
    document.addEventListener('visibilitychange', visibility);
    document.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('pointermove', move, true);
      window.removeEventListener('pointerup', up, true);
      window.removeEventListener('pointercancel', up, true);
      window.removeEventListener('blur', cancel);
      document.removeEventListener('pointerdown', otherPointer, true);
      document.removeEventListener('visibilitychange', visibility);
      document.removeEventListener('keydown', key);
      finishRef.current(true, undefined, false);
    };
  }, []);
  useEffect(() => { if (options.disabled) finishRef.current(true); }, [options.disabled]);

  return {
    phase,
    bind: {
      onPointerDown(event: ReactPointerEvent<HTMLElement>) {
        // A fresh primary press also ends suppression when the browser omitted
        // the previous touch's compatibility click (including on hybrid PCs).
        if (event.isPrimary && event.button === 0) suppressClick.current = false;
        if (latest.current.disabled || event.defaultPrevented || (event.pointerType !== 'touch' && !(latest.current.allowMouse && event.pointerType === 'mouse')) || !event.isPrimary || event.button !== 0) return;
        finishRef.current(true);
        const point = { clientX: event.clientX, clientY: event.clientY, pointerId: event.pointerId };
        const touch = event.pointerType === 'touch';
        const target = event.currentTarget;
        const timer = setTimeout(() => {
          const current = gesture.current;
          if (!current || !target.isConnected) { finishRef.current(true); return; }
          try { target.setPointerCapture?.(point.pointerId); }
          catch { finishRef.current(true); return; }
          current.active = true; suppressClick.current = true;
          setPhase('active'); if (touch) hapticFeedback('hold'); latest.current.onHold(point);
        }, TOUCH_HOLD_DELAY);
        gesture.current = { point, target, timer, active: false };
        setPhase('pending');
      },
      onClickCapture(event: ReactMouseEvent<HTMLElement>) {
        if (!suppressClick.current || event.detail === 0) return;
        suppressClick.current = false; event.preventDefault(); event.stopPropagation();
      },
      onContextMenu(event: ReactMouseEvent<HTMLElement>) {
        if (gesture.current || suppressClick.current) event.preventDefault();
      },
      onLostPointerCapture(event: ReactPointerEvent<HTMLElement>) {
        // Taking capture from a touched child emits its own bubbling loss event.
        // Only losing capture on this recognizer's owner cancels the gesture.
        if (event.target === gesture.current?.target && event.pointerId === gesture.current.point.pointerId) finishRef.current(true);
      },
    },
  };
}
