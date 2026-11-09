import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useWorldStore } from './worldStore';
import { useLegionWorkspace } from './legionWorkspace';
import { buildCardDraft } from './helpers';
import type { WorldCard } from '../types/world';

beforeEach(() => {
  useWorldStore.setState({ cards: [], edges: [], toasts: [], cardTombstones: {}, edgeTombstones: {}, loadedChunkKeys: [],
    selectedCardIds: [], undoStack: [], redoStack: [], stressCards: [], syncState: 'online' });
  useLegionWorkspace.getState().close();
});
afterEach(() => { vi.restoreAllMocks(); useLegionWorkspace.getState().close(); });

it('loads an existing Legion outside the viewport before opening its workspace', async () => {
  const group = { id: 'imported', ...buildCardDraft('legion', { x: 47000, y: -4500 }) };
  const member = { id: 'history', ...buildCardDraft('conversation', { x: 47500, y: -4000 }), parent_id: group.id };
  vi.spyOn(worldApi, 'getCard').mockResolvedValue(group);
  const load = vi.spyOn(worldApi, 'getWorld').mockResolvedValue({ nodes: [group, member], edges: [], chunks: ['22:-3'] });
  expect(await useWorldStore.getState().openLegionInstance(group.id)).toBe(true);
  expect(load).toHaveBeenCalledWith(['22:-3']);
  expect(useLegionWorkspace.getState().activeId).toBe(group.id);
  expect(useWorldStore.getState().cards.map(card => card.id)).toEqual(['imported', 'history']);
});

it('reports a missing imported Legion instead of claiming that it opened', async () => {
  vi.spyOn(worldApi, 'getCard').mockRejectedValue(new Error('Legion no longer exists'));
  const load = vi.spyOn(worldApi, 'getWorld');
  expect(await useWorldStore.getState().openLegionInstance('deleted')).toBe(false);
  expect(load).not.toHaveBeenCalled();
  expect(useLegionWorkspace.getState().activeId).toBeUndefined();
  expect(useWorldStore.getState().toasts.at(-1)?.detail).toBe('Legion no longer exists');
});

it('places the existing formation at the drop point and repositions it on subsequent drops', async () => {
  const group = { id: 'imported', ...buildCardDraft('legion', { x: 47000, y: -4500 }) };
  const history = { id: 'history', ...buildCardDraft('conversation', { x: 47500, y: -4000 }), parent_id: group.id };
  const files: WorldCard = { id: 'files', ...buildCardDraft('sandbox', { x: 48000, y: -4000 }), parent_id: group.id,
    config: { workspace_path: '/original/research', workspace_scope: 'shared', workspace_access: 'read_only' } };
  let serverCards: WorldCard[] = [group, history, files];
  vi.spyOn(worldApi, 'getCard').mockImplementation(async id => serverCards.find(card => card.id === id)!);
  vi.spyOn(worldApi, 'getWorld').mockImplementation(async () => ({ nodes: serverCards, edges: [], chunks: [] }));
  const save = vi.spyOn(worldApi, 'batchUpdateNodes').mockImplementation(async updates => {
    serverCards = serverCards.map(card => {
      const update = updates.find(item => item.node_id === card.id);
      return update ? { ...card, ...update.patch } : card;
    });
    return updates.map(item => serverCards.find(card => card.id === item.node_id)!);
  });
  expect(await useWorldStore.getState().placeLegionInstance(group.id, { x: 200, y: 300 })).toBe(true);
  expect(save.mock.calls[0][0].map(item => [item.node_id, item.patch.position])).toEqual([
    ['imported', { x: 200, y: 300 }], ['history', { x: 700, y: 800 }], ['files', { x: 1200, y: 800 }],
  ]);
  expect(await useWorldStore.getState().placeLegionInstance(group.id, { x: 800, y: 900 })).toBe(true);
  expect(useWorldStore.getState().cards.map(card => card.id)).toEqual(['imported', 'history', 'files']);
  expect(useWorldStore.getState().cards.find(card => card.id === 'files')?.config).toEqual(files.config);
  expect(useWorldStore.getState().selectedCardIds).toEqual([group.id]);
  expect(useWorldStore.getState().undoStack.at(-1)?.kind).toBe('cards-updated');
  expect(useLegionWorkspace.getState().activeId).toBeUndefined();
});

it('keeps the existing formation in place when its position cannot be saved', async () => {
  const group = { id: 'imported', ...buildCardDraft('legion', { x: 47000, y: -4500 }) };
  vi.spyOn(worldApi, 'getCard').mockResolvedValue(group);
  vi.spyOn(worldApi, 'getWorld').mockResolvedValue({ nodes: [group], edges: [], chunks: [] });
  vi.spyOn(worldApi, 'batchUpdateNodes').mockRejectedValue(new Error('Position save failed'));
  expect(await useWorldStore.getState().placeLegionInstance(group.id, { x: 200, y: 300 })).toBe(false);
  expect(useWorldStore.getState().cards.find(card => card.id === group.id)?.position).toEqual(group.position);
  expect(useWorldStore.getState().toasts.at(-1)?.detail).toBe('Position save failed');
  expect(useWorldStore.getState().undoStack).toEqual([]);
});
