import type { Atom, Structure } from "./formats";

type Vector3 = { x: number; y: number; z: number };
type Basis = number[][];
const dot = (a: number[], b: number[]) => a.reduce((sum, value, axis) => sum + value * b[axis], 0);

/** QR of the periodic lattice vectors (columns), including 1D/2D cells. */
function orthogonalize(basis: Basis): { q: Basis; r: Basis } | null {
  const q: Basis = [];
  const r = basis.map(() => basis.map(() => 0));
  for (let column = 0; column < basis.length; column += 1) {
    const residual = [...basis[column]];
    for (let row = 0; row < column; row += 1) {
      r[row][column] = dot(q[row], residual);
      for (let axis = 0; axis < 3; axis += 1) residual[axis] -= r[row][column] * q[row][axis];
    }
    const length = Math.hypot(...residual);
    if (length <= 32 * Number.EPSILON * Math.hypot(...basis[column])) return null;
    r[column][column] = length;
    q.push(residual.map(value => value / length));
  }
  return { q, r };
}

/** LLL size reductions keep the exact closest-vector search small for skew cells.
 * Only integer combinations and swaps are used, so the periodic lattice is unchanged.
 */
function reducedBasis(vectors: Basis): Basis | null {
  const basis = vectors.map(vector => [...vector]);
  let index = 1;
  while (index < basis.length) {
    let qr = orthogonalize(basis);
    if (!qr) return null;
    for (let previous = index - 1; previous >= 0; previous -= 1) {
      const multiple = Math.round(qr.r[previous][index] / qr.r[previous][previous]);
      if (multiple) {
        basis[index] = basis[index].map((value, axis) => value - multiple * basis[previous][axis]);
        qr = orthogonalize(basis);
        if (!qr) return null;
      }
    }
    const mu = qr.r[index - 1][index] / qr.r[index - 1][index - 1];
    if (qr.r[index][index] ** 2 >= (0.75 - mu ** 2) * qr.r[index - 1][index - 1] ** 2) {
      index += 1;
    } else {
      [basis[index], basis[index - 1]] = [basis[index - 1], basis[index]];
      index = Math.max(1, index - 1);
    }
  }
  return basis;
}

/** Returns the shortest vector from `from` to `to`, respecting enabled PBC axes. */
export function minimumImageDisplacement(from: Vector3, to: Vector3, structure: Pick<Structure, "cell" | "pbc">): Vector3 {
  const direct = { x: to.x - from.x, y: to.y - from.y, z: to.z - from.z };
  const cell = structure.cell;
  if (!cell || !structure.pbc.some(Boolean)) return direct;
  if (cell.length !== 3 || cell.some(row => row.length !== 3 || row.some(value => !Number.isFinite(value)))) return direct;
  const basis = reducedBasis(cell.filter((_, axis) => structure.pbc[axis]));
  const qr = basis && orthogonalize(basis);
  if (!basis || !qr) return direct;
  const { q, r } = qr;
  const delta = [direct.x, direct.y, direct.z];
  const projected = q.map(vector => dot(delta, vector));
  const count = basis.length;
  const coefficients = Array<number>(count).fill(0);
  const center = (row: number) => (projected[row] - r[row].slice(row + 1)
    .reduce((sum, value, offset) => sum + value * coefficients[row + offset + 1], 0)) / r[row][row];
  // Babai's nearest-plane point supplies an initial search radius, not the answer.
  for (let row = count - 1; row >= 0; row -= 1) coefficients[row] = Math.round(center(row));
  let best = [...coefficients];
  let bestSquared = projected.reduce((sum, value, row) => sum + (value - dot(r[row], coefficients)) ** 2, 0);
  if (!Number.isFinite(bestSquared)) return direct;
  const tolerance = bestSquared * 1e-12;
  function search(row: number, squared: number): void {
    if (row < 0) {
      if (squared < bestSquared) { bestSquared = squared; best = [...coefficients]; }
      return;
    }
    const middle = center(row);
    const radius = Math.sqrt(Math.max(0, bestSquared - squared + tolerance)) / r[row][row];
    const lower = Math.ceil(middle - radius);
    const upper = Math.floor(middle + radius);
    // Visit the nearest coefficient first so improved radii prune later candidates.
    const nearest = Math.round(middle);
    for (let offset = 0; offset <= Math.max(nearest - lower, upper - nearest); offset += 1) {
      for (const candidate of offset ? [nearest - offset, nearest + offset] : [nearest]) {
        if (candidate < lower || candidate > upper) continue;
        const next = squared + ((middle - candidate) * r[row][row]) ** 2;
        if (next > bestSquared + tolerance) continue;
        coefficients[row] = candidate;
        search(row - 1, next);
      }
    }
  }
  if (bestSquared > 0) search(count - 1, 0);
  const result = delta.map((value, axis) => value - basis.reduce((sum, vector, index) => sum + vector[axis] * best[index], 0));
  return { x: result[0], y: result[1], z: result[2] };
}

export function minimumImageDistance(first: Atom | Vector3, second: Atom | Vector3, structure: Pick<Structure, "cell" | "pbc">): number {
  const delta = minimumImageDisplacement(first, second, structure);
  return Math.hypot(delta.x, delta.y, delta.z);
}
