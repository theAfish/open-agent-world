// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

const fixture = vi.hoisted(() => {
  const world = { viewport: { x: 100, y: 100, zoom: 0.8, width: 1280, height: 800 }, terrainSeed: 123 };
  let flow = { transform: [100, 100, 0.8] };
  const listeners = new Set<(next: typeof flow, previous: typeof flow) => void>();
  let onChange: (viewport: { x: number; y: number; zoom: number }) => void;
  return {
    world,
    chunks: [{ key: '123:0:0:56', chunkX: 0, chunkY: 0, resolution: 56, minorPath: 'M0 0L100 100', majorPath: '', fillPaths: [] }],
    store: { getState: () => flow, subscribe: (listener: (next: typeof flow, previous: typeof flow) => void) => {
      listeners.add(listener); return () => listeners.delete(listener);
    } },
    onViewportChange: (handlers: { onChange: typeof onChange }) => { onChange = handlers.onChange; },
    move: (x: number, zoom: number) => {
      const previous = flow;
      flow = { transform: [x, 100, zoom] };
      listeners.forEach(listener => listener(flow, previous));
      onChange({ x, y: 100, zoom });
    },
  };
});

vi.mock('@xyflow/react', () => ({ useStoreApi: () => fixture.store, useOnViewportChange: fixture.onViewportChange }));
vi.mock('./FlowPortal', () => ({ ViewportPortal: ({ children }: { children: ReactNode }) => children }));
vi.mock('../state/worldStore', () => ({ useWorldStore: Object.assign(
  (selector: (state: typeof fixture.world) => unknown) => selector(fixture.world), { getState: () => fixture.world },
) }));
vi.mock('./useTerrainChunks', () => ({ useTerrainChunks: vi.fn(() => fixture.chunks) }));

import { ContourLayer } from './ContourLayer';
import { useTerrainChunks } from './useTerrainChunks';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('updates exact screen stroke compensation without React rendering unchanged coverage', () => {
  const { container } = render(<ContourLayer />);
  const tile = container.querySelector('svg.contour-chunk');
  const initial = vi.mocked(useTerrainChunks).mock.calls.length;
  for (let i = 0; i < 20; i++) {
    act(() => fixture.move(100 + i, 0.76 + i * 0.004));
  }
  expect(vi.mocked(useTerrainChunks).mock.calls).toHaveLength(initial);
  expect(container.querySelector('svg.contour-chunk')).toBe(tile);
  const layer = container.querySelector<HTMLElement>('.contour-layer')!;
  expect(Number(layer.style.getPropertyValue('--contour-stroke-scale'))).toBeCloseTo(1 / 0.836, 8);
  expect(layer.style.getPropertyValue('--contour-promotion')).toBe('auto');

  act(() => fixture.move(-2100, 0.836));
  expect(vi.mocked(useTerrainChunks).mock.calls.length).toBeGreaterThan(initial);
  expect(vi.mocked(useTerrainChunks).mock.lastCall?.[0]).toContain('2:0');
});
