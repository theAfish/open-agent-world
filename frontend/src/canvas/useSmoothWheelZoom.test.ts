import { describe, expect, it } from 'vitest';
import { wheelZoomTarget } from './useSmoothWheelZoom';

describe('wheel zoom compatibility', () => {
  it('preserves pixel, line, page and Mac pinch strength', () => {
    const wheel = (deltaY: number, deltaMode: number, ctrlKey = false) => ({ deltaY, deltaMode, ctrlKey });
    expect(wheelZoomTarget(1, wheel(-120, 0), false)).toBeCloseTo(2 ** 0.24);
    expect(wheelZoomTarget(1, wheel(-3, 1), false)).toBeCloseTo(2 ** 0.15);
    expect(wheelZoomTarget(1, wheel(-1, 2), false)).toBe(2);
    expect(wheelZoomTarget(1, wheel(-12, 0, true), true)).toBeCloseTo(2 ** 0.24);
    expect(wheelZoomTarget(1, wheel(-12, 0, true), false)).toBeCloseTo(2 ** 0.024);
  });
  it('clamps at the existing limits and reverses immediately from a limit', () => {
    expect(wheelZoomTarget(1, { deltaY: -10000, deltaMode: 0, ctrlKey: false }, false)).toBe(2.2);
    expect(wheelZoomTarget(1, { deltaY: 10000, deltaMode: 0, ctrlKey: false }, false)).toBe(0.12);
    expect(wheelZoomTarget(2.2, { deltaY: 120, deltaMode: 0, ctrlKey: false }, false)).toBeCloseTo(2.2 / 2 ** 0.24);
  });
});
