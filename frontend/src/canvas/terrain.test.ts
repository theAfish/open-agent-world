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

  it("parses signed chunk keys without accepting malformed values", () => {
    expect(parseChunkKey("-12:7")).toEqual({ x: -12, y: 7 });
    expect(parseChunkKey("12.5:7")).toBeUndefined();
    expect(parseChunkKey("bad:key")).toBeUndefined();
  });
});
