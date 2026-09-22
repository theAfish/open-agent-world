import { describe, expect, it } from 'vitest';
import type { WorldCard } from '../types/world';
import { sessionVisibleCards } from './conversationView';

const node = (id: string, extra: Partial<WorldCard> = {}): WorldCard => ({
  id, type: 'agent', name: id, config: {}, position: { x: 0, y: 0 }, size: { width: 96, height: 96 },
  expanded: false, status: 'idle', ...extra,
});

describe('session canvas projection', () => {
  it('switches whole workspace ownership trees while preserving ordinary cards and source data', () => {
    const cards = [node('ordinary'), node('a', { type: 'core.virtual-workspace', config: { conversation_id: 'room', session_id: 'a' } }),
      node('b', { type: 'core.virtual-workspace', config: { conversation_id: 'room', session_id: 'b' } }),
      node('other-room', { type: 'core.virtual-workspace', config: { conversation_id: 'other', session_id: 'a' } }),
      node('worker', { parent_id: 'a' }), node('equipment', { equipment: { owner_id: 'worker', relationship: null } }),
      node('nested', { parent_id: 'equipment' }), node('standalone', { type: 'core.virtual-workspace' })];
    const snapshot = structuredClone(cards);
    const ids = (room?: string, session?: string) => sessionVisibleCards(cards, room, session).map(card => card.id);
    expect(ids('room', 'a')).toEqual(['ordinary', 'a', 'worker', 'equipment', 'nested', 'standalone']);
    expect(ids('room', 'b')).toEqual(['ordinary', 'b', 'standalone']);
    expect(ids('other', 'a')).toEqual(['ordinary', 'other-room', 'standalone']);
    expect(ids()).toEqual(['ordinary', 'standalone']);
    expect(cards).toEqual(snapshot);
  });
});
