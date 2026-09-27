import { useOnViewportChange, useStoreApi, type Viewport } from "@xyflow/react";
import { ViewportPortal } from "./FlowPortal";
import { lazy, memo, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { CHUNK_SIZE, getViewportChunkBounds, getViewportChunkKeys } from "../state/chunks";
import { useWorldStore } from "../state/worldStore";
import type { FlowViewportState } from "../types/world";
import { terrainResolutionForZoom, type TerrainChunkGeometry } from "./terrain";
import { useTerrainChunks } from './useTerrainChunks';

// Internal benchmark only. The production renderer remains SVG.
const CanvasExperiment = import.meta.env.DEV
  ? lazy(() => import('./TerrainCanvasExperiment')) : null;

interface TerrainView {
  keys: string[];
  resolution: number;
  signature: string;
}

function terrainViewSignature(viewport: FlowViewportState, resolution: number) {
  const { minX, maxX, minY, maxY } = getViewportChunkBounds(viewport, 0);
  return `${resolution}|${minX}:${maxX}:${minY}:${maxY}`;
}

function terrainViewFor(viewport: FlowViewportState, resolution = terrainResolutionForZoom(viewport.zoom)): TerrainView {
  const visible = new Set(getViewportChunkKeys(viewport, 0));
  const keys = getViewportChunkKeys(viewport).sort((a, b) => Number(visible.has(b)) - Number(visible.has(a)));
  return { keys, resolution, signature: terrainViewSignature(viewport, resolution) };
}

// Worker arrivals and coverage changes should only render new/replaced tiles.
const ContourChunk = memo(function ContourChunk({ chunk }: { chunk: TerrainChunkGeometry }) {
  return <svg
    className="contour-chunk"
    data-chunk={`${chunk.chunkX}:${chunk.chunkY}`}
    data-resolution={chunk.resolution}
    viewBox={`0 0 ${CHUNK_SIZE} ${CHUNK_SIZE}`}
    style={{
      left: chunk.chunkX * CHUNK_SIZE,
      top: chunk.chunkY * CHUNK_SIZE,
    }}
    role="presentation"
  >
    {chunk.fillPaths.map((path, index) => path && (
      <path key={index} className="contour-fill" fillRule="evenodd" d={path} />
    ))}
    {chunk.minorPath && <path className="contour contour-minor" d={chunk.minorPath} />}
    {chunk.majorPath && <path className="contour contour-major" d={chunk.majorPath} />}
  </svg>;
});

export const ContourLayer = memo(function ContourLayer() {
  const store = useStoreApi();
  const layer = useRef<HTMLDivElement>(null);
  const storedViewport = useWorldStore((state) => state.viewport);
  const terrainSeed = useWorldStore((state) => state.terrainSeed);
  const [terrainView, setTerrainView] = useState(() => terrainViewFor(storedViewport));
  const currentView = useRef(terrainView);
  const acceptViewport = useCallback((viewport: FlowViewportState) => {
    // Constant-time boundary check on pointer moves; enumerate/sort only when
    // coverage or LOD actually changes, regardless of how wide the view is.
    const resolution = terrainResolutionForZoom(viewport.zoom, currentView.current.resolution);
    if (terrainViewSignature(viewport, resolution) === currentView.current.signature) return;
    currentView.current = terrainViewFor(viewport, resolution);
    setTerrainView(currentView.current);
  }, []);
  const onViewportChange = useCallback((viewport: Viewport) => {
    const { width, height } = useWorldStore.getState().viewport;
    acceptViewport({ ...viewport, width, height });
  }, [acceptViewport]);

  useOnViewportChange({ onChange: onViewportChange, onEnd: onViewportChange });
  useEffect(() => acceptViewport(storedViewport), [acceptViewport, storedViewport]);

  useLayoutEffect(() => {
    // Outer HTML scaling is not compensated by SVG vector-effect. One inherited
    // property keeps exact screen-space strokes without rendering every tile.
    // This still repaints SVG strokes during zoom; benchmark browser paint too.
    const update = () => {
      const zoom = store.getState().transform[2];
      layer.current?.style.setProperty('--contour-stroke-scale', String(1 / zoom));
      layer.current?.style.setProperty('--contour-promotion', zoom < 0.45 ? 'transform' : 'auto');
    };
    update();
    return store.subscribe((state, previous) => {
      if (state.transform[2] !== previous.transform[2]) update();
    });
  }, [store]);

  const chunks = useTerrainChunks(terrainView.keys, terrainView.resolution, terrainSeed);
  const canvasExperiment = CanvasExperiment && new URLSearchParams(location.search).get('terrainRenderer') === 'canvas';
  const zoom = store.getState().transform[2];

  return (
    <ViewportPortal>
      <div ref={layer} className="contour-layer" data-terrain-renderer={canvasExperiment ? 'canvas-experiment' : 'svg'}
        style={{ '--contour-stroke-scale': 1 / zoom, '--contour-promotion': zoom < 0.45 ? 'transform' : 'auto' } as CSSProperties}>
        {chunks.map((chunk) => (
          <ContourChunk key={`${chunk.chunkX}:${chunk.chunkY}`} chunk={chunk} />
        ))}
        {canvasExperiment && <Suspense fallback={null}><CanvasExperiment chunks={chunks} /></Suspense>}
      </div>
    </ViewportPortal>
  );
});
