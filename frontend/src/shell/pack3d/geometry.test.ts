import { describe, expect, it } from 'vitest';
import { Mesh, MeshBasicMaterial, Raycaster, Vector3 } from 'three';
import { boxWallGeometry, paperFlapGeometry, pouchGeometry, pouchPoint } from './geometry';

describe('physical packaging geometry', () => {
  it.each([false, true])('joins the pouch to its detachable seal and leaves only the mouth open (premium=%s)', premium => {
    const body = pouchGeometry(premium), seal = pouchGeometry(premium, 'seal');
    const p = body.getAttribute('position'), s = seal.getAttribute('position'), index = body.index!;
    const edges = new Map<string, number>();
    for (let i = 0; i < index.count; i += 3) {
      for (let j = 0; j < 3; j++) {
        const a = index.getX(i + j), b = index.getX(i + (j + 1) % 3);
        const key = [a, b].sort((a, b) => a - b).join(':'); edges.set(key, (edges.get(key) ?? 0) + 1);
      }
    }
    const boundary = [...edges].filter(([, count]) => count === 1);
    expect(boundary.length).toBeGreaterThan(0);
    expect([...edges.values()].every(count => count <= 2)).toBe(true);
    for (const [edge] of boundary) for (const i of edge.split(':').map(Number)) expect(p.getY(i)).toBeCloseTo(pouchPoint(0, .946, premium)[1], 5);
    const boundaryVertices = new Set(boundary.flatMap(([edge]) => edge.split(':').map(Number)));
    const seam = Array.from({ length: s.count }, (_, i) => i).filter(i => Math.abs(seal.getAttribute('uv').getY(i) - .946) < 1e-6)
      .map(i => [s.getX(i), s.getY(i), s.getZ(i)].join(':'));
    expect([...boundaryVertices].map(i => [p.getX(i), p.getY(i), p.getZ(i)].join(':')).sort()).toEqual(seam.sort());
    const sealEdges = new Map<string, number>();
    for (let i = 0; i < seal.index!.count; i += 3) for (let j = 0; j < 3; j++) {
      const key = [seal.index!.getX(i + j), seal.index!.getX(i + (j + 1) % 3)].sort((a, b) => a - b).join(':');
      sealEdges.set(key, (sealEdges.get(key) ?? 0) + 1);
    }
    expect([...sealEdges.values()].every(count => count === 2)).toBe(true);
    // Count the shell twice because its shared geometry also draws the lining.
    expect((body.index!.count * 2 + seal.index!.count) / 3).toBeLessThan(18000);
    expect([...p.array, ...body.getAttribute('normal').array].every(Number.isFinite)).toBe(true);
    // Mixed row densities must not collapse triangles.
    const a = new Vector3(), b = new Vector3(), c = new Vector3();
    for (let i = 0; i < index.count; i += 3) {
      a.fromBufferAttribute(p, index.getX(i)); b.fromBufferAttribute(p, index.getX(i + 1)); c.fromBufferAttribute(p, index.getX(i + 2));
      expect(b.sub(a).cross(c.sub(a)).lengthSq()).toBeGreaterThan(1e-14);
    }
    expect(pouchPoint(.5, .5, premium)[2]).toBeGreaterThan(.2);
    expect(pouchPoint(.5, .99, premium)[2]).toBeLessThan(.03);
    body.dispose(); seal.dispose();
  });

  it('assigns foil only to the narrow front region of the premium pouch', () => {
    const standard = pouchGeometry(false), premium = pouchGeometry(true), p = premium.getAttribute('position'), uv = premium.getAttribute('uv');
    expect(standard.groups.find(group => group.materialIndex === 3)?.count).toBe(0);
    const foil = premium.groups.find(group => group.materialIndex === 3)!;
    expect(foil.count).toBeGreaterThan(0);
    for (let i = foil.start; i < foil.start + foil.count; i++) {
      const vertex = premium.index!.getX(i);
      expect(p.getZ(vertex)).toBeGreaterThan(0);
      expect(uv.getX(vertex)).toBeGreaterThan(.83);
      expect(uv.getX(vertex)).toBeLessThan(.91);
    }
    standard.dispose(); premium.dispose();
  });

  it.each([false, true])('keeps the reduced surface close to the curved pouch (premium=%s)', premium => {
    const geometry = pouchGeometry(premium), p = geometry.getAttribute('position'), uv = geometry.getAttribute('uv');
    let maxError = 0;
    for (const group of geometry.groups.filter(group => group.materialIndex !== 2)) {
      for (let i = group.start; i < group.start + group.count; i += 3) {
        const ids = [0, 1, 2].map(j => geometry.index!.getX(i + j));
        const average = (values: number[]) => values.reduce((a, b) => a + b, 0) / 3;
        const back = group.materialIndex === 1;
        const u = average(ids.map(i => uv.getX(i))), v = average(ids.map(i => uv.getY(i)));
        const [x, y, z] = pouchPoint(back ? 1 - u : u, v, premium);
        maxError = Math.max(maxError, Math.hypot(average(ids.map(i => p.getX(i))) - x,
          average(ids.map(i => p.getY(i))) - y, average(ids.map(i => p.getZ(i))) - (back ? -z : z)));
      }
    }
    // Less than roughly 1.5 CSS pixels at a 350px-tall inspection size.
    expect(maxError).toBeLessThan(.015);
    geometry.dispose();
  });

  it('gives the box continuous inner and outer walls with a real empty cavity', () => {
    const geometry = boxWallGeometry(2.2, 3.18, .69, .075), material = new MeshBasicMaterial(), mesh = new Mesh(geometry, material);
    mesh.updateMatrixWorld();
    for (const direction of [new Vector3(1, 0, 0), new Vector3(-1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, -1, 0)]) {
      const inner = new Raycaster(new Vector3(), direction).intersectObject(mesh);
      expect(inner.length).toBeGreaterThan(0);
      const outside = new Raycaster(direction.clone().multiplyScalar(4), direction.clone().negate()).intersectObject(mesh);
      expect(outside.length).toBeGreaterThan(0);
      const thickness = outside[0].point.length() - inner[0].point.length();
      expect(thickness).toBeCloseTo(.075 + 2 * .006, 3); // The two bevels add to the wall thickness.
    }
    expect(new Raycaster(new Vector3(0, 0, 4), new Vector3(0, 0, -1)).intersectObject(mesh)).toHaveLength(0);
    geometry.dispose(); material.dispose();
  });

  it('gives the curved flap thickness and an opaque reverse face', () => {
    const geometry = paperFlapGeometry(), material = new MeshBasicMaterial(), mesh = new Mesh(geometry, material);
    mesh.updateMatrixWorld();
    const front = new Raycaster(new Vector3(0, -.5, 1), new Vector3(0, 0, -1)).intersectObject(mesh);
    const back = new Raycaster(new Vector3(0, -.5, -1), new Vector3(0, 0, 1)).intersectObject(mesh);
    expect(front.length).toBeGreaterThan(0); expect(back.length).toBeGreaterThan(0);
    expect(front[0].point.z - back[0].point.z).toBeGreaterThan(.01);
    geometry.dispose(); material.dispose();
  });
});
