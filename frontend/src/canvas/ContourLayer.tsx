import { useOnViewportChange, useStore, type Viewport } from "@xyflow/react";
import { ViewportPortal } from "./FlowPortal";
import { memo, useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { CHUNK_SIZE, getViewportChunkBounds, getViewportChunkKeys } from "../state/chunks";
import { useWorldStore } from "../state/worldStore";
import type { FlowViewportState } from "../types/world";
import { terrainResolutionForZoom, type TerrainChunkGeometry } from "./terrain";
import { useTerrainChunks } from './useTerrainChunks';

interface TerrainView {
  keys: string[];
  resolution: number;
  signature: string;
}

function terrainViewSignature(viewport: FlowViewportState) {
  const { minX, maxX, minY, maxY } = getViewportChunkBounds(viewport, 0);
  return `${terrainResolutionForZoom(viewport.zoom)}|${minX}:${maxX}:${minY}:${maxY}`;
}

function terrainViewFor(viewport: FlowViewportState, signature = terrainViewSignature(viewport)): TerrainView {
  const visible = new Set(getViewportChunkKeys(viewport, 0));
  const keys = getViewportChunkKeys(viewport).sort((a, b) => Number(visible.has(b)) - Number(visible.has(a)));
  const resolution = terrainResolutionForZoom(viewport.zoom);
  return { keys, resolution, signature };
}

// Worker arrivals and coverage changes should only render new/replaced tiles.
const ContourChunk = memo(function ContourChunk({ chunk, zoom }: { chunk: TerrainChunkGeometry; zoom: number }) {
  return <svg
    className="contour-chunk"
    data-chunk={`${chunk.chunkX}:${chunk.chunkY}`}
    data-resolution={chunk.resolution}
    viewBox={`0 0 ${CHUNK_SIZE} ${CHUNK_SIZE}`}
    style={{
      '--contour-stroke-scale': 1 / zoom,
      // At overview scale many complex paths fit on screen. Cache each tile's
      // raster for panning; only small on-screen tiles get a promoted layer.
      // Avoid retaining large textures when zoomed in (2048 * 2.2 per tile).
      willChange: zoom < 0.45 ? 'transform' : undefined,
      left: chunk.chunkX * CHUNK_SIZE,
      top: chunk.chunkY * CHUNK_SIZE,
    } as CSSProperties}
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
  // React Flow scales an HTML ancestor. SVG vector-effect does not compensate
  // that outer CSS transform, so convert screen pixels back to world units.
  const zoom = useStore(state => state.transform[2]);
  const storedViewport = useWorldStore((state) => state.viewport);
  const terrainSeed = useWorldStore((state) => state.terrainSeed);
  const [terrainView, setTerrainView] = useState(() => terrainViewFor(storedViewport));
  const signature = useRef(terrainView.signature);
  const acceptViewport = useCallback((viewport: FlowViewportState) => {
    // Constant-time boundary check on pointer moves; enumerate/sort only when
    // coverage or LOD actually changes, regardless of how wide the view is.
    const nextSignature = terrainViewSignature(viewport);
    if (nextSignature === signature.current) return;
    signature.current = nextSignature;
    setTerrainView(terrainViewFor(viewport, nextSignature));
  }, []);
  const onViewportChange = useCallback((viewport: Viewport) => {
    const { width, height } = useWorldStore.getState().viewport;
    acceptViewport({ ...viewport, width, height });
  }, [acceptViewport]);

  useOnViewportChange({ onChange: onViewportChange, onEnd: onViewportChange });
  useEffect(() => acceptViewport(storedViewport), [acceptViewport, storedViewport]);

  const chunks = useTerrainChunks(terrainView.keys, terrainView.resolution, terrainSeed);

  return (
    <ViewportPortal>
      {chunks.map((chunk) => (
        <ContourChunk key={`${chunk.chunkX}:${chunk.chunkY}`} chunk={chunk} zoom={zoom} />
      ))}
    </ViewportPortal>
  );
});
