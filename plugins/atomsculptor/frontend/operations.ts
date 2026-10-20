import type { Atom, Layer, Structure } from "./formats";
import { minimumImageDistance } from "./periodic";

const clone = (value: Structure) => JSON.parse(JSON.stringify(value)) as Structure;
const atomLayers = (structure: Structure) => structure.layers.filter(layer => layer.kind === "atoms");
const activeAtoms = (structure: Structure) => new Set((structure.active_layer_ids ?? []).filter(id => structure.layers.some(layer => layer.id === id && layer.kind === "atoms")));
const nextLayer = (structure: Structure) => `atoms-${Math.max(0, ...structure.layers.map(layer => Number(/atoms-(\d+)$/.exec(layer.id)?.[1]) || 0)) + 1}`;

export function deleteActiveLayers(structure: Structure): Structure | null {
  const active = activeAtoms(structure); if (!active.size || atomLayers(structure).length <= active.size) return null;
  const next = clone(structure); next.layers = next.layers.filter(layer => !active.has(layer.id)); next.atoms = next.atoms.filter(atom => !active.has(atom.layer_id)); next.selected_atom_ids = next.selected_atom_ids.filter(id => next.atoms.some(atom => atom.id === id)); next.active_layer_ids = [atomLayers(next)[0].id]; return next;
}

export function mergeActiveLayers(structure: Structure): Structure | null {
  const selected = atomLayers(structure).filter(layer => activeAtoms(structure).has(layer.id)); if (selected.length < 2) return null;
  const next = clone(structure); const [target, ...merged] = selected; const ids = new Set(merged.map(layer => layer.id)); next.atoms = next.atoms.map(atom => ids.has(atom.layer_id) ? { ...atom, layer_id: target.id } : atom); next.layers = next.layers.filter(layer => !ids.has(layer.id)); next.active_layer_ids = [target.id]; return next;
}

export function extractSelection(structure: Structure): Structure | null {
  if (!structure.selected_atom_ids.length) return null;
  const next = clone(structure); const id = nextLayer(next); next.layers.push({ id, name: `Atoms ${atomLayers(next).length + 1}`, kind: "atoms", visible: true, cell: next.cell, pbc: next.pbc }); const selected = new Set(next.selected_atom_ids); next.atoms = next.atoms.map(atom => selected.has(atom.id) ? { ...atom, layer_id: id } : atom); next.active_layer_ids = [id]; return next;
}

export function selectByTypes(structure: Structure, raw: string): number[] {
  const symbols = new Set(raw.split(/[\s,]+/).map(value => value.trim()).filter(Boolean)); const active = activeAtoms(structure);
  return structure.atoms.filter(atom => active.has(atom.layer_id) && symbols.has(atom.symbol)).map(atom => atom.id);
}

export function invertSelection(structure: Structure): number[] {
  const selected = new Set(structure.selected_atom_ids); const active = activeAtoms(structure);
  return structure.atoms.filter(atom => active.has(atom.layer_id) && !selected.has(atom.id)).map(atom => atom.id);
}

export function expandSelection(structure: Structure, distance: number): number[] {
  if (!Number.isFinite(distance) || distance <= 0) return structure.selected_atom_ids;
  const active = activeAtoms(structure); const selected = structure.atoms.filter(atom => structure.selected_atom_ids.includes(atom.id)); const result = new Set(structure.selected_atom_ids); const limit = distance * distance;
  for (const atom of structure.atoms) if (active.has(atom.layer_id)) for (const source of selected) { if (minimumImageDistance(atom, source, structure) ** 2 <= limit) { result.add(atom.id); break; } }
  return [...result];
}

function inverse3(matrix: number[][]): number[][] | null {
  const [a, b, c] = matrix; const determinant = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]); if (Math.abs(determinant) < 1e-12) return null; const d = 1 / determinant;
  return [[(b[1] * c[2] - b[2] * c[1]) * d, (a[2] * c[1] - a[1] * c[2]) * d, (a[1] * b[2] - a[2] * b[1]) * d], [(b[2] * c[0] - b[0] * c[2]) * d, (a[0] * c[2] - a[2] * c[0]) * d, (a[2] * b[0] - a[0] * b[2]) * d], [(b[0] * c[1] - b[1] * c[0]) * d, (a[1] * c[0] - a[0] * c[1]) * d, (a[0] * b[1] - a[1] * b[0]) * d]];
}
const fractional = (atom: Atom, inverse: number[][]) => [atom.x * inverse[0][0] + atom.y * inverse[1][0] + atom.z * inverse[2][0], atom.x * inverse[0][1] + atom.y * inverse[1][1] + atom.z * inverse[2][1], atom.x * inverse[0][2] + atom.y * inverse[1][2] + atom.z * inverse[2][2]];
const cartesian = (fraction: number[], cell: number[][]) => ({ x: fraction[0] * cell[0][0] + fraction[1] * cell[1][0] + fraction[2] * cell[2][0], y: fraction[0] * cell[0][1] + fraction[1] * cell[1][1] + fraction[2] * cell[2][1], z: fraction[0] * cell[0][2] + fraction[1] * cell[1][2] + fraction[2] * cell[2][2] });

export function wrapToCell(structure: Structure): Structure | null {
  if (!structure.cell) return null; const inverse = inverse3(structure.cell); if (!inverse) return null; const next = clone(structure);
  next.atoms = next.atoms.map(atom => ({ ...atom, ...cartesian(fractional(atom, inverse).map(value => value - Math.floor(value)), next.cell as number[][]) })); return next;
}

export function applyLattice(structure: Structure, cell: number[][], scaleAtoms: boolean): Structure | null {
  if (cell.length !== 3 || cell.some(row => row.length !== 3 || row.some(value => !Number.isFinite(value)))) return null;
  const next = clone(structure); const previous = next.cell; next.cell = cell.map(row => [...row]); next.layers = next.layers.map(layer => layer.kind === "lattice" ? { ...layer, cell: next.cell } : layer);
  if (scaleAtoms && previous) { const inverse = inverse3(previous); if (!inverse) return null; next.atoms = next.atoms.map(atom => ({ ...atom, ...cartesian(fractional(atom, inverse), next.cell as number[][]) })); }
  return next;
}

export function useLayerLattice(structure: Structure, id: string): Structure | null {
  const source = structure.layers.find(layer => layer.id === id); if (!source?.cell) return null; const next = clone(structure); next.cell = source.cell.map(row => [...row]); next.pbc = source.pbc ?? next.pbc; return next;
}

export function addLayer(structure: Structure): Structure {
  const next = clone(structure); const id = nextLayer(next); next.layers.push({ id, name: `Atoms ${atomLayers(next).length + 1}`, kind: "atoms", visible: true, cell: next.cell, pbc: next.pbc }); next.active_layer_ids = [id]; return next;
}

export function latticeLayer(structure: Structure): Layer | undefined { return structure.layers.find(layer => layer.kind === "lattice"); }
