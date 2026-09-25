import { expect, it } from 'vitest';
import { terrainRasterFits, terrainRasterSpec, TERRAIN_RASTER_BUDGET } from './terrainRaster';

it('budgets pixel backing at actual zoom and DPR, including transfer headroom', () => {
  const overview = terrainRasterSpec(0.12, 1);
  expect(overview.pixels).toBe(250);
  expect(terrainRasterFits(77, overview.bytes)).toBe(true);
  const high = terrainRasterSpec(2.2, 2);
  expect(high.bytes).toBeGreaterThan(TERRAIN_RASTER_BUDGET);
  expect(terrainRasterFits(1, high.bytes)).toBe(false);
  const maximum = Math.floor(TERRAIN_RASTER_BUDGET / overview.bytes) - 3;
  expect(terrainRasterFits(maximum, overview.bytes)).toBe(true);
  expect(terrainRasterFits(maximum + 1, overview.bytes)).toBe(false);
});
