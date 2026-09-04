import { ViewportPortal, useOnViewportChange, type Viewport } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CHUNK_SIZE, getViewportChunkKeys } from "../state/chunks";
import { useWorldStore } from "../state/worldStore";
import type { FlowViewportState } from "../types/world";
import { getTerrainChunk, parseChunkKey, terrainResolutionForZoom } from "./terrain";

interface TerrainView {
  keys: string[];
  resolution: number;
  signature: string;
}

function terrainViewFor(viewport: FlowViewportState): TerrainView {
  const keys = getViewportChunkKeys(viewport);
  const resolution = terrainResolutionForZoom(viewport.zoom);
  return { keys, resolution, signature: `${resolution}|${keys.join(",")}` };
}

export function ContourLayer() {
  const storedViewport = useWorldStore((state) => state.viewport);
  const [terrainView, setTerrainView] = useState(() => terrainViewFor(storedViewport));
  const signature = useRef(terrainView.signature);
  const acceptViewport = useCallback((viewport: FlowViewportState) => {
    const next = terrainViewFor(viewport);
    if (next.signature === signature.current) return;
    signature.current = next.signature;
    setTerrainView(next);
  }, []);
  const onViewportChange = useCallback((viewport: Viewport) => {
    const { width, height } = useWorldStore.getState().viewport;
    acceptViewport({ ...viewport, width, height });
  }, [acceptViewport]);

  useOnViewportChange({ onChange: onViewportChange, onEnd: onViewportChange });
  useEffect(() => acceptViewport(storedViewport), [acceptViewport, storedViewport]);

  const chunks = useMemo(() => terrainView.keys.flatMap((key) => {
    const coordinates = parseChunkKey(key);
    return coordinates ? [getTerrainChunk(coordinates.x, coordinates.y, terrainView.resolution)] : [];
  }), [terrainView]);

  return (
    <ViewportPortal>
      {chunks.map((chunk) => (
        <svg
          key={chunk.key}
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
          {chunk.minorPath && <path className="contour contour-minor" d={chunk.minorPath} />}
          {chunk.majorPath && <path className="contour contour-major" d={chunk.majorPath} />}
        </svg>
      ))}
    </ViewportPortal>
  );
}
