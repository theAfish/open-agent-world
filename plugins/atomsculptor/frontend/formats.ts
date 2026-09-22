import { minimumImageDistance } from "./periodic";

export type AtomMetadata = Record<string, string | number | boolean | null>;
export type Atom = { id: number; symbol: string; x: number; y: number; z: number; layer_id: string; label?: string; occupancy?: number | null; selective_dynamics?: [boolean, boolean, boolean] | null; metadata?: AtomMetadata };
export type Bond = { first_atom_id: number; second_atom_id: number; order: string; metadata?: AtomMetadata };
export type Layer = {
  id: string;
  name: string;
  kind: "atoms" | "lattice" | "selection" | "annotation";
  visible: boolean;
  cell?: number[][] | null;
  pbc?: [boolean, boolean, boolean] | null;
  metadata?: string;
};
export type InterfaceCandidate = {
  id: number;
  file_name: string;
  formula: string;
  atom_count: number;
  von_mises_strain?: number | null;
  area?: number | null;
  termination_index?: number | null;
};
export type Structure = {
  format_version?: 1;
  atoms: Atom[];
  bonds?: Bond[];
  layers: Layer[];
  active_layer_ids?: string[];
  selected_atom_ids: number[];
  source_name: string;
  source_metadata?: AtomMetadata;
  interface_candidates?: InterfaceCandidate[];
  cell: number[][] | null;
  pbc: [boolean, boolean, boolean];
};

const finite = (value: string) => Number.isFinite(Number(value));
const numeric = (value: string) => finite(value) ? Number(value) : 0;
const cellText = (cell: number[][] | null) => (cell ?? [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
  .flat().map(value => value.toFixed(6)).join(" ");
const pbcText = (pbc: [boolean, boolean, boolean]) => pbc.map(value => value ? "T" : "F").join(" ");

function inverse3(matrix: number[][]): number[][] | null {
  const [a, b, c] = matrix; const determinant = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]); if (Math.abs(determinant) < 1e-12) return null; const d = 1 / determinant;
  return [[(b[1] * c[2] - b[2] * c[1]) * d, (a[2] * c[1] - a[1] * c[2]) * d, (a[1] * b[2] - a[2] * b[1]) * d], [(b[2] * c[0] - b[0] * c[2]) * d, (a[0] * c[2] - a[2] * c[0]) * d, (a[2] * b[0] - a[0] * b[2]) * d], [(b[0] * c[1] - b[1] * c[0]) * d, (a[1] * c[0] - a[0] * c[1]) * d, (a[0] * b[1] - a[1] * b[0]) * d]];
}
function fractional(atom: Atom, inverse: number[][]): number[] { return [atom.x * inverse[0][0] + atom.y * inverse[1][0] + atom.z * inverse[2][0], atom.x * inverse[0][1] + atom.y * inverse[1][1] + atom.z * inverse[2][1], atom.x * inverse[0][2] + atom.y * inverse[1][2] + atom.z * inverse[2][2]]; }

export function downloadStructure(name: string, content: string, type = "text/plain"): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export type StructureFormat = "json" | "xyz" | "extxyz" | "lxyz" | "cif" | "poscar" | "pdb" | "sdf" | "mol2";

export function exportStructure(structure: Structure, format: StructureFormat): string {
  if (format === "json") return JSON.stringify(structure, null, 2);
  if (format === "xyz" || format === "extxyz") {
    return `${structure.atoms.length}\nLattice="${cellText(structure.cell)}" pbc="${pbcText(structure.pbc)}"\n${structure.atoms.map(atom => `${atom.symbol} ${atom.x} ${atom.y} ${atom.z}`).join("\n")}\n`;
  }
  if (format === "poscar") return exportPoscar(structure);
  if (format === "pdb") return exportPdb(structure);
  if (format === "sdf") return exportSdf(structure);
  if (format === "mol2") return exportMol2(structure);
  if (format === "cif") return exportCif(structure);
  const lines = ["[lattice layer]", cellText(structure.cell)];
  structure.layers.filter(layer => layer.kind === "atoms").forEach((layer, index) => {
    lines.push(`[<atoms layer ${index + 1}>]`);
    lines.push(`Lattice="${cellText(layer.cell ?? structure.cell)}" pbc="${pbcText(layer.pbc ?? structure.pbc)}" Properties=species:S:1:pos:R:3`);
    structure.atoms.filter(atom => atom.layer_id === layer.id).forEach(atom => {
      lines.push(`${atom.symbol} ${atom.x.toFixed(6)} ${atom.y.toFixed(6)} ${atom.z.toFixed(6)}`);
    });
  });
  return `${lines.join("\n")}\n`;
}

