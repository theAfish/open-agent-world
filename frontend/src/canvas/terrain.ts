import { CHUNK_SIZE } from "../state/chunks";

const TERRAIN_SEED = 0x5eeda11;
const SIMPLEX_F2 = 0.5 * (Math.sqrt(3) - 1);
const SIMPLEX_G2 = (3 - Math.sqrt(3)) / 6;

export const CONTOUR_LEVELS = Array.from({ length: 13 }, (_value, index) => -0.6 + index * 0.1);

export interface TerrainGrid {
  resolution: number;
  values: Float32Array;
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
export function terrainHeightAt(worldX: number, worldY: number, seed = TERRAIN_SEED) {
  const warpX = fractalNoise(worldX / 3900, worldY / 3900, seed + 17, 3);
  const warpY = fractalNoise(
    (worldX + 12_700) / 3900,
    (worldY - 8_300) / 3900,
    seed + 43,
    3,
  );
  const warpedX = worldX + warpX * 760;
  const warpedY = worldY + warpY * 760;
  const continent = fractalNoise(warpedX / 6200, warpedY / 6200, seed + 101, 5);
  const rolling = fractalNoise(warpedX / 1750, warpedY / 1750, seed + 211, 4);
  const mountainField = fractalNoise(
    (warpedX - 4_100) / 4100,
    (warpedY + 2_900) / 4100,
    seed + 307,
    3,
  );
  const mountainMask = smoothstep(-0.24, 0.52, mountainField);
  const ridges = ridgedNoise(warpedX / 1050, warpedY / 1050, seed + 401, 4);
  const detail = fractalNoise(warpedX / 520, warpedY / 520, seed + 503, 2);

  return (
    continent * 0.54
    + rolling * 0.25
    + detail * 0.07
    + (ridges - 0.43) * 0.62 * mountainMask
  );
}

// The scalar field has the same sampling resolution at every camera scale.
export const TERRAIN_RESOLUTION = 56;

export function sampleTerrainChunk(chunkX: number, chunkY: number, resolution: number, seed = TERRAIN_SEED): TerrainGrid {
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
        seed,
      );
    }
  }

  return { resolution, values };
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
