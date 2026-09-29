import type { TerrainScalarTile } from './terrainScalar';

export const TERRAIN_GPU_TILE_LIMIT = 256;
export const TERRAIN_GPU_TEXTURE_BUDGET = 4 * 1024 * 1024;

export interface TerrainTextureTile {
  tile: TerrainScalarTile;
  texture: WebGLTexture | null;
}

/** LRU owns both a bounded recovery copy and the corresponding GPU texture. */
export class TerrainTextureCache {
  readonly entries = new Map<string, TerrainTextureTile>();
  bytes = 0;
  uploads = 0;
  evictions = 0;
  peakBytes = 0;
  private protectedKeys = new Set<string>();

  constructor(private gl: WebGL2RenderingContext, readonly limit = TERRAIN_GPU_TILE_LIMIT,
    readonly budget = TERRAIN_GPU_TEXTURE_BUDGET) {}

  protect(keys: string[]) {
    this.protectedKeys = new Set(keys);
    for (const key of keys) {
      const entry = this.entries.get(key);
      if (entry) { this.entries.delete(key); this.entries.set(key, entry); }
    }
  }

  add(tile: TerrainScalarTile, upload = true) {
    const key = `${tile.chunkX}:${tile.chunkY}`;
    const previous = this.entries.get(key);
    if (previous?.tile.key === tile.key) return false;
    if (previous) this.remove(key, previous);
    if (tile.values.byteLength > this.budget) return false;
    for (const [oldKey, entry] of this.entries) {
      if (this.entries.size < this.limit && this.bytes + tile.values.byteLength <= this.budget) break;
      if (!this.protectedKeys.has(oldKey)) this.remove(oldKey, entry);
    }
    if (this.entries.size >= this.limit || this.bytes + tile.values.byteLength > this.budget) return false;
    const entry = { tile, texture: null };
    this.entries.set(key, entry);
    this.bytes += tile.values.byteLength;
    this.peakBytes = Math.max(this.peakBytes, this.bytes);
    if (upload) this.upload(entry);
    return true;
  }

  private remove(key: string, entry: TerrainTextureTile) {
    if (entry.texture) this.gl.deleteTexture(entry.texture);
    this.entries.delete(key);
    this.bytes -= entry.tile.values.byteLength;
    this.evictions++;
  }

  private upload(entry: TerrainTextureTile) {
    const gl = this.gl;
    const texture = gl.createTexture();
    if (!texture) throw new Error('Terrain texture allocation failed');
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const size = entry.tile.resolution + 3;
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, size, size, 0, gl.RED, gl.FLOAT, entry.tile.values);
    if (gl.getError() !== gl.NO_ERROR) {
      gl.deleteTexture(texture);
      throw new Error('Terrain texture upload failed');
    }
    entry.texture = texture;
    this.uploads++;
  }

  contextLost() {
    // WebGL already freed these handles; retain only the bounded CPU copies.
    for (const entry of this.entries.values()) entry.texture = null;
  }

  restore(gl: WebGL2RenderingContext) {
    this.gl = gl;
    for (const entry of this.entries.values()) this.upload(entry);
  }

  clear() {
    for (const [key, entry] of this.entries) this.remove(key, entry);
    this.protectedKeys.clear();
  }
}
