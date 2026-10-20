import { describe, expect, it } from "vitest";
import { exportStructure, parseStructure, type Structure } from "../../../plugins/atomsculptor/frontend/formats";
import { minimumImageDisplacement, minimumImageDistance } from "../../../plugins/atomsculptor/frontend/periodic";

const origin = { x: 0, y: 0, z: 0 };

describe("AtomSculptor POSCAR export", () => {
  it("keeps mixed species coordinates and selective constraints together", () => {
    const structure: Structure = {
      atoms: [
        { id: 7, symbol: "C", x: 1, y: 0, z: 0, layer_id: "atoms", selective_dynamics: [true, false, true] },
        { id: 8, symbol: "H", x: 2, y: 0, z: 0, layer_id: "atoms", selective_dynamics: [false, true, false] },
        { id: 9, symbol: "C", x: 3, y: 0, z: 0, layer_id: "atoms" },
      ],
      layers: [{ id: "atoms", name: "Atoms", kind: "atoms", visible: true }],
      selected_atom_ids: [], source_name: "mixed", cell: [[4, 0, 0], [0, 4, 0], [0, 0, 4]], pbc: [true, true, true],
    };
    const exported = exportStructure(structure, "poscar").trim().split("\n");
    expect(exported.slice(5, 9)).toEqual(["C H", "2 1", "Selective dynamics", "Cartesian"]);
    expect(exported.slice(9)).toEqual([
      "1.00000000 0.00000000 0.00000000 T F T",
      "3.00000000 0.00000000 0.00000000 T T T",
      "2.00000000 0.00000000 0.00000000 F T F",
    ]);
    const atoms = parseStructure(exported.join("\n"), "POSCAR").atoms;
    expect(atoms.map(atom => [atom.symbol, atom.x, atom.selective_dynamics])).toEqual([
      ["C", 1, [true, false, true]], ["C", 3, [true, true, true]], ["H", 2, [false, true, false]],
    ]);
    expect(structure.atoms.map(atom => atom.id)).toEqual([7, 8, 9]);
  });
});

describe("AtomSculptor minimum image", () => {
  it("finds the shortest displacement in the reported skew cell", () => {
    const cell = [[1, 0, 0], [0.9, 0.1, 0], [0, 0, 1]];
    const delta = minimumImageDisplacement(origin, { x: 0.931, y: 0.049, z: 0 }, { cell, pbc: [true, true, false] });
    expect(delta.x).toBeCloseTo(0.031, 12);
    expect(delta.y).toBeCloseTo(-0.051, 12);
    expect(Math.hypot(delta.x, delta.y, delta.z)).toBeCloseTo(0.0596824932455, 12);
  });

  it("handles images beyond a fixed 27-neighbor search", () => {
    expect(minimumImageDistance(origin, { x: 0.49, y: 0.049, z: 0 }, {
      cell: [[1, 0, 0], [100, 0.1, 0], [0, 0, 1]], pbc: [true, true, true],
    })).toBeCloseTo(Math.hypot(0.49, 0.049), 12);
  });

  it("uses a scale-relative search bound for very small cells", () => {
    const distance = minimumImageDistance(origin, { x: 2.9e-20, y: 0, z: 0 }, {
      cell: [[1e-20, 0, 0], [0, 1e-20, 0], [0, 0, 1e-20]], pbc: [true, true, true],
    });
    expect(distance / 1e-20).toBeCloseTo(0.1, 12);
  });

  it("wraps only periodic vectors, even with a singular nonperiodic cell", () => {
    expect(minimumImageDisplacement(origin, { x: 2.9, y: 4, z: 5 }, {
      cell: [[1, 0, 0], [0, 0, 0], [0, 0, 0]], pbc: [true, false, false],
    })).toEqual({ x: 2.9 - 3, y: 4, z: 5 });
    expect(minimumImageDisplacement(origin, { x: 0.931, y: 0.049, z: 2 }, {
      cell: [[1, 0, 0], [0.9, 0.1, 0], [0, 0, 1]], pbc: [true, false, false],
    })).toEqual({ x: 0.931 - 1, y: 0.049, z: 2 });
  });

  it("matches independent exhaustive images for every PBC combination", () => {
    const cell = [[1, 0.2, 0.1], [0.9, 0.4, 0.2], [0.3, 0.2, 1]];
    for (let mask = 0; mask < 8; mask += 1) {
      const pbc = [Boolean(mask & 1), Boolean(mask & 2), Boolean(mask & 4)] as [boolean, boolean, boolean];
      for (let sample = 0; sample < 12; sample += 1) {
        const delta = [Math.sin(sample * 1.3), Math.cos(sample * 2.1), Math.sin(sample * 0.7)];
        let shortest = Infinity;
        for (let i = pbc[0] ? -6 : 0; i <= (pbc[0] ? 6 : 0); i += 1)
          for (let j = pbc[1] ? -6 : 0; j <= (pbc[1] ? 6 : 0); j += 1)
            for (let k = pbc[2] ? -6 : 0; k <= (pbc[2] ? 6 : 0); k += 1) {
              shortest = Math.min(shortest, Math.hypot(...delta.map((value, axis) => value - i * cell[0][axis] - j * cell[1][axis] - k * cell[2][axis])));
            }
        const point = { x: delta[0], y: delta[1], z: delta[2] };
        expect(minimumImageDistance(origin, point, { cell, pbc })).toBeCloseTo(shortest, 10);
        const shifted = delta.map((value, axis) => value + (pbc[0] ? 31 * cell[0][axis] : 0) - (pbc[1] ? 47 * cell[1][axis] : 0));
        expect(minimumImageDistance(origin, { x: shifted[0], y: shifted[1], z: shifted[2] }, { cell, pbc })).toBeCloseTo(shortest, 10);
      }
    }
  });
});
