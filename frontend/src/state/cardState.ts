import { useWorldStore } from './worldStore';
import { useConversationView } from './conversationView';
import type { WorldCard, NodeTypeCatalogItem } from '../types/world';

type ConversationView = Pick<ReturnType<typeof useConversationView.getState>, 'sessions' | 'activeConversationId'>;

/** Host-only resolution. Plugin props never carry session IDs or storage keys. */
export function resolveCardStateSession(id: string, cards: WorldCard[], types: NodeTypeCatalogItem[], view: ConversationView): string | undefined {
  const card = cards.find(item => item.id === id);
  const policy = types.find(item => item.id === card?.type)?.state;
  if (!card || policy?.mode !== 'scoped' || (card.state_scope ?? policy.defaultScope) !== 'session') return undefined;
  const byId = new Map(cards.map(item => [item.id, item]));
  const parents = (node: WorldCard): string[] => {
    const result: string[] = [];
    let owner = node.equipment?.owner_id ?? node.parent_id;
    while (owner && !result.includes(owner)) {
      result.push(owner);
      const parent = byId.get(owner);
      owner = parent?.equipment?.owner_id ?? parent?.parent_id;
    }
    return result;
  };
  for (const parentId of parents(card)) {
    const parent = byId.get(parentId);
    if (typeof parent?.config.session_id === 'string') return parent.config.session_id;
    const conversations = cards.filter(item => types.find(type => type.id === item.type)?.traits.includes('core.conversation') && parents(item).includes(parentId));
    const selected = conversations.find(item => item.id === view.activeConversationId) ?? (conversations.length === 1 ? conversations[0] : undefined);
    if (selected) return view.sessions[selected.id];
  }
  return byId.has(view.activeConversationId ?? '') ? view.sessions[view.activeConversationId ?? ''] : undefined;
}

export function cardStateSession(id: string): string | undefined {
  const world = useWorldStore.getState();
  return resolveCardStateSession(id, world.cards, world.catalog.node_types, useConversationView.getState());
}

export function useCardStateSession(id: string): string | undefined {
  const cards = useWorldStore(state => state.cards);
  const types = useWorldStore(state => state.catalog.node_types);
  const view = useConversationView();
  return resolveCardStateSession(id, cards, types, view);
}

export function stateSessionHeaders(sessionId: string | null | undefined): Record<string, string> {
  return sessionId ? { 'X-OAW-State-Session': sessionId } : {};
}
