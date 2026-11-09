import { useWorldStore } from './worldStore';
import { useConversationView } from './conversationView';
import type { WorldCard, WorldEdge, NodeTypeCatalogItem, PluginCatalog } from '../types/world';
import { getConnectionOptions, getRelationshipOptions } from './relationships';

type ConversationView = Pick<ReturnType<typeof useConversationView.getState>, 'sessions' | 'activeConversationId'>;

/** Host-only resolution. Plugin props never carry session IDs or storage keys. */
export function resolveCardStateSession(id: string, cards: WorldCard[], catalog: PluginCatalog, view: ConversationView, edges: WorldEdge[] = []): string | undefined {
  const card = cards.find(item => item.id === id);
  const policy = catalog.node_types.find(item => item.id === card?.type)?.state;
  if (!card || policy?.mode !== 'scoped' || ((card.state_scope ?? policy.defaultScope) === 'shared' && !policy.followWorkspace)) return undefined;
  return policy.followWorkspace ? resolveViewingSession(id, cards, catalog, view, edges) : resolveDocumentSession(id, cards, catalog.node_types, view);
}

function resolveDocumentSession(id: string, cards: WorldCard[], types: NodeTypeCatalogItem[], view: ConversationView): string | undefined {
  const card = cards.find(item => item.id === id);
  if (!card) return undefined;
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

/** UI context hints follow actual sources; the backend validates native grants. */
export function resolveViewingSession(id: string, cards: WorldCard[], catalog: PluginCatalog, view: ConversationView, edges: WorldEdge[] = []): string | undefined {
  const card = cards.find(item => item.id === id);
  if (!card) return undefined;
  const byId = new Map(cards.map(item => [item.id, item]));
  const hasTrait = (node: WorldCard | undefined, trait: string) => !!node && !!catalog.node_types.find(type => type.id === node.type)?.traits.includes(trait);
  const links = [...edges.filter(edge => !edge.missing_plugin), ...cards.flatMap(node => {
    const owner = node.equipment && byId.get(node.equipment.owner_id);
    if (!owner || owner.missing_plugin || node.missing_plugin) return [];
    const options = getConnectionOptions(catalog, owner.type, node.type);
    const relationship = node.equipment!.relationship ?? options[0]?.value;
    if (!relationship) return [];
    const forward = getRelationshipOptions(catalog, owner.type, node.type).some(option => option.value === relationship);
    if (!forward && !getRelationshipOptions(catalog, node.type, owner.type).some(option => option.value === relationship)) return [];
    return [{ source: forward ? owner.id : node.id, target: forward ? node.id : owner.id, relationship }];
  })];
  const visited = new Set<string>();
  let owner = card.equipment?.owner_id ?? card.parent_id;
  while (owner && !visited.has(owner)) {
    visited.add(owner);
    const parent = byId.get(owner);
    if (typeof parent?.config.session_id === 'string') return parent.config.session_id;
    owner = parent?.equipment?.owner_id ?? parent?.parent_id;
  }
  const agents = new Set<string>();
  if (hasTrait(card, 'core.agent')) agents.add(id);
  else {
    const direct = links.filter(edge => edge.source === id && edge.relationship === 'core.file-preview'
      && hasTrait(byId.get(edge.target), 'core.sandbox')).map(edge => edge.target);
    const sandboxes = new Set(hasTrait(card, 'core.sandbox') ? [id] : direct);
    if (!sandboxes.size) {
      for (const edge of links) if (edge.target === id && hasTrait(byId.get(edge.source), 'core.agent')) agents.add(edge.source);
      for (const edge of links) if (agents.has(edge.source) && hasTrait(byId.get(edge.target), 'core.sandbox')) sandboxes.add(edge.target);
    }
    for (const edge of links) if (sandboxes.has(edge.target) && hasTrait(byId.get(edge.source), 'core.agent')) agents.add(edge.source);
  }
  const conversations = new Set(links.filter(edge => agents.has(edge.source) && edge.relationship === 'participate'
    && hasTrait(byId.get(edge.target), 'core.conversation')).map(edge => edge.target));
  const selected = conversations.has(view.activeConversationId ?? '') ? view.activeConversationId
    : conversations.size === 1 ? [...conversations][0] : undefined;
  return selected ? view.sessions[selected] : undefined;
}

export function viewingSession(id: string): string | undefined {
  const world = useWorldStore.getState();
  return resolveViewingSession(id, world.cards, world.catalog, useConversationView.getState(), world.edges);
}

export function useViewingSession(id: string, enabled = true): string | undefined {
  useConversationView(view => {
    if (!enabled) return undefined;
    const world = useWorldStore.getState();
    return resolveViewingSession(id, world.cards, world.catalog, view, world.edges);
  });
  return useWorldStore(world => enabled ? resolveViewingSession(id, world.cards, world.catalog, useConversationView.getState(), world.edges) : undefined);
}

export function cardStateSession(id: string): string | undefined {
  const world = useWorldStore.getState();
  return resolveCardStateSession(id, world.cards, world.catalog, useConversationView.getState(), world.edges);
}

export function useCardStateSession(id: string): string | undefined {
  // Subscribe to the resolved scope, not every card or unrelated conversation edit.
  useConversationView(view => {
    const world = useWorldStore.getState();
    return resolveCardStateSession(id, world.cards, world.catalog, view, world.edges);
  });
  return useWorldStore(world => resolveCardStateSession(id, world.cards, world.catalog, useConversationView.getState(), world.edges));
}

export function stateSessionHeaders(sessionId: string | null | undefined): Record<string, string> {
  return sessionId ? { 'X-OAW-State-Session': sessionId } : {};
}