function exportPoscar(structure: Structure): string {
  const cell = structure.cell ?? [[1, 0, 0], [0, 1, 0], [0, 0, 1]]; const symbols = [...new Set(structure.atoms.map(atom => atom.symbol))]; const counts = symbols.map(symbol => structure.atoms.filter(atom => atom.symbol === symbol).length);
  const selective = structure.atoms.some(atom => atom.selective_dynamics);
  return `${structure.source_name || "AtomSculptor"}\n1.0\n${cell.map(row => row.map(value => value.toFixed(8)).join(" ")).join("\n")}\n${symbols.join(" ")}\n${counts.join(" ")}\n${selective ? "Selective dynamics\n" : ""}Cartesian\n${structure.atoms.map(atom => `${atom.x.toFixed(8)} ${atom.y.toFixed(8)} ${atom.z.toFixed(8)}${selective ? ` ${(atom.selective_dynamics ?? [true, true, true]).map(value => value ? "T" : "F").join(" ")}` : ""}`).join("\n")}\n`;
}

function exportPdb(structure: Structure): string {
  const records = structure.atoms.map((atom, index) => {
    const data = atom.metadata ?? {}; const record = data.pdb_record === "HETATM" ? "HETATM" : "ATOM  "; const name = String(data.pdb_name ?? atom.symbol).slice(0, 4).padStart(4); const alt = String(data.pdb_alt_loc ?? " ").slice(0, 1); const residue = String(data.pdb_residue ?? "MOL").slice(0, 3).padStart(3); const chain = String(data.pdb_chain ?? "A").slice(0, 1); const residueId = String(data.pdb_residue_id ?? "1").padStart(4); const occupancy = typeof atom.occupancy === "number" ? atom.occupancy : Number(data.pdb_occupancy ?? 1); const temperature = Number(data.pdb_temperature_factor ?? 0); const charge = String(data.pdb_charge ?? "").slice(0, 2).padStart(2);
    return `${record}${String(index + 1).padStart(5)} ${name}${alt}${residue} ${chain}${residueId}    ${atom.x.toFixed(3).padStart(8)}${atom.y.toFixed(3).padStart(8)}${atom.z.toFixed(3).padStart(8)}${(Number.isFinite(occupancy) ? occupancy : 1).toFixed(2).padStart(6)}${(Number.isFinite(temperature) ? temperature : 0).toFixed(2).padStart(6)}          ${atom.symbol.padStart(2)}${charge}`;
  });
  return `${records.join("\n")}\nEND\n`;
}

function exportSdf(structure: Structure): string {
  const bonds = explicitOrGuessedBonds(structure); const indexById = new Map(structure.atoms.map((atom, index) => [atom.id, index + 1])); const header = `${structure.source_name || "AtomSculptor"}\n  AtomSculptor\n\n${String(structure.atoms.length).padStart(3)}${String(bonds.length).padStart(3)}  0  0  0  0            999 V2000\n`;
  const atoms = structure.atoms.map(atom => `${atom.x.toFixed(4).padStart(10)}${atom.y.toFixed(4).padStart(10)}${atom.z.toFixed(4).padStart(10)} ${atom.symbol.padEnd(3)} 0  ${sdfChargeCode(atom.metadata?.sdf_formal_charge)}  0  0  0  0  0  0  0  0  0  0`).join("\n");
  const lines = bonds.map(bond => `${String(indexById.get(bond.first_atom_id) ?? 0).padStart(3)}${String(indexById.get(bond.second_atom_id) ?? 0).padStart(3)}${String(sdfBondOrder(bond.order)).padStart(3)}  0  0  0  0`).join("\n"); return `${header}${atoms}\n${lines}${lines ? "\n" : ""}M  END\n$$$$\n`;
}

