import { CHUNK_SIZE } from "../state/chunks";

const TERRAIN_SEED = 0x5eeda11;
const SIMPLEX_F2 = 0.5 * (Math.sqrt(3) - 1);
const SIMPLEX_G2 = (3 - Math.sqrt(3)) / 6;
const TERRAIN_CACHE_LIMIT = 256;

export const CONTOUR_LEVELS = Array.from({ length: 13 }, (_value, index) => -0.6 + index * 0.1);

interface Point {
  x: number;
  y: number;
}

interface Segment {
  start: Point;
  end: Point;
}

export interface TerrainGrid {
  resolution: number;
  values: Float32Array;
}

export interface TerrainChunkGeometry {
  key: string;
  chunkX: number;
  chunkY: number;
  resolution: number;
  minorPath: string;
  majorPath: string;
}

const gradients: ReadonlyArray<readonly [number, number]> = [
  [1, 0],
  [0.70710678, 0.70710678],
  [0, 1],
  [-0.70710678, 0.70710678],
  [-1, 0],
  [-0.70710678, -0.70710678],
  [0, -1],
  [0.70710678, -0.70710678],
];

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value));
}

function smoothstep(minimum: number, maximum: number, value: number) {
  const amount = clamp((value - minimum) / (maximum - minimum), 0, 1);
  return amount * amount * (3 - 2 * amount);
}

function gradientIndex(x: number, y: number, seed: number) {
  let hash = Math.imul(x, 0x1f123bb5) ^ Math.imul(y, 0x5f356495) ^ Math.imul(seed, 0x6c8e9cf5);
  hash = Math.imul(hash ^ (hash >>> 15), 0x2c1b3c6d);
  return (hash ^ (hash >>> 12)) & 7;
}

/** Deterministic, isotropic 2D simplex noise in approximately [-1, 1]. */
function simplex2D(x: number, y: number, seed: number) {
  const skew = (x + y) * SIMPLEX_F2;
  const cellX = Math.floor(x + skew);
  const cellY = Math.floor(y + skew);
  const unskew = (cellX + cellY) * SIMPLEX_G2;
  const localX0 = x - (cellX - unskew);
  const localY0 = y - (cellY - unskew);
  const offsetX = localX0 > localY0 ? 1 : 0;
  const offsetY = localX0 > localY0 ? 0 : 1;
  const localX1 = localX0 - offsetX + SIMPLEX_G2;
  const localY1 = localY0 - offsetY + SIMPLEX_G2;
  const localX2 = localX0 - 1 + 2 * SIMPLEX_G2;
  const localY2 = localY0 - 1 + 2 * SIMPLEX_G2;

  const contribution = (gridX: number, gridY: number, localX: number, localY: number) => {
    const falloff = 0.5 - localX * localX - localY * localY;
    if (falloff <= 0) return 0;
    const gradient = gradients[gradientIndex(gridX, gridY, seed)];
    const squared = falloff * falloff;
    return squared * squared * (gradient[0] * localX + gradient[1] * localY);
  };

  return 70 * (
    contribution(cellX, cellY, localX0, localY0)
    + contribution(cellX + offsetX, cellY + offsetY, localX1, localY1)
    + contribution(cellX + 1, cellY + 1, localX2, localY2)
  );
}

function fractalNoise(x: number, y: number, seed: number, octaves: number) {
  let value = 0;
  let amplitude = 1;
  let amplitudeTotal = 0;
  let sampleX = x;
  let sampleY = y;

  for (let octave = 0; octave < octaves; octave += 1) {
    value += simplex2D(sampleX, sampleY, seed + octave * 1013) * amplitude;
    amplitudeTotal += amplitude;
    amplitude *= 0.5;
    const rotatedX = (sampleX * 0.8 - sampleY * 0.6) * 2.03 + 13.7;
    sampleY = (sampleX * 0.6 + sampleY * 0.8) * 2.03 - 9.2;
    sampleX = rotatedX;
  }

  return value / amplitudeTotal;
}

function ridgedNoise(x: number, y: number, seed: number, octaves: number) {
  let value = 0;
  let amplitude = 1;
  let amplitudeTotal = 0;
  let weight = 1;
  let sampleX = x;
  let sampleY = y;

  for (let octave = 0; octave < octaves; octave += 1) {
    let ridge = 1 - Math.abs(simplex2D(sampleX, sampleY, seed + octave * 1597));
    ridge *= ridge;
    ridge *= weight;
    weight = clamp(ridge * 1.85, 0, 1);
    value += ridge * amplitude;
    amplitudeTotal += amplitude;
    amplitude *= 0.52;
    const rotatedX = (sampleX * 0.866 - sampleY * 0.5) * 2.08 - 7.4;
    sampleY = (sampleX * 0.5 + sampleY * 0.866) * 2.08 + 11.1;
    sampleX = rotatedX;
  }

  return value / amplitudeTotal;
}

/**
 * Continuous procedural height field. Chunk coordinates never enter the noise,
 * so independently generated neighbors sample exactly the same shared edge.
 */
