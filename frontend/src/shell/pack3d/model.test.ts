import { expect, it, vi } from 'vitest';
import { Mesh, MeshBasicMaterial, Texture } from 'three';
import { createPackModel } from './model';
import { createMicrostructure } from './materials';
import { cardPrint } from './print';
import type { PackRenderOptions } from './types';

vi.mock('./print', () => ({
  createPrint: () => ({ front: new Texture(), back: new Texture(), spine: new Texture(), flap: new Texture(), roughness: new Texture(), bump: new Texture(), dispose: vi.fn() }),
  cardPrint: vi.fn(() => ({ map: new Texture(), dispose: vi.fn() })),
}));

it.each(['standard', 'premium', 'paper', 'collector'] as const)('defers %s card contents until they are revealed and releases them with the wrapper', packaging => {
  vi.mocked(cardPrint).mockClear();
  const options: PackRenderOptions = { id: 'test', name: 'Test', description: '', edition: '', packaging, color: '#78967b', count: 1, countLabel: 'CARDS',
    opened: false, revealing: false, finishVisible: false, reducedMotion: false, view: {}, cards: [{ id: 'card', label: 'Test', finish: 'foil' }] };
  const micro = createMicrostructure(), model = createPackModel(options, micro, () => {});
  model.pose(0, false, false);
  const lining = model.root.getObjectByName('foil-lining');
  if (lining) expect(lining.visible).toBe(false);
  model.pose(1, false, false);
  expect(cardPrint).not.toHaveBeenCalled();
  expect(model.root.getObjectByName('card:card')).toBeUndefined();
  model.pose(.5, true, true);
  if (lining) expect(lining.visible).toBe(true);
  const card = model.root.getObjectByName('card:card') as Mesh;
  expect(card).toBeDefined();
  expect(cardPrint).toHaveBeenCalledTimes(1);
  model.pose(.6, true, true);
  expect(model.root.getObjectByName('card:card')).toBe(card);
  const clay = new MeshBasicMaterial();
  model.surface(clay);
  expect(card.material).toBe(clay);
  model.surface();
  expect(card.material).not.toBe(clay);
  model.dispose(); micro.dispose(); clay.dispose();
  expect(vi.mocked(cardPrint).mock.results[0].value.dispose).toHaveBeenCalledTimes(1);
});
