// @vitest-environment jsdom
import { useRef } from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Viewport } from '@xyflow/react';
import { useSmoothWheelZoom } from './useSmoothWheelZoom';

const flow = vi.hoisted(() => ({
  view: { x: 50, y: 30, zoom: 1 }, read: vi.fn(), write: vi.fn(),
  end: (_view: Viewport) => {}, store: { getState: () => ({ userSelectionActive: false }) },
}));
vi.mock('@xyflow/react', () => ({ useReactFlow: () => ({ getViewport: flow.read, setViewport: flow.write }), useStoreApi: () => flow.store }));
const persist = vi.fn();
const scrollable = () => false;
let now = 0, sequence = 0, endTimer: ReturnType<typeof setTimeout>;
const frames = new Map<number, FrameRequestCallback>();
function Harness() {
  const ref = useRef<HTMLDivElement>(null);
  const consume = useSmoothWheelZoom(ref, scrollable, persist);
  flow.end = viewport => { if (!consume(viewport)) persist(viewport); };
  return <div ref={ref}><div id="oaw-world-map" className="react-flow" /></div>;
}
function wheel(deltaY: number, timestamp = now) {
  const event = new WheelEvent('wheel', { deltaY, clientX: 200, clientY: 100, bubbles: true, cancelable: true });
  Object.defineProperty(event, 'timeStamp', { value: timestamp });
  act(() => document.querySelector('#oaw-world-map')!.dispatchEvent(event));
  return event;
}
function frame(time: number, rafTimestamp = time) {
  now = time;
  act(() => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback(rafTimestamp)); });
  act(() => vi.runOnlyPendingTimers());
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  now = 10; sequence = 0; frames.clear(); persist.mockClear(); flow.write.mockReset();
  flow.view = { x: 50, y: 30, zoom: 1 };
  flow.read.mockImplementation(() => flow.view);
  flow.write.mockImplementation((viewport: Viewport) => {
    flow.view = viewport;
    clearTimeout(endTimer);
    // Match XYFlow's deferred, coalesced move-end callback.
    endTimer = setTimeout(() => flow.end(viewport), 0);
    return Promise.resolve(true);
  });
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++sequence, callback); return sequence; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  render(<Harness />);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('persists once after the gesture, including delayed move-end callbacks', () => {
  wheel(-120);
  for (const time of [30, 60, 90, 120]) { frame(time); expect(persist).not.toHaveBeenCalled(); }
  frame(200);
  expect(persist).toHaveBeenCalledTimes(1);
  expect(flow.view.zoom).toBeCloseTo(2 ** .24);
  expect((200 - flow.view.x) / flow.view.zoom).toBeCloseTo(150);
  expect((100 - flow.view.y) / flow.view.zoom).toBeCloseTo(70);
});

it('accumulates same-direction input with one queued frame and no redundant writes', () => {
  wheel(-120); wheel(-120); wheel(-120);
  expect(flow.write).toHaveBeenCalledTimes(1);
  expect(frames.size).toBe(1);
  frame(200);
  expect(flow.view.zoom).toBeCloseTo(2 ** .72);
  expect(persist).toHaveBeenCalledTimes(1);
});

it('reverses from the displayed zoom rather than the unseen forward target', () => {
  wheel(-300); frame(45);
  const reversalZoom = flow.view.zoom;
  now = 50; wheel(120); frame(65);
  expect(flow.view.zoom).toBeLessThan(reversalZoom);
  frame(240);
  expect(flow.view.zoom).toBeCloseTo(reversalZoom / 2 ** .24);
  expect((200 - flow.view.x) / flow.view.zoom).toBeCloseTo(150);
});

it('never eases backwards with an old RAF timestamp or adds a tail to stale input', () => {
  now = 50; wheel(-120); frame(60, 40);
  expect(flow.view.zoom).toBeGreaterThan(1);
  frame(240);
  const previous = flow.view.zoom;
  now = 500; wheel(-120, 250); frame(500, 490);
  expect(flow.view.zoom).toBeCloseTo(previous * 2 ** .24);
  expect(frames.size).toBe(0);
});

it('lets another camera control persist while a wheel animation is active', () => {
  wheel(-120); frame(50);
  const external = { x: 500, y: 200, zoom: .7 };
  flow.view = external; flow.end(external);
  expect(persist).toHaveBeenCalledWith(external);
  frame(80);
  expect(flow.view).toEqual(external);
  expect(frames.size).toBe(0);
});

it('direct manipulation cancels animation and rejects wheel input queued before takeover', () => {
  wheel(-120); frame(30);
  const interrupted = flow.view;
  now = 40; act(() => document.dispatchEvent(new Event('pointerdown')));
  now = 60; expect(wheel(-120, 35).defaultPrevented).toBe(true);
  frame(300);
  expect(flow.view).toEqual(interrupted);
  now = 310; wheel(120); frame(500);
  expect(flow.view.zoom).toBeCloseTo(interrupted.zoom / 2 ** .24);
});
