import type { Atom, Structure } from "./formats";

type Vector3 = { x: number; y: number; z: number };

function inverse3(matrix: number[][]): number[][] | null {
  if (matrix.length !== 3 || matrix.some(row => row.length !== 3)) return null;
  const [a, b, c] = matrix;
  const determinant = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]);
  if (Math.abs(determinant) < 1e-12) return null;
  const d = 1 / determinant;
  return [[(b[1] * c[2] - b[2] * c[1]) * d, (a[2] * c[1] - a[1] * c[2]) * d, (a[1] * b[2] - a[2] * b[1]) * d], [(b[2] * c[0] - b[0] * c[2]) * d, (a[0] * c[2] - a[2] * c[0]) * d, (a[2] * b[0] - a[0] * b[2]) * d], [(b[0] * c[1] - b[1] * c[0]) * d, (a[1] * c[0] - a[0] * c[1]) * d, (a[0] * b[1] - a[1] * b[0]) * d]];
}

function fractional(vector: Vector3, inverse: number[][]): number[] {
  return [vector.x * inverse[0][0] + vector.y * inverse[1][0] + vector.z * inverse[2][0], vector.x * inverse[0][1] + vector.y * inverse[1][1] + vector.z * inverse[2][1], vector.x * inverse[0][2] + vector.y * inverse[1][2] + vector.z * inverse[2][2]];
}

function cartesian(vector: number[], cell: number[][]): Vector3 {
  return { x: vector[0] * cell[0][0] + vector[1] * cell[1][0] + vector[2] * cell[2][0], y: vector[0] * cell[0][1] + vector[1] * cell[1][1] + vector[2] * cell[2][1], z: vector[0] * cell[0][2] + vector[1] * cell[1][2] + vector[2] * cell[2][2] };
}

/** Returns the shortest vector from `from` to `to`, respecting enabled PBC axes. */
export function minimumImageDisplacement(from: Vector3, to: Vector3, structure: Pick<Structure, "cell" | "pbc">): Vector3 {
  const direct = { x: to.x - from.x, y: to.y - from.y, z: to.z - from.z };
  if (!structure.cell || !structure.pbc.some(Boolean)) return direct;
  const inverse = inverse3(structure.cell);
  if (!inverse) return direct;
  const delta = fractional(direct, inverse);
  for (let axis = 0; axis < 3; axis += 1) if (structure.pbc[axis]) delta[axis] -= Math.round(delta[axis]);
  return cartesian(delta, structure.cell);
}

export function minimumImageDistance(first: Atom | Vector3, second: Atom | Vector3, structure: Pick<Structure, "cell" | "pbc">): number {
  const delta = minimumImageDisplacement(first, second, structure);
  return Math.hypot(delta.x, delta.y, delta.z);
}
