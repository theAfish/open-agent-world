import { expect, it, vi } from 'vitest';
import { TerrainTextureCache } from './terrainTextureCache';
import type { TerrainScalarTile } from './terrainScalar';

function gpu() {
  return { createTexture: vi.fn(() => ({})), deleteTexture: vi.fn(), bindTexture: vi.fn(), texParameteri: vi.fn(),
    texImage2D: vi.fn(), getError: vi.fn(() => 0), NO_ERROR: 0 } as unknown as WebGL2RenderingContext;
}
const tile = (x: number, seed = 1): TerrainScalarTile => ({ key: `${seed}:${x}:0:2`, chunkX: x, chunkY: 0,
  resolution: 2, values: new Float32Array(25) });

it('reuses uploads, protects visible tiles, and deletes evicted GPU resources within both budgets', () => {
  const gl = gpu(), cache = new TerrainTextureCache(gl, 3, 300);
  for (let i = 0; i < 3; i++) cache.add(tile(i));
  cache.protect(['0:0', '3:0']);
  expect(cache.add(tile(0))).toBe(false);
  cache.add(tile(3));
  expect([...cache.entries.keys()]).toEqual(['2:0', '0:0', '3:0']);
  expect(gl.texImage2D).toHaveBeenCalledTimes(4);
  expect(gl.deleteTexture).toHaveBeenCalledTimes(1);
  expect(cache.bytes).toBe(300);
  expect(cache.peakBytes).toBe(300);
  for (let i = 4; i < 400; i++) { cache.protect([`${i}:0`]); cache.add(tile(i)); }
  expect(cache.entries.size).toBe(3); expect(cache.bytes).toBe(300);
  cache.clear(); expect(cache.bytes).toBe(0);
  expect(gl.deleteTexture).toHaveBeenCalledTimes(400);
});

it('retains bounded CPU copies during context loss and reuploads only on restoration', () => {
  const gl = gpu(), cache = new TerrainTextureCache(gl, 3, 300);
  cache.add(tile(0)); cache.add(tile(1)); cache.contextLost();
  expect([...cache.entries.values()].every(entry => entry.texture === null)).toBe(true);
  cache.add(tile(2), false);
  expect(cache.uploads).toBe(2);
  cache.restore(gl); expect(cache.uploads).toBe(5);
  expect([...cache.entries.values()].every(entry => entry.texture !== null)).toBe(true);
  cache.clear(); cache.add(tile(0, 2));
  expect(cache.entries.get('0:0')?.tile.key).toBe('2:0:0:2');
});

it('enforces byte limits as well as tile counts and reports failed uploads', () => {
  const gl = gpu(), cache = new TerrainTextureCache(gl, 20, 150);
  cache.add(tile(0)); cache.protect(['1:0']); cache.add(tile(1));
  expect(cache.entries.size).toBe(1); expect(cache.bytes).toBe(100);
  vi.mocked(gl.getError).mockReturnValue(1285);
  cache.protect(['2:0']); expect(() => cache.add(tile(2))).toThrow('upload failed');
});
