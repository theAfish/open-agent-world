import { ViewportPortal } from "@xyflow/react";

interface TerrainFormation {
  x: number;
  y: number;
  radiusX: number;
  radiusY: number;
  rings: number;
  rotation: number;
  seed: number;
}

const WORLD_WIDTH = 4200;
const WORLD_HEIGHT = 3100;
const TERRAIN_SEED = 0x5eeda11;
const PATH_POINTS = 80;

const formations: TerrainFormation[] = [
  { x: 630, y: 620, radiusX: 480, radiusY: 330, rings: 4, rotation: -0.18, seed: 17 },
  { x: 1690, y: 560, radiusX: 590, radiusY: 410, rings: 5, rotation: 0.23, seed: 29 },
  { x: 2870, y: 980, radiusX: 390, radiusY: 560, rings: 4, rotation: 0.4, seed: 43 },
  { x: 950, y: 1980, radiusX: 630, radiusY: 380, rings: 5, rotation: 0.13, seed: 61 },
  { x: 2200, y: 2120, radiusX: 520, radiusY: 370, rings: 4, rotation: -0.3, seed: 79 },
  { x: 3440, y: 1780, radiusX: 310, radiusY: 255, rings: 3, rotation: 0.54, seed: 97 },
];

function fade(value: number) {
  return value * value * value * (value * (value * 6 - 15) + 10);
}

function lerp(start: number, end: number, amount: number) {
  return start + (end - start) * amount;
}

function gradientHash(x: number, y: number, seed: number) {
  let hash = Math.imul(x, 374761393) ^ Math.imul(y, 668265263) ^ Math.imul(seed, TERRAIN_SEED);
  hash = Math.imul(hash ^ (hash >>> 13), 1274126177);
  return (hash ^ (hash >>> 16)) & 7;
}

function gradientDot(x: number, y: number, offsetX: number, offsetY: number, seed: number) {
  const gradients = [
    [1, 0], [0.707, 0.707], [0, 1], [-0.707, 0.707],
    [-1, 0], [-0.707, -0.707], [0, -1], [0.707, -0.707],
  ];
  const gradient = gradients[gradientHash(x, y, seed)];
  return gradient[0] * offsetX + gradient[1] * offsetY;
}

/** Deterministic 2D Perlin noise: stable across renders and browser sessions. */
function perlin(x: number, y: number, seed: number) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const localX = x - x0;
  const localY = y - y0;
  const top = lerp(
    gradientDot(x0, y0, localX, localY, seed),
    gradientDot(x0 + 1, y0, localX - 1, localY, seed),
    fade(localX),
  );
  const bottom = lerp(
    gradientDot(x0, y0 + 1, localX, localY - 1, seed),
    gradientDot(x0 + 1, y0 + 1, localX - 1, localY - 1, seed),
    fade(localX),
  );
  return lerp(top, bottom, fade(localY));
}

function fractalPerlin(x: number, y: number, seed: number) {
  return (
    perlin(x, y, seed) * 0.6
    + perlin(x * 2, y * 2, seed + 1) * 0.28
    + perlin(x * 4, y * 4, seed + 2) * 0.12
  );
}

function contourPath(formation: TerrainFormation, ring: number) {
  const inset = 1 - ring * 0.155;
  const points: string[] = [];
  for (let index = 0; index <= PATH_POINTS; index += 1) {
    const angle = (index / PATH_POINTS) * Math.PI * 2;
    const cosine = Math.cos(angle);
    const sine = Math.sin(angle);
    const sampleX = formation.x / 260 + cosine * inset * 1.4;
    const sampleY = formation.y / 260 + sine * inset * 1.4;
    const detail = fractalPerlin(sampleX, sampleY, formation.seed);
    const radius = inset * (1 + detail * (0.2 - ring * 0.012));
    const localX = cosine * formation.radiusX * radius;
    const localY = sine * formation.radiusY * radius;
    const x = formation.x + localX * Math.cos(formation.rotation) - localY * Math.sin(formation.rotation);
    const y = formation.y + localX * Math.sin(formation.rotation) + localY * Math.cos(formation.rotation);
    points.push(`${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`);
  }
  return `${points.join(" ")} Z`;
}

const contourPaths = formations.flatMap((formation) =>
  Array.from({ length: formation.rings }, (_value, ring) => ({
    id: `${formation.seed}-${ring}`,
    d: contourPath(formation, ring),
  })),
);

export function ContourLayer() {
  return (
    <ViewportPortal>
      <svg className="contour-world" viewBox={`0 0 ${WORLD_WIDTH} ${WORLD_HEIGHT}`} role="presentation">
        {contourPaths.map((path) => <path key={path.id} className="contour" d={path.d} />)}
      </svg>
    </ViewportPortal>
  );
}
