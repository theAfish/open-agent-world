// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import TerrainCanvasExperiment from './TerrainCanvasExperiment';
import type { TerrainChunkGeometry } from './terrain';
import type { TerrainRasterRequest, TerrainRasterResponse } from './terrainRaster.worker';

const state = vi.hoisted(() => ({ transform: [0, 0, 0.4], width: 640, height: 400 }));
const store = { getState: () => state, subscribe: () => () => undefined };
vi.mock('@xyflow/react', () => ({ useStoreApi: () => store }));

class FakeWorker {
  static latest: FakeWorker;
  requests: TerrainRasterRequest[] = [];
  onmessage: ((event: MessageEvent<TerrainRasterResponse>) => void) | null = null;
  terminate = vi.fn();
  constructor() { FakeWorker.latest = this; }
  postMessage(request: TerrainRasterRequest) { this.requests.push(request); }
  reply() {
    const bitmap = { close: vi.fn() } as unknown as ImageBitmap;
    this.onmessage?.(new MessageEvent('message', { data: { id: this.requests.at(-1)!.id, bitmap } }));
    return bitmap;
  }
}

const chunk: TerrainChunkGeometry = { key: '123:0:0:32', chunkX: 0, chunkY: 0, resolution: 32,
  minorPath: 'M0 0L10 10', majorPath: '', fillPaths: [] };

function scene(chunks: TerrainChunkGeometry[]) {
  return <div><svg className="contour-chunk" data-chunk="0:0" /><TerrainCanvasExperiment chunks={chunks} /></div>;
}

function setup() {
  vi.useFakeTimers();
  vi.stubGlobal('Worker', FakeWorker);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ transferFromImageBitmap: vi.fn() } as unknown as ImageBitmapRenderingContext);
  state.transform = [0, 0, 0.4];
}

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('reuses a warm raster after leaving and returning, and releases backing on unmount', () => {
  setup();
  const { container, rerender, unmount } = render(scene([chunk]));
  const worker = FakeWorker.latest;
  expect(worker.requests).toHaveLength(1);
  let bitmap: ImageBitmap;
  act(() => { bitmap = worker.reply(); });
  expect(bitmap!.close).toHaveBeenCalledOnce();
  const canvas = container.querySelector('canvas')!;
  expect(canvas.width).toBeGreaterThan(1);
  expect(container.querySelector('svg')?.getAttribute('data-canvas-ready')).toBe('true');
  rerender(scene([]));
  expect(container.querySelector('canvas')).toBeNull();
  expect(container.querySelector('svg')?.hasAttribute('data-canvas-ready')).toBe(false);
  act(() => vi.advanceTimersByTime(120));
  rerender(scene([chunk]));
  act(() => vi.advanceTimersByTime(120));
  expect(container.querySelector('canvas')).toBe(canvas);
  expect(worker.requests).toHaveLength(1);
  const metrics = container.querySelector<HTMLElement>('.contour-canvas-experiment')!.dataset;
  expect(Number(metrics.rasterPeakBytes)).toBeLessThanOrEqual(Number(metrics.rasterBudget));
  unmount();
  expect(canvas.width).toBe(1);
  expect(worker.terminate).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it('discards a raster arriving after its geometry was cleared', () => {
  setup();
  const { container, rerender } = render(scene([chunk]));
  rerender(scene([]));
  let bitmap: ImageBitmap;
  act(() => { bitmap = FakeWorker.latest.reply(); });
  expect(bitmap!.close).toHaveBeenCalledOnce();
  expect(container.querySelector('canvas')).toBeNull();
  expect(container.querySelector('svg')?.hasAttribute('data-canvas-ready')).toBe(false);
});

it('keeps SVG at high DPR when exact-quality raster exceeds the memory budget', () => {
  setup();
  state.transform = [0, 0, 2.2];
  vi.stubGlobal('devicePixelRatio', 2);
  const { container } = render(scene([chunk]));
  expect(FakeWorker.latest.requests).toHaveLength(0);
  expect(container.querySelector<HTMLElement>('.contour-canvas-experiment')!.dataset.fallback).toBe('budget');
  expect(container.querySelector('svg')?.hasAttribute('data-canvas-ready')).toBe(false);
});
