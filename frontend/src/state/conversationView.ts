import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { profileStorage } from './profileStorage';
import type { WorldCard } from '../types/world';
import { useOpenFiles } from './openFiles';

/** Local viewing state; session contents and workspace geometry remain in the world. */
export const useConversationView = create<{
  activeConversationId?: string;
  sessions: Record<string, string | undefined>;
  groupSessions: Record<string, Record<string, string>>;
  rememberGroupSession: (conversationId: string, groupId: string, sessionId: string) => void;
  activate: (conversationId: string) => void;
  selectSession: (conversationId: string, sessionId: string | undefined) => void;
  showSession: (conversationId: string, sessionId: string) => void;
}>()(persist((set) => ({
  sessions: {},
  groupSessions: {},
  rememberGroupSession: (conversationId, groupId, sessionId) => set(state => {
    if (state.groupSessions[conversationId]?.[groupId] === sessionId) return state;
    return { groupSessions: { ...state.groupSessions, [conversationId]: { ...state.groupSessions[conversationId], [groupId]: sessionId } } };
  }),
  activate: (activeConversationId) => set({ activeConversationId }),
  selectSession: (conversationId, sessionId) => set(state => {
    if (state.sessions[conversationId] !== sessionId) useOpenFiles.getState().clear(conversationId);
    return { sessions: { ...state.sessions, [conversationId]: sessionId } };
  }),
  showSession: (conversationId, sessionId) => set(state => {
    if (state.sessions[conversationId] !== sessionId) useOpenFiles.getState().clear(conversationId);
    return { activeConversationId: conversationId, sessions: { ...state.sessions, [conversationId]: sessionId } };
  }),
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
