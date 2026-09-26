import { useStoreApi } from '@xyflow/react';
import { useEffect, useLayoutEffect, useRef } from 'react';
import { CHUNK_SIZE, getViewportChunkBounds } from '../state/chunks';
import type { TerrainChunkGeometry } from './terrain';
import { TERRAIN_RASTER_BUDGET, terrainRasterFits, terrainRasterSpec } from './terrainRaster';
import type { TerrainRasterRequest, TerrainRasterResponse } from './terrainRaster.worker';

interface RasterTile {
  canvas: HTMLCanvasElement;
  bytes: number;
  coordinate: string;
}

/**
 * Dev-only A/B: /?terrainRenderer=canvas. SVG remains the fallback while a tile
 * is missing, unsupported or exceeds the byte budget. Geometry is unchanged.
 * Cached bitmaps follow the same world transform. During zoom they temporarily
 * scale their strokes; 120 ms after input stops we restore exact screen widths
 * and DPR. This visual/refinement tradeoff is part of the experiment, not a
 * production rendering policy.
 */
export default function TerrainCanvasExperiment({ chunks }: { chunks: TerrainChunkGeometry[] }) {
  const store = useStoreApi();
  const root = useRef<HTMLDivElement>(null);
  const latestChunks = useRef(chunks);
  const refresh = useRef<() => void>();
  useLayoutEffect(() => {
    latestChunks.current = chunks;
    refresh.current?.();
  }, [chunks]);

  useEffect(() => {
    const host = root.current!;
    const layer = host.parentElement!;
    const cache = new Map<string, RasterTile>();
    const shown = new Map<string, RasterTile>();
    let bytes = 0;
    let peakBytes = 0;
    let serial = 0;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let queue: Array<{ key: string; chunk: TerrainChunkGeometry }> = [];
    let desired = new Set<string>();
    let svgByCoordinate = new Map<string, SVGSVGElement>();
    let pending: { id: number; key: string; generation: number; request: TerrainRasterRequest } | undefined;
    let settings = { zoom: 1, dpr: 1, stroke: '', fill: '' };
    let disabled = false;
    let worker: Worker;
    try {
      worker = new Worker(new URL('./terrainRaster.worker.ts', import.meta.url), { type: 'module' });
    } catch {
      host.dataset.fallback = 'worker';
      host.dataset.rasterBytes = '0';
      host.dataset.rasterPending = '0';
      return;
    }

    const report = () => {
      host.dataset.rasterBytes = String(bytes);
      host.dataset.rasterPeakBytes = String(peakBytes);
      host.dataset.rasterBudget = String(TERRAIN_RASTER_BUDGET);
      host.dataset.rasterTiles = String(cache.size);
      host.dataset.activeTiles = String(shown.size);
      host.dataset.rasterPending = String(queue.length + Number(!!pending));
    };
    const detach = (tile: RasterTile) => {
      tile.canvas.remove();
      if (shown.get(tile.coordinate) === tile) {
        shown.delete(tile.coordinate);
        svgByCoordinate.get(tile.coordinate)?.removeAttribute('data-canvas-ready');
      }
    };
    const remove = (key: string, tile: RasterTile) => {
      detach(tile);
      // Removing a node alone does not promptly release canvas backing memory.
      tile.canvas.width = 1;
      tile.canvas.height = 1;
      cache.delete(key);
      bytes -= tile.bytes;
    };
    const show = (key: string, tile: RasterTile) => {
      const old = shown.get(tile.coordinate);
      if (old && old !== tile) detach(old);
      if (tile.canvas.parentElement !== host) host.append(tile.canvas);
      shown.set(tile.coordinate, tile);
      svgByCoordinate.get(tile.coordinate)?.setAttribute('data-canvas-ready', 'true');
      // Touch the LRU only when used; dormant canvases retain their raster.
      cache.delete(key);
      cache.set(key, tile);
    };
    const pump = () => {
      if (pending || disabled) return;
      const job = queue.shift();
      if (!job) { report(); return; }
      const spec = terrainRasterSpec(settings.zoom, settings.dpr);
      // Keep capacity for transient worker/transfer/replacement backing too.
      for (const [key, tile] of cache) {
        if (bytes + spec.bytes * 3 <= TERRAIN_RASTER_BUDGET) break;
        if (!desired.has(key)) remove(key, tile);
      }
      if (bytes + spec.bytes * 3 > TERRAIN_RASTER_BUDGET) {
        host.dataset.fallback = 'budget';
        queue = [];
        report();
        return;
      }
      peakBytes = Math.max(peakBytes, bytes + spec.bytes * 3);
      const request = { id: ++serial, chunk: job.chunk, ...settings } satisfies TerrainRasterRequest;
      pending = { id: request.id, key: job.key, generation, request };
      worker.postMessage(request);
      report();
    };
    const reconcile = () => {
      if (disabled) return;
      generation++;
      queue = [];
      const state = store.getState();
      const [x, y, zoom] = state.transform;
      const dpr = window.devicePixelRatio || 1;
      const bounds = getViewportChunkBounds({ x, y, zoom, width: state.width, height: state.height }, 0);
      const colors = getComputedStyle(layer);
      settings = { zoom, dpr, stroke: colors.getPropertyValue('--contour').trim(), fill: colors.getPropertyValue('--contour-fill').trim() };
      const spec = terrainRasterSpec(zoom, dpr);
      const visible = latestChunks.current.filter(chunk => chunk.chunkX >= bounds.minX && chunk.chunkX <= bounds.maxX
        && chunk.chunkY >= bounds.minY && chunk.chunkY <= bounds.maxY);
      svgByCoordinate = new Map(Array.from(layer.querySelectorAll<SVGSVGElement>('svg.contour-chunk'))
        .map(svg => [svg.dataset.chunk!, svg]));
      const eligible = spec.pixels <= 8192 && terrainRasterFits(visible.length, spec.bytes);
      host.dataset.fallback = eligible ? '' : 'budget';
      if (!eligible) for (const [key, tile] of cache) remove(key, tile);
      const cacheKey = (chunk: TerrainChunkGeometry) => `${chunk.key}|${zoom}|${dpr}|${settings.stroke}|${settings.fill}`;
      desired = new Set(eligible ? visible.map(cacheKey) : []);
      // Once input settles, use exact SVG while its new bitmap is pending.
      // Old rasters were retained throughout the continuous zoom gesture.
      for (const tile of shown.values()) detach(tile);
      for (const chunk of eligible ? visible : []) {
        const key = cacheKey(chunk);
        const cached = cache.get(key);
        if (cached) show(key, cached);
        else queue.push({ key, chunk });
      }
      host.dataset.rasterZoom = String(zoom);
      pump();
    };
    const fallback = (reason: string) => {
      disabled = true;
      queue = [];
      pending = undefined;
      for (const [key, tile] of cache) remove(key, tile);
      host.dataset.fallback = reason;
      report();
      worker.terminate();
    };
    worker.onmessage = (event: MessageEvent<TerrainRasterResponse>) => {
      const { bitmap, error, id } = event.data;
      const completed = pending;
      pending = undefined;
      if (error) { bitmap?.close(); fallback('unsupported'); return; }
      if (!bitmap || !completed || completed.id !== id || completed.generation !== generation) {
        bitmap?.close(); pump(); return;
      }
      const { chunk, zoom, dpr } = completed.request;
      const spec = terrainRasterSpec(zoom, dpr);
      const canvas = document.createElement('canvas');
      canvas.width = spec.pixels;
      canvas.height = spec.pixels;
      const context = canvas.getContext('bitmaprenderer');
      if (!context) { bitmap.close(); fallback('unsupported'); return; }
      context.transferFromImageBitmap(bitmap);
      bitmap.close();
      canvas.className = 'contour-chunk contour-canvas';
      const coordinate = `${chunk.chunkX}:${chunk.chunkY}`;
      canvas.dataset.chunk = coordinate;
      canvas.dataset.resolution = String(chunk.resolution);
      canvas.dataset.rasterZoom = String(zoom);
      canvas.dataset.rasterDpr = String(dpr);
      canvas.style.left = `${chunk.chunkX * CHUNK_SIZE - spec.padding / spec.scale}px`;
      canvas.style.top = `${chunk.chunkY * CHUNK_SIZE - spec.padding / spec.scale}px`;
      canvas.style.width = `${spec.pixels / spec.scale}px`;
      canvas.style.height = `${spec.pixels / spec.scale}px`;
      const tile = { canvas, bytes: spec.bytes, coordinate };
      cache.set(completed.key, tile);
      bytes += spec.bytes;
      show(completed.key, tile);
      pump();
    };
    worker.onerror = () => fallback('worker');
    const schedule = () => {
      if (timer !== undefined) clearTimeout(timer);
      timer = setTimeout(() => { timer = undefined; reconcile(); }, 120);
    };
    refresh.current = () => {
      generation++;
      queue = [];
      // A new seed clears geometry immediately. Never retain an old-world bitmap
      // merely because the next raster pass is waiting for motion to settle.
      const coordinates = new Set(latestChunks.current.map(chunk => `${chunk.chunkX}:${chunk.chunkY}`));
      for (const tile of shown.values()) if (!coordinates.has(tile.coordinate)) detach(tile);
      report();
      schedule();
    };
    const unsubscribe = store.subscribe((state, previous) => {
      if (state.transform !== previous.transform || state.width !== previous.width || state.height !== previous.height) schedule();
    });
    const observer = new MutationObserver(reconcile);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style', 'class'] });
    window.addEventListener('resize', schedule);
    reconcile();
    return () => {
      refresh.current = undefined;
      unsubscribe();
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      if (timer !== undefined) clearTimeout(timer);
      worker.terminate();
      for (const [key, tile] of cache) remove(key, tile);
    };
  }, [store]);
  return <div ref={root} className="contour-canvas-experiment" aria-hidden="true" />;
}