export function terrainHeightAt(worldX: number, worldY: number) {
  const warpX = fractalNoise(worldX / 3900, worldY / 3900, TERRAIN_SEED + 17, 3);
  const warpY = fractalNoise(
    (worldX + 12_700) / 3900,
    (worldY - 8_300) / 3900,
    TERRAIN_SEED + 43,
    3,
  );
  const warpedX = worldX + warpX * 760;
  const warpedY = worldY + warpY * 760;
  const continent = fractalNoise(warpedX / 6200, warpedY / 6200, TERRAIN_SEED + 101, 5);
  const rolling = fractalNoise(warpedX / 1750, warpedY / 1750, TERRAIN_SEED + 211, 4);
  const mountainField = fractalNoise(
    (warpedX - 4_100) / 4100,
    (warpedY + 2_900) / 4100,
    TERRAIN_SEED + 307,
    3,
  );
  const mountainMask = smoothstep(-0.24, 0.52, mountainField);
  const ridges = ridgedNoise(warpedX / 1050, warpedY / 1050, TERRAIN_SEED + 401, 4);
  const detail = fractalNoise(warpedX / 520, warpedY / 520, TERRAIN_SEED + 503, 2);

  return (
    continent * 0.54
    + rolling * 0.25
    + detail * 0.07
    + (ridges - 0.43) * 0.62 * mountainMask
  );
}

export function terrainResolutionForZoom(zoom: number) {
  if (zoom >= 1.15) return 80;
  if (zoom >= 0.45) return 56;
  return 32;
}

export function sampleTerrainChunk(chunkX: number, chunkY: number, resolution: number): TerrainGrid {
  const stride = resolution + 1;
  const step = CHUNK_SIZE / resolution;
  const originX = chunkX * CHUNK_SIZE;
  const originY = chunkY * CHUNK_SIZE;
  const values = new Float32Array(stride * stride);

  for (let row = 0; row <= resolution; row += 1) {
    for (let column = 0; column <= resolution; column += 1) {
      values[row * stride + column] = terrainHeightAt(
        originX + column * step,
        originY + row * step,
      );
    }
  }

  return { resolution, values };
}

type Edge = 0 | 1 | 2 | 3;
type EdgePair = readonly [Edge, Edge];

const contourCases: ReadonlyArray<ReadonlyArray<EdgePair>> = [
  [],
  [[3, 0]],
  [[0, 1]],
  [[3, 1]],
  [[1, 2]],
  [],
  [[0, 2]],
  [[3, 2]],
  [[2, 3]],
  [[0, 2]],
  [],
  [[1, 2]],
  [[1, 3]],
  [[0, 1]],
  [[3, 0]],
  [],
];

function edgePairs(mask: number, centerAbove: boolean): ReadonlyArray<EdgePair> {
  if (mask === 5) return centerAbove ? [[0, 1], [2, 3]] : [[3, 0], [1, 2]];
  if (mask === 10) return centerAbove ? [[3, 0], [1, 2]] : [[0, 1], [2, 3]];
  return contourCases[mask];
}

function interpolate(level: number, start: number, end: number) {
  const difference = end - start;
  if (Math.abs(difference) < 1e-8) return 0.5;
  return clamp((level - start) / difference, 0, 1);
}

function edgePoint(
  edge: Edge,
  column: number,
  row: number,
  step: number,
  level: number,
  topLeft: number,
  topRight: number,
  bottomRight: number,
  bottomLeft: number,
): Point {
  if (edge === 0) {
    return { x: (column + interpolate(level, topLeft, topRight)) * step, y: row * step };
  }
  if (edge === 1) {
    return { x: (column + 1) * step, y: (row + interpolate(level, topRight, bottomRight)) * step };
  }
  if (edge === 2) {
    return { x: (column + interpolate(level, bottomLeft, bottomRight)) * step, y: (row + 1) * step };
  }
  return { x: column * step, y: (row + interpolate(level, topLeft, bottomLeft)) * step };
}