function exportMol2(structure: Structure): string {
  const bonds = explicitOrGuessedBonds(structure); const indexById = new Map(structure.atoms.map((atom, index) => [atom.id, index + 1])); return `@<TRIPOS>MOLECULE\n${structure.source_name || "AtomSculptor"}\n${structure.atoms.length} ${bonds.length} 0 0 0\nSMALL\nUSER_CHARGES\n\n@<TRIPOS>ATOM\n${structure.atoms.map((atom, index) => `${index + 1} ${String(atom.metadata?.mol2_name ?? `${atom.symbol}${index + 1}`)} ${atom.x.toFixed(6)} ${atom.y.toFixed(6)} ${atom.z.toFixed(6)} ${String(atom.metadata?.mol2_type ?? atom.symbol)} ${String(atom.metadata?.mol2_substructure_id ?? 1)} ${String(atom.metadata?.mol2_substructure_name ?? "MOL")} ${Number(atom.metadata?.mol2_charge ?? 0).toFixed(4)}`).join("\n")}\n@<TRIPOS>BOND\n${bonds.map((bond, index) => `${index + 1} ${indexById.get(bond.first_atom_id) ?? 0} ${indexById.get(bond.second_atom_id) ?? 0} ${bond.order}`).join("\n")}\n`;
}

function exportCif(structure: Structure): string {
  const cell = structure.cell ?? [[1, 0, 0], [0, 1, 0], [0, 0, 1]]; const lengths = cell.map(row => Math.hypot(...row)); const angle = (a: number[], b: number[]) => Math.acos((a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (Math.hypot(...a) * Math.hypot(...b))) * 180 / Math.PI; const inverse = inverse3(cell); const fractionalAtoms = inverse ? structure.atoms.map(atom => fractional(atom, inverse)) : structure.atoms.map(atom => [atom.x, atom.y, atom.z]);
  // Coordinates are exported as the fully expanded P1 set, the same convention
  // ASE and pymatgen writers use; partial occupancies are preserved explicitly.
  const partialOccupancy = structure.atoms.some(atom => atom.occupancy != null && Math.abs(atom.occupancy - 1) > 1e-6);
  const tags = ["_atom_site_label", "_atom_site_type_symbol", "_atom_site_fract_x", "_atom_site_fract_y", "_atom_site_fract_z", ...(partialOccupancy ? ["_atom_site_occupancy"] : [])];
  return `data_${(structure.source_name || "atomsculptor").replace(/\W+/g, "_")}\n_cell_length_a ${lengths[0].toFixed(8)}\n_cell_length_b ${lengths[1].toFixed(8)}\n_cell_length_c ${lengths[2].toFixed(8)}\n_cell_angle_alpha ${angle(cell[1], cell[2]).toFixed(8)}\n_cell_angle_beta ${angle(cell[0], cell[2]).toFixed(8)}\n_cell_angle_gamma ${angle(cell[0], cell[1]).toFixed(8)}\n_symmetry_space_group_name_H-M 'P 1'\n_symmetry_Int_Tables_number 1\nloop_\n${tags.join("\n")}\n${structure.atoms.map((atom, index) => [`${atom.symbol}${index + 1}`, atom.symbol, ...fractionalAtoms[index].map(value => value.toFixed(8)), ...(partialOccupancy ? [(atom.occupancy ?? 1).toFixed(6)] : [])].join(" ")).join("\n")}\n`;
}

function guessedBonds(structure: Structure): Bond[] { const bonds: Bond[] = []; for (let first = 0; first < structure.atoms.length; first += 1) for (let second = first + 1; second < structure.atoms.length; second += 1) { const a = structure.atoms[first]; const b = structure.atoms[second]; if (minimumImageDistance(a, b, structure) < 2.1) bonds.push({ first_atom_id: a.id, second_atom_id: b.id, order: "1" }); } return bonds; }
const explicitOrGuessedBonds = (structure: Structure) => structure.bonds?.length ? structure.bonds : guessedBonds(structure);
const sdfBondOrder = (order: string) => /^\d+$/.test(order) ? Math.min(4, Math.max(1, Number(order))) : order === "ar" ? 4 : 1;
const sdfChargeCode = (charge: unknown) => ({ "3": 1, "2": 2, "1": 3, "-1": 5, "-2": 6, "-3": 7 }[String(charge ?? "")] ?? 0);
const sdfFormalCharge = (code: string | undefined): number | null => ({ "1": 3, "2": 2, "3": 1, "5": -1, "6": -2, "7": -3 }[code ?? ""] ?? null);

function parsePbc(text: string): [boolean, boolean, boolean] {
  const values = /pbc\s*=\s*"([^"]+)"/i.exec(text)?.[1].toLowerCase().split(/\s+/) ?? [];
  return [values[0], values[1], values[2]].map(value => ["t", "true", "1", "yes"].includes(value)) as [boolean, boolean, boolean];
}

