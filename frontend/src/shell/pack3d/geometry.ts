import { BufferGeometry, Float32BufferAttribute, Shape, Path, ExtrudeGeometry } from 'three';

const smooth = (a: number, b: number, x: number) => {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

/** A continuous inflated pouch: shoulder creases, crimping and notches are geometry. */
export function pouchPoint(u: number, v: number, premium: boolean) {
  const x = u * 2 - 1, y = v * 2 - 1;
  const shoulders = 1 - smooth(.67, .89, Math.abs(y));
  const belly = Math.pow(Math.max(0, 1 - Math.pow(Math.abs(x), 6)), .62);
  const seal = smooth(.865, .89, Math.abs(y));
  const crease = Math.sin(x * 30 + y * 15) * .013 * Math.pow(Math.abs(x), 5) * shoulders
    + Math.sin(x * 22 + y * 16 + Math.sin(x * 6) * 2) * .018 * (1 - smooth(.12, .33, Math.abs(Math.abs(y) - .73))) * belly * (.2 + .8 * Math.pow(Math.abs(x), 3));
  const notch = .047 * Math.exp(-Math.pow((y - .75) / .026, 2)) * Math.pow(Math.abs(x), 24);
  return [x * (premium ? 1.13 : 1.17) - Math.sign(x) * notch,
    y * 1.71 + Math.sin(x * 3.3) * .012 * shoulders,
    .012 + (premium ? .22 : .26) * belly * shoulders + crease + seal * (.004 + .005 * Math.cos(x * Math.PI * 42))] as const;
}

// Keep detail where it changes the silhouette or surface relief. Crimping has
// 42 waves across the seal, so those rows retain 96 segments. The smooth belly
// needs far fewer, while shoulder rows retain the creases and the tear notch.
const BODY_ROWS = [0, .03, .055, .06, .0675, .085, .11, .14, .165, .20, .26, .34, .42, .5,
  .58, .66, .74, .80, .835, .855, .862, .8685, .875, .8815, .888, .905, .92, .9325, .935, .945, .946];
const FOIL_LEFT = 80 / 96, FOIL_RIGHT = 87 / 96;
function pouchColumns(v: number, premium: boolean) {
  const segments = v <= .0675 || v >= .9325 ? 96 : v < .26 || v > .74 ? 48 : 24;
  return [...new Set([
    ...Array.from({ length: segments + 1 }, (_, i) => i / segments),
    .004, .012, .024, .04, .96, .976, .988, .996,
    ...(premium ? [FOIL_LEFT, FOIL_RIGHT] : []),
  ])].sort((a, b) => a - b);
}

/** Stitch different row densities without T-junctions. Front/back share their
 * side and bottom seam vertices; only the torn mouth stays open. */
export function pouchGeometry(premium: boolean, part: 'body' | 'seal' = 'body') {
  const rows = (part === 'body' ? BODY_ROWS : [.946, 1]).map(v => ({ v, columns: pouchColumns(v, premium), offset: 0 }));
  const positions: number[] = [], uv: number[] = [], indices: number[][] = [[], [], [], []];
  let size = 0;
  for (const row of rows) { row.offset = size; size += row.columns.length; }
  for (let side = 0; side < 2; side++) for (const { v, columns } of rows) for (const u of columns) {
    const [x, y, z] = pouchPoint(u, v, premium);
    positions.push(x, y, side ? -z : z); uv.push(side ? 1 - u : u, v);
  }
  for (let side = 0; side < 2; side++) for (let j = 0; j < rows.length - 1; j++) {
    const lower = rows[j], upper = rows[j + 1], v = (lower.v + upper.v) / 2;
    const triangle = (a: number, b: number, c: number, u: number) => {
      const material = v < .06 || v > .935 ? 2 : side ? 1 : premium && u > FOIL_LEFT && u < FOIL_RIGHT ? 3 : 0;
      indices[material].push(...(side ? [a, c, b] : [a, b, c]));
    };
    let i = 0, k = 0;
    while (i < lower.columns.length - 1 || k < upper.columns.length - 1) {
      const a = side * size + lower.offset + i, c = side * size + upper.offset + k;
      const nextLower = lower.columns[i + 1] ?? Infinity, nextUpper = upper.columns[k + 1] ?? Infinity;
      if (nextLower <= nextUpper) {
        triangle(a, a + 1, c, (lower.columns[i] + nextLower + upper.columns[k]) / 3);
        i++;
      } else {
        triangle(a, c + 1, c, (lower.columns[i] + nextUpper + upper.columns[k]) / 3);
        k++;
      }
    }
  }
  for (let j = 0; j < rows.length - 1; j++) {
    const a = rows[j].offset, b = rows[j + 1].offset;
    indices[2].push(a, b, a + size, b, b + size, a + size);
    const c = a + rows[j].columns.length - 1, d = b + rows[j + 1].columns.length - 1;
    indices[2].push(c, c + size, d, d, c + size, d + size);
  }
  for (let i = 0; i < rows[0].columns.length - 1; i++) indices[2].push(i, i + size, i + 1, i + 1, i + size, i + size + 1);
  if (part === 'seal') for (let i = 0; i < rows.at(-1)!.columns.length - 1; i++) {
    const a = rows.at(-1)!.offset + i;
    indices[2].push(a, a + 1, a + size, a + 1, a + size + 1, a + size);
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new Float32BufferAttribute(uv, 2));
  let start = 0;
  for (let i = 0; i < indices.length; i++) { geometry.addGroup(start, indices[i].length, i); start += indices[i].length; }
  geometry.setIndex(indices.flat()); geometry.computeVertexNormals();
  return geometry;
}

function roundedPath<T extends Shape | Path>(path: T, w: number, h: number, r: number): T {
  const x = -w / 2, y = -h / 2;
  path.moveTo(x + r, y); path.lineTo(x + w - r, y); path.quadraticCurveTo(x + w, y, x + w, y + r);
  path.lineTo(x + w, y + h - r); path.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  path.lineTo(x + r, y + h); path.quadraticCurveTo(x, y + h, x, y + h - r);
  path.lineTo(x, y + r); path.quadraticCurveTo(x, y, x + r, y);
  return path;
}

/** A continuous hollow wall with inner surfaces, thickness and rounded corners. */
export function boxWallGeometry(w: number, h: number, depth: number, thickness: number) {
  const shape = roundedPath(new Shape(), w, h, .065);
  shape.holes.push(roundedPath(new Path(), w - thickness * 2, h - thickness * 2, .04));
  const geometry = new ExtrudeGeometry(shape, { depth, bevelEnabled: true, bevelSize: .006, bevelThickness: .006, bevelSegments: 2, curveSegments: 6, steps: 1 });
  geometry.translate(0, 0, -depth / 2);
  return geometry;
}

export function paperFlapGeometry() {
  const shape = new Shape();
  shape.moveTo(-1.13, 0); shape.lineTo(1.13, 0); shape.lineTo(1.13, -.36);
  shape.bezierCurveTo(1.12, -.75, .48, -.89, 0, -.91);
  shape.bezierCurveTo(-.48, -.89, -1.12, -.75, -1.13, -.36); shape.closePath();
  const geometry = new ExtrudeGeometry(shape, { depth: .012, bevelEnabled: true, bevelSize: .012, bevelThickness: .004, bevelSegments: 3, curveSegments: 24 });
  const p = geometry.getAttribute('position'), uv = geometry.getAttribute('uv');
  for (let i = 0; i < p.count; i++) uv.setXY(i, (p.getX(i) + 1.15) / 2.3, 1 + p.getY(i) / .95);
  return geometry;
}
