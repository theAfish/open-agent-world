import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { profileStorage } from './profileStorage';
import type { WorldCard } from '../types/world';

/** Local viewing state; session contents and workspace geometry remain in the world. */
export const useConversationView = create<{
  activeConversationId?: string;
  sessions: Record<string, string | undefined>;
  activate: (conversationId: string) => void;
  selectSession: (conversationId: string, sessionId: string | undefined) => void;
  showSession: (conversationId: string, sessionId: string) => void;
}>()(persist((set) => ({
  sessions: {},
  activate: (activeConversationId) => set({ activeConversationId }),
  selectSession: (conversationId, sessionId) => set(state => ({
    sessions: { ...state.sessions, [conversationId]: sessionId },
  })),
  showSession: (conversationId, sessionId) => set(state => ({
    activeConversationId: conversationId, sessions: { ...state.sessions, [conversationId]: sessionId },
  })),
}), {
  name: 'oaw-conversation-view-v1',
  storage: createJSONStorage(() => profileStorage),
}));

/** Hide the entire ownership tree, including equipment and nested containers. */
export function sessionVisibleCards(cards: WorldCard[], conversationId?: string, sessionId?: string): WorldCard[] {
  const byId = new Map(cards.map(card => [card.id, card]));
  const visible = new Map<string, boolean>();
  const visit = (card: WorldCard): boolean => {
    if (visible.has(card.id)) return visible.get(card.id)!;
    if (card.type === 'core.virtual-workspace' && card.config.session_id) {
      const shown = card.config.conversation_id === conversationId && card.config.session_id === sessionId;
      visible.set(card.id, shown);
      return shown;
    }
    const parent = byId.get(card.equipment?.owner_id ?? card.parent_id ?? '');
    const shown = !parent || visit(parent);
    visible.set(card.id, shown);
    return shown;
  };
  return cards.filter(visit);
}
