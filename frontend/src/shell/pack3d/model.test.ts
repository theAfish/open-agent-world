import { expect, it, vi } from 'vitest';
import { Mesh, MeshBasicMaterial, NoBlending, Raycaster, Texture, Vector3 } from 'three';
import { createPackModel } from './model';
import { createMicrostructure } from './materials';
import type { PackRenderOptions } from './types';

vi.mock('./print', () => ({
  createPrint: () => ({ front: new Texture(), back: new Texture(), spine: new Texture(), flap: new Texture(), roughness: new Texture(), bump: new Texture(), dispose: vi.fn() }),
}));

it.each(['standard', 'premium', 'paper', 'collector'] as const)('defers %s card contents until they are revealed and releases them with the wrapper', packaging => {
  const options: PackRenderOptions = { id: 'test', name: 'Test', description: '', edition: '', packaging, color: '#78967b', count: 1, countLabel: 'CARDS',
    opened: false, revealing: false, finishVisible: false, reducedMotion: false, view: {}, cards: [{ id: 'card' }] };
  const micro = createMicrostructure(), model = createPackModel(options, micro, () => {});
  model.pose(0, false);
  const lining = model.root.getObjectByName('foil-lining');
  if (lining) expect(lining.visible).toBe(false);
  model.pose(1, false);
  expect(model.cards).toHaveLength(0);
  expect(model.root.getObjectByName('card:card')).toBeUndefined();
  model.pose(.5, true);
  if (lining) expect(lining.visible).toBe(true);
  const card = model.root.getObjectByName('card:card') as Mesh;
  expect(card).toBeDefined();
  expect(model.cards).toHaveLength(1);
  const face = (card.material as MeshBasicMaterial[])[4];
  expect(face.name).toBe('card-face-mask');
  expect(face.map).toBeNull();
  expect(face.opacity).toBe(0);
  expect(face.color.getHex()).toBe(0);
  expect(face.premultipliedAlpha).toBe(true);
  expect(face.blending).toBe(NoBlending);
  expect(face.depthWrite).toBe(true);
  model.pose(.6, true);
  expect(model.root.getObjectByName('card:card')).toBe(card);
  const clay = new MeshBasicMaterial();
  model.surface(clay);
  expect(card.material).toBe(clay);
  model.surface();
  expect(card.material).not.toBe(clay);
  model.pose(1, false);
  expect(model.cards[0].opacity).toBe(0);
  const geometryDisposed = vi.spyOn(card.geometry, 'dispose');
  const faceDisposed = vi.spyOn(face, 'dispose');
  model.dispose(); micro.dispose(); clay.dispose();
  expect(geometryDisposed).toHaveBeenCalledTimes(1);
  expect(faceDisposed).toHaveBeenCalledTimes(1);
});

it('closes the paper mouth across its full depth and clears it again when the flap opens', () => {
  const options: PackRenderOptions = { id: 'paper', name: 'Paper', description: '', edition: '', packaging: 'paper', color: '#78967b', count: 0, countLabel: 'CARDS',
    opened: false, revealing: false, finishVisible: false, reducedMotion: false, view: {}, cards: [] };
  const micro = createMicrostructure(), model = createPackModel(options, micro, () => {});
  try {
    for (const progress of [0, .3, .6, 1, 0]) {
      model.pose(progress, false); model.root.updateMatrixWorld(true);
      for (const x of [-1.08, -.64, 0, .64, 1.08]) for (const z of [-.06, 0, .06]) {
        const hits = new Raycaster(new Vector3(x, 3, z), new Vector3(0, -1, 0)).intersectObject(model.root);
        expect(hits.length).toBeGreaterThan(0);
        if (progress === 0) {
          expect(hits[0].point.y).toBeGreaterThan(1.59);
          expect(hits[0].point.y).toBeLessThan(1.64);
        } else expect(hits[0].point.y).toBeLessThan(-1.57);
      }
    }
  } finally { model.dispose(); micro.dispose(); }
});