function parseCell(text: string): number[][] | null {
  const values = /lattice\s*=\s*"([^"]+)"/i.exec(text)?.[1].trim().split(/\s+/).map(Number) ?? [];
  return values.length === 9 && values.every(Number.isFinite)
    ? [values.slice(0, 3), values.slice(3, 6), values.slice(6, 9)]
    : null;
}

export function parseStructure(text: string, sourceName: string): Structure {
  if (sourceName.toLowerCase().endsWith(".json")) {
    const document = JSON.parse(text) as Structure;
    return { ...document, source_name: sourceName, active_layer_ids: document.active_layer_ids ?? [document.layers?.[0]?.id ?? "atoms"] };
  }
  const lines = text.split(/\r?\n/);
  const lowerName = sourceName.toLowerCase();
  if (lowerName.endsWith(".cif")) return parseCif(lines, sourceName);
  if (lowerName.endsWith(".pdb")) return parsePdb(lines, sourceName);
  if (lowerName.endsWith(".sdf") || lowerName.endsWith(".mol")) return parseSdf(lines, sourceName);
  if (lowerName.endsWith(".mol2")) return parseMol2(lines, sourceName);
  if (lowerName.endsWith(".vasp") || /(^|\/)(poscar|contcar)$/i.test(sourceName)) return parsePoscar(lines, sourceName);
  const lxyz = sourceName.toLowerCase().endsWith(".lxyz") || lines.some(line => /^\[\s*lattice\s+layer\s*\]$/i.test(line.trim()));
  if (lxyz) return parseLxyz(lines, sourceName);
  const count = Number(lines[0]?.trim());
  const start = Number.isInteger(count) && count >= 0 ? 2 : 0;
  const comment = start ? lines[1] ?? "" : "";
  const atoms: Atom[] = [];
  lines.slice(start).forEach(line => {
    const [symbol, x, y, z] = line.trim().split(/\s+/);
    if (symbol && [x, y, z].every(value => value !== undefined && finite(value))) {
      atoms.push({ id: atoms.length, symbol, x: numeric(x), y: numeric(y), z: numeric(z), layer_id: "atoms" });
    }
  });
  return {
    atoms,
    layers: [{ id: "atoms", name: "Atoms", kind: "atoms", visible: true }],
    active_layer_ids: ["atoms"],
    selected_atom_ids: [],
    source_name: sourceName,
    cell: parseCell(comment),
    pbc: parsePbc(comment),
  };
}

const defaultLayer = () => [{ id: "atoms", name: "Atoms", kind: "atoms" as const, visible: true }];
const documentFromAtoms = (atoms: Atom[], source_name: string, cell: number[][] | null = null, pbc: [boolean, boolean, boolean] = [false, false, false]): Structure => ({ atoms, layers: defaultLayer(), active_layer_ids: ["atoms"], selected_atom_ids: [], source_name, cell, pbc });
const symbolFrom = (value: string) => (/^[A-Z][a-z]?/.exec(value.trim())?.[0] ?? "X");

function parsePdb(lines: string[], sourceName: string): Structure {
  const atoms = lines.filter(line => /^(ATOM  |HETATM)/.test(line)).map((line, id) => ({ id, symbol: symbolFrom(line.slice(76, 78).trim() || line.slice(12, 16)), x: Number(line.slice(30, 38)), y: Number(line.slice(38, 46)), z: Number(line.slice(46, 54)), layer_id: "atoms", label: line.slice(12, 16).trim(), occupancy: Number(line.slice(54, 60)), metadata: { pdb_record: line.slice(0, 6).trim(), pdb_name: line.slice(12, 16).trim(), pdb_alt_loc: line.slice(16, 17).trim(), pdb_residue: line.slice(17, 20).trim(), pdb_chain: line.slice(21, 22).trim(), pdb_residue_id: line.slice(22, 26).trim(), pdb_temperature_factor: Number(line.slice(60, 66)), pdb_charge: line.slice(78, 80).trim() } })).filter(atom => [atom.x, atom.y, atom.z].every(Number.isFinite)).map(atom => ({ ...atom, occupancy: Number.isFinite(atom.occupancy) ? atom.occupancy : null, metadata: { ...atom.metadata, pdb_temperature_factor: Number.isFinite(Number(atom.metadata.pdb_temperature_factor)) ? atom.metadata.pdb_temperature_factor : null } })); return documentFromAtoms(atoms, sourceName);
}

