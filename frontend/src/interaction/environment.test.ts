// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { COMPACT_LAYOUT_QUERY, startInteractionEnvironment, useInteractionEnvironment } from './environment';
import { hapticFeedback } from './feedback';

const queries = new Map<string, EventTarget & { matches: boolean }>();
let stop: (() => void) | undefined;
let viewport: EventTarget & { height: number; offsetTop: number; scale: number };
beforeEach(() => {
  vi.useFakeTimers();
  queries.clear();
  vi.stubGlobal('matchMedia', (query: string) => {
    if (!queries.has(query)) queries.set(query, Object.assign(new EventTarget(), { matches: false }));
    return queries.get(query);
  });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => setTimeout(() => callback(0), 16));
  vi.stubGlobal('cancelAnimationFrame', clearTimeout);
  vi.stubGlobal('innerHeight', 800);
  viewport = Object.assign(new EventTarget(), { height: 800, offsetTop: 0, scale: 1 });
  vi.stubGlobal('visualViewport', viewport);
  vi.stubGlobal('navigator', { maxTouchPoints: 0, vibrate: vi.fn(() => true), userActivation: { hasBeenActive: true } });
});
afterEach(() => { stop?.(); stop = undefined; vi.useRealTimers(); vi.unstubAllGlobals(); });

it('separates compact layout, touch capability and the current input, then removes its listeners', () => {
  stop = startInteractionEnvironment();
  queries.get(COMPACT_LAYOUT_QUERY)!.matches = true;
  queries.get(COMPACT_LAYOUT_QUERY)!.dispatchEvent(new Event('change'));
  vi.runOnlyPendingTimers();
  expect(useInteractionEnvironment.getState()).toMatchObject({ compact: true, touch: false });
  document.dispatchEvent(Object.assign(new Event('pointerdown'), { pointerType: 'touch' }));
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
  expect(useInteractionEnvironment.getState()).toMatchObject({ touch: true, input: 'keyboard' });
  queries.get('(any-pointer: coarse)')!.matches = true;
  queries.get(COMPACT_LAYOUT_QUERY)!.matches = false;
  queries.get(COMPACT_LAYOUT_QUERY)!.dispatchEvent(new Event('change'));
  vi.runOnlyPendingTimers();
  expect(useInteractionEnvironment.getState()).toMatchObject({ compact: false, touch: true });
  const before = useInteractionEnvironment.getState();
  stop(); stop = undefined;
  document.dispatchEvent(Object.assign(new Event('pointerdown'), { pointerType: 'pen' }));
  expect(useInteractionEnvironment.getState()).toBe(before);
  expect(document.documentElement.hasAttribute('data-layout')).toBe(false);
});

it('tracks the keyboard-adjusted visible area without reflowing the layout during browser pinch zoom', () => {
  stop = startInteractionEnvironment();
  viewport.height = 440; viewport.offsetTop = 40;
  viewport.dispatchEvent(new Event('resize'));
  viewport.dispatchEvent(new Event('scroll'));
  vi.runOnlyPendingTimers();
  expect(useInteractionEnvironment.getState()).toMatchObject({ height: 440, top: 40, bottom: 320 });
  expect(document.documentElement.style.getPropertyValue('--app-height')).toBe('440px');
  viewport.scale = 2; viewport.height = 200;
  viewport.dispatchEvent(new Event('resize'));
  vi.runOnlyPendingTimers();
  expect(useInteractionEnvironment.getState()).toMatchObject({ height: 800, top: 0, bottom: 0 });
});

it('uses the window size when VisualViewport is unavailable', () => {
  vi.stubGlobal('visualViewport', undefined);
  stop = startInteractionEnvironment();
  vi.stubGlobal('innerHeight', 390);
  window.dispatchEvent(new Event('resize'));
  vi.runOnlyPendingTimers();
  expect(useInteractionEnvironment.getState().height).toBe(390);
});

it('keeps haptics optional, respects reduced motion and tolerates a rejected API', () => {
  startInteractionEnvironment()();
  expect(hapticFeedback('hold')).toBe(true);
  expect(navigator.vibrate).toHaveBeenCalledWith(16);
  queries.get('(prefers-reduced-motion: reduce)')!.matches = true;
  expect(hapticFeedback('selection')).toBe(false);
  expect(navigator.vibrate).toHaveBeenCalledTimes(1);
  queries.get('(prefers-reduced-motion: reduce)')!.matches = false;
  vi.mocked(navigator.vibrate).mockImplementation(() => { throw new Error('Unavailable'); });
  expect(hapticFeedback('confirm')).toBe(false);
  vi.stubGlobal('navigator', {});
  expect(hapticFeedback('hold')).toBe(false);
});
