import { CHUNK_SIZE } from '../state/chunks';
import { sampleTerrainChunk, terrainHeightAt } from './terrain';

export interface TerrainScalarTile {
  key: string;
  chunkX: number;
  chunkY: number;
  resolution: number;
  /** One sample of halo on each side, for continuous cubic interpolation. */
  values: Float32Array;
}

export const TERRAIN_SCALAR_CACHE_LIMIT = 256;
const cache = new Map<string, TerrainScalarTile>();

/** Sample the seeded world field with a halo for seamless GPU interpolation. */
export function getTerrainScalarTile(chunkX: number, chunkY: number, resolution: number, seed: number): TerrainScalarTile {
  const key = `${seed}:${chunkX}:${chunkY}:${resolution}`;
  const existing = cache.get(key);
  if (existing) {
    cache.delete(key); cache.set(key, existing);
    return existing;
  }
  const grid = sampleTerrainChunk(chunkX, chunkY, resolution, seed);
  const size = resolution + 3;
  const values = new Float32Array(size * size);
  for (let y = -1; y <= resolution + 1; y++) {
    for (let x = -1; x <= resolution + 1; x++) {
      values[(y + 1) * size + x + 1] = x >= 0 && x <= resolution && y >= 0 && y <= resolution
        ? grid.values[y * (resolution + 1) + x]
        : terrainHeightAt((chunkX + x / resolution) * CHUNK_SIZE, (chunkY + y / resolution) * CHUNK_SIZE, seed);
    }
  }
  const tile = { key, chunkX, chunkY, resolution, values };
  cache.set(key, tile);
  if (cache.size > TERRAIN_SCALAR_CACHE_LIMIT) cache.delete(cache.keys().next().value!);
  return tile;
}
