import { afterEach, describe, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { getViewportChunkKeys } from './chunks';
import { useWorldStore } from './worldStore';

const viewport = { x: -200, y: -200, zoom: 1, width: 800, height: 600 };

afterEach(() => vi.restoreAllMocks());

describe('viewport chunk invalidation', () => {
  it('persists camera changes without invalidating coverage or requesting chunks', () => {
    const keys = getViewportChunkKeys(viewport);
    useWorldStore.setState({ viewport, activeChunkKeys: keys, loadedChunkKeys: keys, loadingChunkKeys: [] });
    const ensure = vi.spyOn(useWorldStore.getState(), 'ensureChunks');
    const changes: string[][] = [];
    const unsubscribe = useWorldStore.subscribe((state, previous) => {
      if (state.activeChunkKeys !== previous.activeChunkKeys) changes.push(state.activeChunkKeys);
    });
    for (let step = 1; step <= 100; step++) {
      useWorldStore.getState().setViewport({ ...viewport, x: viewport.x - step, zoom: 1 + step / 1000 });
    }
    expect(useWorldStore.getState().viewport.x).toBe(-300);
    expect(useWorldStore.getState().activeChunkKeys).toBe(keys);
    expect(changes).toEqual([]);
    expect(ensure).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('does not publish an identical viewport', () => {
    useWorldStore.setState({ viewport, activeChunkKeys: getViewportChunkKeys(viewport) });
    const listener = vi.fn();
    const unsubscribe = useWorldStore.subscribe(listener);
    useWorldStore.getState().setViewport({ ...viewport });
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('requests newly covered chunks immediately, deduplicates pending loads and reuses cached return coverage', async () => {
    const keys = getViewportChunkKeys(viewport);
    useWorldStore.setState({ viewport, activeChunkKeys: keys, loadedChunkKeys: keys,
      loadingChunkKeys: [], cards: [], edges: [], syncState: 'online' });
    const request = vi.spyOn(worldApi, 'getWorld').mockResolvedValue({ nodes: [], edges: [], chunks: [] });
    const crossed = { ...viewport, x: -2100 };
    useWorldStore.getState().setViewport(crossed);
    const nextKeys = getViewportChunkKeys(crossed);
    expect(useWorldStore.getState().activeChunkKeys).toEqual(nextKeys);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(nextKeys.filter(key => !keys.includes(key)));
    useWorldStore.getState().setViewport(viewport);
    useWorldStore.getState().setViewport(crossed);
    expect(request).toHaveBeenCalledTimes(1);
    await Promise.resolve();
    expect(useWorldStore.getState().loadedChunkKeys).toEqual(expect.arrayContaining(nextKeys));
    useWorldStore.getState().setViewport(viewport);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it.each([
    { ...viewport, x: 1 },
    { ...viewport, width: 2200 },
    { ...viewport, height: 2300 },
    { ...viewport, zoom: 0.1 },
  ])('recomputes coverage for pan, resize and zoom crossings: %j', next => {
    useWorldStore.setState({ viewport, activeChunkKeys: getViewportChunkKeys(viewport), syncState: 'offline' });
    useWorldStore.getState().setViewport(next);
    expect(useWorldStore.getState().activeChunkKeys).toEqual(getViewportChunkKeys(next));
  });
});
