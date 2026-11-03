// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ChartCanvas } from '../frontend/ChartCanvas';
import type { Plot } from '../frontend/plot';

const plot: Plot = { points: [
  {x: 0, y: 10, label: 'start', series: ''}, {x: 1, y: 20, label: 'mid', series: ''}, {x: 2, y: 30, label: 'end', series: ''},
], categories: [], dateX: false, nodes: [], edges: [], skipped: 0 };
let chart: HTMLElement;
let context: Record<string, ReturnType<typeof vi.fn>>;
let captured: Set<number>;
function pointer(type: string, x: number, y: number, pointerId = 1, pointerType = 'touch') {
  const event = Object.assign(new MouseEvent(type, {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0}), {pointerId, pointerType});
  act(() => { chart.dispatchEvent(event); });
  return event;
}
const points = () => context.arc.mock.calls.slice(-3).map(call => call[0]);
beforeEach(() => {
  context = Object.fromEntries(['scale', 'clearRect', 'beginPath', 'moveTo', 'lineTo', 'stroke', 'fillText', 'save', 'translate', 'rotate', 'restore', 'rect', 'clip', 'arc', 'fill', 'fillRect'].map(method => [method, vi.fn()]));
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  render(<ChartCanvas plot={plot} kind="scatter" xLabel="Step" yLabel="Value" />);
  chart = screen.getByRole('img').parentElement!;
  vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({left: 10, top: 20, width: 300, height: 160} as DOMRect);
  captured = new Set();
  Object.assign(chart, {
    setPointerCapture: (id: number) => captured.add(id), hasPointerCapture: (id: number) => captured.has(id), releasePointerCapture: (id: number) => captured.delete(id),
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('anchors horizontal pinch zoom at the moving midpoint under a scaled card and continues with one finger', () => {
  expect(points()).toEqual([62, 321, 580]);
  pointer('pointerdown', 91, 60);
  pointer('pointerdown', 191, 60, 2);
  pointer('pointermove', 291, 60, 2);
  // Local midpoint moves 262 -> 362, distance doubles, and the same X value
  // stays beneath that midpoint. The Y scale deliberately remains unchanged.
  expect(points()).toEqual([-38, 480, 998]);
  pointer('pointerup', 291, 60, 2);
  pointer('pointermove', 111, 60);
  expect(points()).toEqual([2, 520, 1038]);
  pointer('pointerup', 111, 60);
  expect(captured.size).toBe(0);
  expect(chart.querySelector('.viz-tooltip')).toBeNull();
});

it('lets a touch tap inspect a point without losing its tooltip on the synthetic pointer leave', () => {
  pointer('pointerdown', 170.5, 92);
  pointer('pointerup', 170.5, 92);
  expect(screen.getByText('mid: 20')).toBeTruthy();
  pointer('pointerout', 170.5, 92);
  expect(screen.getByText('mid: 20')).toBeTruthy();
  pointer('pointerdown', 170.5, 92);
  pointer('pointermove', 200.5, 92);
  pointer('pointerup', 200.5, 92);
  expect(chart.querySelector('.viz-tooltip')).toBeNull();
});

it.each(['pointercancel', 'lostpointercapture', 'blur'])('cleans up all pointers on %s and ignores later movement', type => {
  pointer('pointerdown', 91, 60);
  pointer('pointerdown', 191, 60, 2);
  pointer('pointermove', 291, 60, 2);
  if (type === 'blur') act(() => { window.dispatchEvent(new Event('blur')); });
  else pointer(type, 291, 60, 2);
  expect(captured.size).toBe(0);
  const position = points();
  pointer('pointermove', 131, 70);
  expect(points()).toEqual(position);
});

it('preserves mouse hover, wheel zoom, keyboard reset and the visible reset action', () => {
  pointer('pointermove', 170.5, 92, 1, 'mouse');
  expect(screen.getByText('mid: 20')).toBeTruthy();
  fireEvent.wheel(chart, {deltaY: -100});
  expect(points()[1]).toBeCloseTo(372.8);
  fireEvent.click(screen.getByRole('button', {name: '↺ 1:1'}));
  expect(points()).toEqual([62, 321, 580]);
  pointer('pointerdown', 91, 60, 1, 'mouse');
  pointer('pointermove', 111, 60, 1, 'mouse');
  expect(points()[0]).toBe(102);
  fireEvent.keyDown(chart, {key: 'Home'});
  expect(points()).toEqual([62, 321, 580]);
  expect(captured.size).toBe(0);
});
