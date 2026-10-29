import { expect, it, vi } from 'vitest';
import { Mesh, MeshBasicMaterial, NoBlending, Texture } from 'three';
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
