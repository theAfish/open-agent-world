import { CHUNK_SIZE } from '../state/chunks';

// Includes retained canvas backing plus conservative headroom for one worker
// raster, its transferred bitmap, and replacement backing. Browser/GPU driver
// allocations are not exposed by the Canvas API and must be measured separately.
export const TERRAIN_RASTER_BUDGET = 64 * 1024 * 1024;

export function terrainRasterSpec(zoom: number, dpr: number) {
  const scale = zoom * dpr;
  const padding = Math.ceil(2 * dpr);
  const pixels = Math.ceil(CHUNK_SIZE * scale) + padding * 2;
  return { scale, padding, pixels, bytes: pixels * pixels * 4 };
}

export function terrainRasterFits(tileCount: number, bytes: number) {
  return (tileCount + 3) * bytes <= TERRAIN_RASTER_BUDGET;
}
