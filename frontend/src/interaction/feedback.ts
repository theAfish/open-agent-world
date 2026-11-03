/** Recognition time in milliseconds; movement tolerance in screen CSS pixels. */
export const TOUCH_HOLD_DELAY = 450;
export const TOUCH_HOLD_SLOP = 10;

const pulses = { selection: 8, hold: 16, confirm: 12 } as const;

/** An optional acknowledgement, never a prerequisite for completing an action. */
export function hapticFeedback(kind: keyof typeof pulses): boolean {
  if (document.hidden || typeof navigator.vibrate !== 'function'
    || matchMedia('(prefers-reduced-motion: reduce)').matches
    || navigator.userActivation?.hasBeenActive === false) return false;
  try { return navigator.vibrate(pulses[kind]); }
  catch { return false; }
}
