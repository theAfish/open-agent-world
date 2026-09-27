import { describe, expect, it } from "vitest";
import { CHUNK_SIZE } from "../state/chunks";
import {
  getTerrainChunk,
  parseChunkKey,
  sampleTerrainChunk,
  terrainHeightAt,
  terrainResolutionForZoom,
} from "./terrain";

describe("procedural contour terrain", () => {
  it("keeps seed-specific terrain deterministic and isolates cached geometry", () => {
    const first = getTerrainChunk(-1, 0, 32, 123);
    const second = getTerrainChunk(-1, 0, 32, 456);
    expect(first).toBe(getTerrainChunk(-1, 0, 32, 123));
    expect(first.key).not.toBe(second.key);
    expect(first.minorPath).not.toBe(second.minorPath);
    expect(first.fillPaths).not.toEqual(second.fillPaths);
    const left = sampleTerrainChunk(-1, 0, 32, 456);
    const right = sampleTerrainChunk(0, 0, 32, 456);
    for (let row = 0; row <= 32; row += 1) {
      expect(left.values[row * 33 + 32]).toBe(right.values[row * 33]);
    }
  });
  it("is deterministic across positive and negative world coordinates", () => {
    const points = [[0, 0], [812.5, -2940], [-18_400, 37_200]] as const;
    for (const [x, y] of points) {
      expect(terrainHeightAt(x, y)).toBe(terrainHeightAt(x, y));
      expect(Number.isFinite(terrainHeightAt(x, y))).toBe(true);
    }
  });

  it("samples identical values along independently generated chunk seams", () => {
    const resolution = 24;
    const left = sampleTerrainChunk(-1, 2, resolution);
    const right = sampleTerrainChunk(0, 2, resolution);
    const stride = resolution + 1;

    for (let row = 0; row <= resolution; row += 1) {
      expect(left.values[row * stride + resolution]).toBe(right.values[row * stride]);
    }
    expect(terrainHeightAt(0, 2 * CHUNK_SIZE)).toBeCloseTo(left.values[resolution], 6);
  });

  it("uses stable LOD tiers and caches identical chunk geometry", () => {
    expect(terrainResolutionForZoom(0.12)).toBe(32);
    expect(terrainResolutionForZoom(0.8)).toBe(56);
    expect(terrainResolutionForZoom(1.5)).toBe(80);
    const chunk = getTerrainChunk(0, 0, 32);
    expect(chunk).toBe(getTerrainChunk(0, 0, 32));
    expect(`${chunk.minorPath}${chunk.majorPath}`).toContain("Q");
  });

  it('keeps LOD inside the hysteresis bands and handles jumps across both tiers', () => {
    expect(terrainResolutionForZoom(0.46, 32)).toBe(32);
    expect(terrainResolutionForZoom(0.44, 56)).toBe(56);
    expect(terrainResolutionForZoom(0.48, 32)).toBe(56);
    expect(terrainResolutionForZoom(0.41, 56)).toBe(32);
    expect(terrainResolutionForZoom(1.18, 56)).toBe(56);
    expect(terrainResolutionForZoom(1.12, 80)).toBe(80);
    expect(terrainResolutionForZoom(1.2, 56)).toBe(80);
    expect(terrainResolutionForZoom(1.09, 80)).toBe(56);
    expect(terrainResolutionForZoom(2, 32)).toBe(80);
    expect(terrainResolutionForZoom(0.2, 80)).toBe(32);
    for (const start of [32, 56]) {
      let resolution = start;
      for (const zoom of [0.44, 0.46, 0.45, 0.44, 0.46]) resolution = terrainResolutionForZoom(zoom, resolution);
      expect(resolution).toBe(start);
    }
  });

  it("parses signed chunk keys without accepting malformed values", () => {
    expect(parseChunkKey("-12:7")).toEqual({ x: -12, y: 7 });
    expect(parseChunkKey("12.5:7")).toBeUndefined();
    expect(parseChunkKey("bad:key")).toBeUndefined();
  });

  it("closes elevation fills at chunk boundaries at every LOD", () => {
    for (const resolution of [32, 56, 80]) {
      for (const [x, y] of [[-1, -1], [0, -1], [-1, 0], [0, 0]]) {
        const chunk = getTerrainChunk(x, y, resolution);
        expect(chunk.fillPaths.some(Boolean)).toBe(true);
        for (const path of chunk.fillPaths.filter(Boolean)) {
          expect(path).not.toMatch(/NaN|Infinity/);
          expect((path.match(/M/g) ?? []).length).toBe((path.match(/Z/g) ?? []).length);
        }
      }
    }
  });
});
