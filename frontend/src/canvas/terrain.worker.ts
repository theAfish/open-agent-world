import { parseChunkKey } from './terrain';
import { getTerrainScalarTile, type TerrainScalarTile } from './terrainScalar';

export interface TerrainRequest {
  id: number;
  keys: string[];
  resolution: number;
  seed: number;
}

export interface TerrainResponse {
  id: number;
  tile: TerrainScalarTile;
}

let pending: TerrainRequest | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;

function pump() {
  timer = undefined;
  if (!pending) return;
  const key = pending.keys.shift();
  if (key === undefined) return;
  const coordinates = parseChunkKey(key);
  if (coordinates) {
    const cached = getTerrainScalarTile(coordinates.x, coordinates.y, pending.resolution, pending.seed);
    // Transfer a copy: the bounded worker cache must retain its own samples.
    const tile = { ...cached, values: cached.values.slice() };
    postMessage({ id: pending.id, tile } satisfies TerrainResponse, { transfer: [tile.values.buffer] });
  }
  // Yield between chunks so a newer viewport can replace obsolete queued work.
  if (pending.keys.length) timer = setTimeout(pump, 0);
}

self.onmessage = (event: MessageEvent<TerrainRequest>) => {
  pending = event.data;
  if (timer === undefined) timer = setTimeout(pump, 0);
};
