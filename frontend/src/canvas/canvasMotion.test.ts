import { expect, it } from 'vitest';
import { buildCardDraft } from '../state/helpers';
import { cardMotionChanges } from './canvasMotion';

const a = { id: 'a', ...buildCardDraft('text', { x: 0, y: 0 }) };
const b = { ...a, id: 'b' };
const baseline = { cards: [a], loadedChunkKeys: ['0,0'], syncState: 'online' };

it('animates real card membership changes, including undo, but not metadata edits', () => {
  const added = { ...baseline, cards: [a, b] };
  expect(cardMotionChanges(added, baseline)).toEqual({ added: ['b'], removed: [] });
  expect(cardMotionChanges(baseline, added)).toEqual({ added: [], removed: ['b'] });
  expect(cardMotionChanges({ ...baseline, cards: [{ ...a, name: 'Renamed' }] }, baseline)).toEqual({ added: [], removed: [] });
});

it('does not animate initial loading, refreshes or chunk replacement as card operations', () => {
  const empty = { added: [], removed: [] };
  expect(cardMotionChanges(baseline, { ...baseline, cards: [], syncState: 'loading' })).toEqual(empty);
  expect(cardMotionChanges({ ...baseline, cards: [b], loadedChunkKeys: ['1,0'] }, baseline)).toEqual(empty);
  expect(cardMotionChanges({ ...baseline, cards: [a, b], loadedChunkKeys: [...baseline.loadedChunkKeys] }, baseline)).toEqual(empty);
});
