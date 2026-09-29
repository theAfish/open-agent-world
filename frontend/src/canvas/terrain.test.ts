import { describe, expect, it } from "vitest";
import { CHUNK_SIZE } from "../state/chunks";
import {
  parseChunkKey,
  sampleTerrainChunk,
  terrainHeightAt,
} from "./terrain";

describe("procedural terrain field", () => {
  it("keeps seed-specific terrain deterministic", () => {
    const first = sampleTerrainChunk(-1, 0, 32, 123);
    const second = sampleTerrainChunk(-1, 0, 32, 456);
    expect(first.values).toEqual(sampleTerrainChunk(-1, 0, 32, 123).values);
    expect(first.values).not.toEqual(second.values);
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

  it("parses signed chunk keys without accepting malformed values", () => {
    expect(parseChunkKey("-12:7")).toEqual({ x: -12, y: 7 });
    expect(parseChunkKey("12.5:7")).toBeUndefined();
    expect(parseChunkKey("bad:key")).toBeUndefined();
  });

});
