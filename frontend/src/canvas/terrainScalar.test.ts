import { describe, expect, it } from 'vitest';
import { sampleTerrainChunk, TERRAIN_RESOLUTION } from './terrain';
import { getTerrainScalarTile } from './terrainScalar';
import { parseTerrainColor } from './TerrainRendererWebGL';

describe('scalar terrain transport', () => {
  it('preserves every original grid sample and seed identity without building paths', () => {
    const n = TERRAIN_RESOLUTION, size = n + 3;
    const tile = getTerrainScalarTile(-2, 3, n, 123);
    const original = sampleTerrainChunk(-2, 3, n, 123);
    for (let y = 0; y <= n; y++) for (let x = 0; x <= n; x++) {
      expect(tile.values[(y + 1) * size + x + 1]).toBe(original.values[y * (n + 1) + x]);
    }
    expect(getTerrainScalarTile(-2, 3, n, 123)).toBe(tile);
    expect(getTerrainScalarTile(-2, 3, n, 456).values).not.toEqual(tile.values);
    expect(tile).not.toHaveProperty('minorPath');
  });

  it('shares borders and interpolation halos across negative and positive tiles', () => {
    const n = TERRAIN_RESOLUTION, size = n + 3;
    const a = getTerrainScalarTile(-1, -1, n, 42);
    const right = getTerrainScalarTile(0, -1, n, 42);
    const bottom = getTerrainScalarTile(-1, 0, n, 42);
    for (let i = 0; i < size; i++) for (let halo = 0; halo < 3; halo++) {
      expect(a.values[i * size + n + halo]).toBeCloseTo(right.values[i * size + halo], 6);
      expect(a.values[(n + halo) * size + i]).toBeCloseTo(bottom.values[halo * size + i], 6);
    }
  });

  it('keeps subtle fill alpha instead of quantizing it through an 8-bit canvas', () => {
    expect(parseTerrainColor('rgba(166, 112, 57, 0.006)')[3]).toBeCloseTo(.006, 8);
    expect(Array.from(parseTerrainColor('#22211e'))).toEqual([34 / 255, 33 / 255, 30 / 255, 1].map(Math.fround));
  });
});