function pointText(point: Point) {
  return `${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
}

function pointKey(point: Point) {
  return `${point.x.toFixed(4)}:${point.y.toFixed(4)}`;
}

function midpoint(start: Point, end: Point): Point {
  return { x: (start.x + end.x) * 0.5, y: (start.y + end.y) * 0.5 };
}

function smoothPolyline(points: Point[]) {
  if (points.length < 2) return "";
  if (points.length === 2) return `M${pointText(points[0])}L${pointText(points[1])}`;
  const closed = pointKey(points[0]) === pointKey(points[points.length - 1]);

  if (closed) {
    const ring = points.slice(0, -1);
    if (ring.length < 3) return `M${pointText(points[0])}L${pointText(points[1])}`;
    const commands = [`M${pointText(midpoint(ring[ring.length - 1], ring[0]))}`];
    for (let index = 0; index < ring.length; index += 1) {
      const current = ring[index];
      const next = ring[(index + 1) % ring.length];
      commands.push(`Q${pointText(current)} ${pointText(midpoint(current, next))}`);
    }
    commands.push("Z");
    return commands.join("");
  }

  const commands = [`M${pointText(points[0])}`];
  for (let index = 1; index < points.length - 1; index += 1) {
    commands.push(`Q${pointText(points[index])} ${pointText(midpoint(points[index], points[index + 1]))}`);
  }
  commands.push(`L${pointText(points[points.length - 1])}`);
  return commands.join("");
}

function stitchSegments(segments: Segment[]) {
  const connections = new Map<string, number[]>();
  const visited = new Uint8Array(segments.length);
  const addConnection = (point: Point, segmentIndex: number) => {
    const key = pointKey(point);
    connections.set(key, [...(connections.get(key) ?? []), segmentIndex]);
  };
  segments.forEach((segment, index) => {
    addConnection(segment.start, index);
    addConnection(segment.end, index);
  });

  const trace = (initialIndex: number, reverse: boolean) => {
    const initial = segments[initialIndex];
    const points = reverse ? [initial.end, initial.start] : [initial.start, initial.end];
    visited[initialIndex] = 1;
    while (points.length <= segments.length + 1) {
      const currentKey = pointKey(points[points.length - 1]);
      const nextIndex = connections.get(currentKey)?.find((index) => !visited[index]);
      if (nextIndex === undefined) break;
      visited[nextIndex] = 1;
      const next = segments[nextIndex];
      points.push(pointKey(next.start) === currentKey ? next.end : next.start);
      if (pointKey(points[points.length - 1]) === pointKey(points[0])) break;
    }
    return smoothPolyline(points);
  };

  const paths: string[] = [];
  segments.forEach((segment, index) => {
    if (visited[index]) return;
    const startDegree = connections.get(pointKey(segment.start))?.length ?? 0;
    const endDegree = connections.get(pointKey(segment.end))?.length ?? 0;
    if (startDegree !== 2 || endDegree !== 2) paths.push(trace(index, endDegree !== 2));
  });
  segments.forEach((_segment, index) => {
    if (!visited[index]) paths.push(trace(index, false));
  });
  return paths.join("");
}

function buildTerrainChunk(chunkX: number, chunkY: number, resolution: number): TerrainChunkGeometry {
  const grid = sampleTerrainChunk(chunkX, chunkY, resolution);
  const stride = resolution + 1;
  const step = CHUNK_SIZE / resolution;
  const minorSegments: string[] = [];
  const majorSegments: string[] = [];

  for (let levelIndex = 0; levelIndex < CONTOUR_LEVELS.length; levelIndex += 1) {
    const level = CONTOUR_LEVELS[levelIndex];
    const destination = levelIndex % 4 === 0 ? majorSegments : minorSegments;
    const levelSegments: Segment[] = [];
    for (let row = 0; row < resolution; row += 1) {
      for (let column = 0; column < resolution; column += 1) {
        const topLeft = grid.values[row * stride + column];
        const topRight = grid.values[row * stride + column + 1];
        const bottomLeft = grid.values[(row + 1) * stride + column];
        const bottomRight = grid.values[(row + 1) * stride + column + 1];
        const mask = (
          (topLeft >= level ? 1 : 0)
          | (topRight >= level ? 2 : 0)
          | (bottomRight >= level ? 4 : 0)
          | (bottomLeft >= level ? 8 : 0)
        );
        if (mask === 0 || mask === 15) continue;

        const pairs = edgePairs(mask, (topLeft + topRight + bottomRight + bottomLeft) * 0.25 >= level);
        for (const [startEdge, endEdge] of pairs) {
          const start = edgePoint(
            startEdge, column, row, step, level, topLeft, topRight, bottomRight, bottomLeft,
          );
          const end = edgePoint(
            endEdge, column, row, step, level, topLeft, topRight, bottomRight, bottomLeft,
          );
          levelSegments.push({ start, end });
        }
      }
    }
    if (levelSegments.length > 0) destination.push(stitchSegments(levelSegments));
  }

  return {
    key: `${chunkX}:${chunkY}:${resolution}`,
    chunkX,
    chunkY,
    resolution,
    minorPath: minorSegments.join(""),
    majorPath: majorSegments.join(""),
  };
}

const terrainCache = new Map<string, TerrainChunkGeometry>();

export function getTerrainChunk(chunkX: number, chunkY: number, resolution: number) {
  const key = `${chunkX}:${chunkY}:${resolution}`;
  const cached = terrainCache.get(key);
  if (cached) {
    terrainCache.delete(key);
    terrainCache.set(key, cached);
    return cached;
  }

  const chunk = buildTerrainChunk(chunkX, chunkY, resolution);
  terrainCache.set(key, chunk);
  if (terrainCache.size > TERRAIN_CACHE_LIMIT) {
    const oldestKey = terrainCache.keys().next().value;
    if (oldestKey) terrainCache.delete(oldestKey);
  }
  return chunk;
}

export function parseChunkKey(key: string): { x: number; y: number } | undefined {
  const parts = key.split(":");
  if (parts.length !== 2) return undefined;
  const [rawX, rawY] = parts;
  const x = Number(rawX);
  const y = Number(rawY);
  if (!Number.isInteger(x) || !Number.isInteger(y)) return undefined;
  return { x, y };
}