function parseSdf(lines: string[], sourceName: string): Structure {
  const count = lines[3]?.trim().split(/\s+/).map(Number) ?? []; const atoms: Atom[] = []; const atomCount = count[0] ?? 0; const bondCount = count[1] ?? 0; for (let index = 0; index < atomCount; index += 1) { const fields = lines[index + 4]?.trim().split(/\s+/) ?? []; const [x, y, z, symbol] = fields; if (symbol && [x, y, z].every(finite)) atoms.push({ id: atoms.length, symbol: symbolFrom(symbol), x: numeric(x), y: numeric(y), z: numeric(z), layer_id: "atoms", metadata: { sdf_formal_charge: sdfFormalCharge(fields[5]) } }); } const bonds: Bond[] = []; for (let index = 0; index < bondCount; index += 1) { const [first, second, order] = lines[4 + atomCount + index]?.trim().split(/\s+/) ?? []; const firstId = Number(first) - 1; const secondId = Number(second) - 1; if (Number.isInteger(firstId) && Number.isInteger(secondId) && atoms[firstId] && atoms[secondId] && order) bonds.push({ first_atom_id: firstId, second_atom_id: secondId, order }); } return { ...documentFromAtoms(atoms, sourceName), bonds };
}

function parseMol2(lines: string[], sourceName: string): Structure {
  const start = lines.findIndex(line => /^@<TRIPOS>ATOM/i.test(line)); const end = lines.findIndex((line, index) => index > start && /^@<TRIPOS>/i.test(line)); const atoms: Atom[] = []; const sourceIds = new Map<number, number>(); for (const line of lines.slice(start + 1, end < 0 ? undefined : end)) { const fields = line.trim().split(/\s+/); if (fields.length >= 6 && [fields[2], fields[3], fields[4]].every(finite)) { const sourceId = Number(fields[0]); const atom = { id: atoms.length, symbol: symbolFrom(fields[5]), x: numeric(fields[2]), y: numeric(fields[3]), z: numeric(fields[4]), layer_id: "atoms", label: fields[1], metadata: { mol2_name: fields[1], mol2_type: fields[5], mol2_substructure_id: Number(fields[6] ?? 1), mol2_substructure_name: fields[7] ?? "MOL", mol2_charge: Number(fields[8] ?? 0) } }; atoms.push(atom); if (Number.isInteger(sourceId)) sourceIds.set(sourceId, atom.id); } } const bondStart = lines.findIndex(line => /^@<TRIPOS>BOND/i.test(line)); const bondEnd = lines.findIndex((line, index) => index > bondStart && /^@<TRIPOS>/i.test(line)); const bonds: Bond[] = []; if (bondStart >= 0) for (const line of lines.slice(bondStart + 1, bondEnd < 0 ? undefined : bondEnd)) { const [, first, second, order] = line.trim().split(/\s+/); const firstId = sourceIds.get(Number(first)); const secondId = sourceIds.get(Number(second)); if (firstId !== undefined && secondId !== undefined && order) bonds.push({ first_atom_id: firstId, second_atom_id: secondId, order }); } return { ...documentFromAtoms(atoms, sourceName), bonds };
}

function parsePoscar(lines: string[], sourceName: string): Structure {
  const scale = Number(lines[1]) || 1; const cell = lines.slice(2, 5).map(line => line.trim().split(/\s+/).map(value => Number(value) * scale)); if (cell.length !== 3 || cell.some(row => row.length < 3 || row.some(value => !Number.isFinite(value)))) return documentFromAtoms([], sourceName);
  let cursor = 5; let symbols = lines[cursor]?.trim().split(/\s+/) ?? []; if (symbols.every(value => /^\d+$/.test(value))) symbols = symbols.map((_, index) => `X${index + 1}`); else cursor += 1; const counts = lines[cursor]?.trim().split(/\s+/).map(Number) ?? []; cursor += 1; const selective = /^s/i.test(lines[cursor]?.trim() ?? ""); if (selective) cursor += 1; const direct = /^d/i.test(lines[cursor]?.trim() ?? ""); cursor += 1;
  const atoms: Atom[] = []; let symbolIndex = 0; let remaining = counts[0] ?? 0; for (const line of lines.slice(cursor)) { const fields = line.trim().split(/\s+/); const values = fields.slice(0, 3).map(Number); if (values.length !== 3 || values.some(value => !Number.isFinite(value))) continue; while (remaining === 0 && symbolIndex < counts.length - 1) { symbolIndex += 1; remaining = counts[symbolIndex]; } const position = direct ? { x: values[0] * cell[0][0] + values[1] * cell[1][0] + values[2] * cell[2][0], y: values[0] * cell[0][1] + values[1] * cell[1][1] + values[2] * cell[2][1], z: values[0] * cell[0][2] + values[1] * cell[1][2] + values[2] * cell[2][2] } : { x: values[0] * scale, y: values[1] * scale, z: values[2] * scale }; const flags = selective && fields.length >= 6 ? fields.slice(3, 6).map(value => /^t/i.test(value)) as [boolean, boolean, boolean] : null; atoms.push({ id: atoms.length, symbol: symbolFrom(symbols[symbolIndex] ?? "X"), ...position, layer_id: "atoms", selective_dynamics: flags }); remaining -= 1; if (atoms.length >= counts.reduce((sum, value) => sum + value, 0)) break; } return documentFromAtoms(atoms, sourceName, cell.map(row => row.slice(0, 3)), [true, true, true]);
}

const cifNumber = (value: string | undefined): number | null => {
  if (value === undefined) return null;
  const number = Number(value.replace(/\(.+\)/, ""));
  return Number.isFinite(number) ? number : null;
};

/** Evaluate a CIF operation term such as "", "-", "1/2", "0.25" or "-2/3". */
const fractionValue = (text: string): number | null => {
  if (text === "" || text === "+") return 1;
  if (text === "-") return -1;
  const match = /^([+-]?)(\d+(?:\.\d+)?)(?:\/(\d+(?:\.\d+)?))?$/.exec(text);
  if (!match) return null;
  const denominator = match[3] ? Number(match[3]) : 1;
  return denominator ? (match[1] === "-" ? -1 : 1) * Number(match[2]) / denominator : null;
};

/** Parse "x, y, -z+1/2" into three [x, y, z, constant] transform rows. */
function parseSymmetryOperation(text: string): number[][] | null {
  const parts = text.split(",").map(part => part.replace(/\s+/g, "").replace(/^['"]|['"]$/g, ""));
  if (parts.length !== 3 || parts.some(part => !part)) return null;
  const rows: number[][] = [];
  for (const part of parts) {
    const row = [0, 0, 0, 0];
    for (const token of part.match(/[+-]?[^+-]+/g) ?? []) {
      const axis = /([xyz])$/i.exec(token)?.[1]?.toLowerCase();
      const value = axis ? fractionValue(token.slice(0, -1)) : fractionValue(token);
      if (value === null) return null;
      if (axis) row["xyz".indexOf(axis)] += value;
      else row[3] += value;
    }
    rows.push(row);
  }
  return rows;
}

function cifSymmetry(lines: string[]): { operations: number[][][]; spaceGroup: string | null } {
  const operations: number[][][] = [];
  const tag = /(_symmetry_equiv_pos_as_xyz|_space_group_symop_operation_xyz)$/i;
  const nameTag = /^(_symmetry_space_group_name_H-M|_space_group_name_H-M_alt)\s+(.+)$/;
  let spaceGroup: string | null = null;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    const named = nameTag.exec(line);
    if (named) spaceGroup ??= named[2].replace(/^['"]|['"]$/g, "");
    if (!/^loop_$/i.test(line)) continue;
    let cursor = index + 1;
    const headers: string[] = [];
    while (cursor < lines.length && lines[cursor].trim().startsWith("_")) { headers.push(lines[cursor].trim().split(/\s+/)[0]); cursor += 1; }
    const column = headers.findIndex(header => tag.test(header));
    if (column < 0) { index = cursor - 1; continue; }
    for (; cursor < lines.length; cursor += 1) {
      const entry = lines[cursor].trim();
      if (!entry || entry.startsWith("#") || entry.startsWith("_") || /^loop_/i.test(entry) || entry.startsWith("data_")) break;
      const values = cifValues(entry);
      const operation = parseSymmetryOperation(values[column] ?? "");
      if (operation) operations.push(operation);
    }
    index = cursor - 1;
  }
  if (!operations.length) {
    for (const line of lines) {
      const match = /^(_symmetry_equiv_pos_as_xyz|_space_group_symop_operation_xyz)\s+(.+)$/.exec(line.trim());
      if (!match) continue;
      const operation = parseSymmetryOperation(match[2].replace(/^['"]|['"]$/g, ""));
      if (operation) operations.push(operation);
    }
  }
  return { operations, spaceGroup };
}

const wrapFraction = (value: number) => {
  const wrapped = value - Math.floor(value);
  return Math.abs(wrapped) < 1e-5 || Math.abs(wrapped - 1) < 1e-5 ? 0 : Number(wrapped.toFixed(5));
};

/** Split a CIF data row while keeping quoted values such as 'x, y, z' intact. */
const cifValues = (line: string): string[] => {
  const values: string[] = [];
  for (const match of line.matchAll(/'([^']*)'|"([^"]*)"|(\S+)/g)) {
    values.push(match[1] ?? match[2] ?? match[3] ?? "");
  }
  return values;
};

function parseCif(lines: string[], sourceName: string): Structure {
  const scalar = (name: string) => Number(lines.find(line => line.trim().startsWith(name))?.trim().split(/\s+/)[1]); const a = scalar("_cell_length_a"); const b = scalar("_cell_length_b"); const c = scalar("_cell_length_c"); const alpha = scalar("_cell_angle_alpha") * Math.PI / 180; const beta = scalar("_cell_angle_beta") * Math.PI / 180; const gamma = scalar("_cell_angle_gamma") * Math.PI / 180; const cell = [a, b, c, alpha, beta, gamma].every(Number.isFinite) ? [[a, 0, 0], [b * Math.cos(gamma), b * Math.sin(gamma), 0], [c * Math.cos(beta), c * (Math.cos(alpha) - Math.cos(beta) * Math.cos(gamma)) / Math.sin(gamma), 0]] : null; if (cell) cell[2][2] = Math.sqrt(Math.max(0, c * c - cell[2][0] ** 2 - cell[2][1] ** 2));
  const { operations, spaceGroup } = cifSymmetry(lines);
  const header = lines.findIndex(line => /_atom_site_(fract_x|cartn_x)/i.test(line)); if (header < 0) return documentFromAtoms([], sourceName, cell, cell ? [true, true, true] : [false, false, false]); let first = header; while (first > 0 && lines[first - 1].trim().startsWith("_atom_site_")) first -= 1; const fields: string[] = []; let cursor = first; while (cursor < lines.length && lines[cursor].trim().startsWith("_atom_site_")) { fields.push(lines[cursor].trim().split(/\s+/)[0]); cursor += 1; }
  const symbolColumn = fields.findIndex(field => /type_symbol|label$/i.test(field)); const xColumn = fields.findIndex(field => /fract_x|cartn_x/i.test(field)); const yColumn = fields.findIndex(field => /fract_y|cartn_y/i.test(field)); const zColumn = fields.findIndex(field => /fract_z|cartn_z/i.test(field)); const occupancyColumn = fields.findIndex(field => /_occupancy$/i.test(field)); const disorderColumn = fields.findIndex(field => /_disorder_group$/i.test(field));
  const fractionalCoordinates = fields.some(field => /fract_/.test(field)); const cartesian = (fraction: number[], basis: number[][]) => ({ x: fraction[0] * basis[0][0] + fraction[1] * basis[1][0] + fraction[2] * basis[2][0], y: fraction[0] * basis[0][1] + fraction[1] * basis[1][1] + fraction[2] * basis[2][1], z: fraction[0] * basis[0][2] + fraction[1] * basis[1][2] + fraction[2] * basis[2][2] });
  interface Site { symbol: string; label: string; fraction: number[] | null; position: { x: number; y: number; z: number }; occupancy: number | null; disorderGroup: number | null }
  const sites: Site[] = [];
  for (const line of lines.slice(cursor)) { if (!line.trim() || line.trim().startsWith("_") || /^loop_/i.test(line)) break; const values = cifValues(line.trim()); const coords = [cifNumber(values[xColumn]), cifNumber(values[yColumn]), cifNumber(values[zColumn])]; if (symbolColumn < 0 || coords.some(value => value === null)) continue; const label = values[fields.findIndex(field => /_label$/i.test(field))] ?? ""; const occupancy = occupancyColumn >= 0 ? cifNumber(values[occupancyColumn]) : null; const disorder = disorderColumn >= 0 ? cifNumber(values[disorderColumn]) : null; sites.push({ symbol: symbolFrom(values[symbolColumn]), label, fraction: fractionalCoordinates ? coords as number[] : null, position: fractionalCoordinates && cell ? cartesian(coords as number[], cell) : { x: coords[0] as number, y: coords[1] as number, z: coords[2] as number }, occupancy: occupancy === null || Number.isFinite(occupancy) ? occupancy : null, disorderGroup: disorder !== null && Number.isInteger(disorder) ? disorder : null }); }
  // Expand the asymmetric unit with the file's own symmetry operations. Sites
  // from different disorder groups are alternatives, never merged; duplicates
  // produced by equivalent operations collapse on wrapped fractional position.
  const expand = operations.length > 1 && cell && fractionalCoordinates;
  const atoms: Atom[] = []; const seen = new Set<string>();
  const addSite = (site: Site, fraction: number[], multiplicity: number) => {
    const key = fraction.map(wrapFraction).map(value => value.toFixed(4)).join(",") + `|${site.disorderGroup ?? ""}`;
    if (seen.has(key)) return; seen.add(key);
    const position = cell ? cartesian(fraction, cell) : site.position;
    atoms.push({ id: atoms.length, symbol: site.symbol, ...position, layer_id: "atoms", label: site.label, occupancy: site.occupancy, metadata: { cif_label: site.label, symmetry_multiplicity: multiplicity, ...(site.disorderGroup !== null ? { cif_disorder_group: site.disorderGroup } : {}) } });
  };
  for (const site of sites) {
    if (!expand || !site.fraction) { atoms.push({ id: atoms.length, symbol: site.symbol, ...site.position, layer_id: "atoms", label: site.label, occupancy: site.occupancy, metadata: { cif_label: site.label, ...(site.disorderGroup !== null ? { cif_disorder_group: site.disorderGroup } : {}) } }); continue; }
    let produced = 0;
    for (const operation of operations) {
      const fraction = [0, 1, 2].map(axis => operation[axis][0] * site.fraction![0] + operation[axis][1] * site.fraction![1] + operation[axis][2] * site.fraction![2] + operation[axis][3]);
      const before = atoms.length; addSite(site, fraction, operations.length); if (atoms.length > before) produced += 1;
      if (atoms.length > 20_000) throw new Error("CIF symmetry expansion exceeds the 20,000 atom document limit");
    }
    if (!produced) addSite(site, site.fraction, 1);
  }
  const structure = documentFromAtoms(atoms, sourceName, cell, cell ? [true, true, true] : [false, false, false]);
  return { ...structure, source_metadata: { ...(spaceGroup ? { cif_space_group: spaceGroup } : {}), ...(operations.length ? { cif_symmetry_operations: operations.length, cif_expanded_to_p1: expand } : {}) } };
}

function parseLxyz(lines: string[], sourceName: string): Structure {
  let cell: number[][] | null = null;
  let pbc: [boolean, boolean, boolean] = [false, false, false];
  let layerId = "atoms-1";
  let expectingCell = false;
  const layers: Layer[] = [];
  const atoms: Atom[] = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (/^\[\s*lattice\s+layer\s*\]$/i.test(line)) { expectingCell = true; continue; }
    const layer = /^\[\s*<?\s*atoms\s+layer\s+(\d+)\s*>?\s*\]$/i.exec(line);
    if (layer) { layerId = `atoms-${layer[1]}`; layers.push({ id: layerId, name: `Atoms ${layer[1]}`, kind: "atoms", visible: true }); expectingCell = false; continue; }
    if (expectingCell) {
      const values = line.split(/\s+/).map(Number);
      if (values.length === 9 && values.every(Number.isFinite)) cell = [values.slice(0, 3), values.slice(3, 6), values.slice(6, 9)];
      expectingCell = false;
      continue;
    }
    if (/lattice\s*=|pbc\s*=/i.test(line)) {
      const layerCell = parseCell(line);
      const layerPbc = parsePbc(line);
      cell = layerCell ?? cell;
      pbc = layerPbc;
      const activeLayer = layers.find(candidate => candidate.id === layerId);
      if (activeLayer) { activeLayer.cell = layerCell; activeLayer.pbc = layerPbc; }
      continue;
    }
    const [symbol, x, y, z] = line.split(/\s+/);
    if (symbol && [x, y, z].every(value => value !== undefined && finite(value))) {
      atoms.push({ id: atoms.length, symbol, x: numeric(x), y: numeric(y), z: numeric(z), layer_id: layerId });
    }
  }
  if (!layers.length) layers.push({ id: "atoms", name: "Atoms", kind: "atoms", visible: true });
  return { atoms, layers, active_layer_ids: [layers[0].id], selected_atom_ids: [], source_name: sourceName, cell, pbc };
}
