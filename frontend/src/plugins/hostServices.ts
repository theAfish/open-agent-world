import { useMemo } from 'react';
import { useWorldStore } from '../state/worldStore';
import { availableModels, type ModelConnection } from '../state/modelConnections';
import { getNodeType } from '../state/catalog';
import { useLibrarySettings } from '../state/librarySettings';

/** Public presentation metadata only. Connection credentials are never exposed. */
export function useModelOptions(adapters?: readonly ModelConnection['adapter'][]) {
  const catalog = useWorldStore(state => state.modelCatalog);
  const legacy = useWorldStore(state => state.modelSettings.models);
  const key = adapters?.join(',');
  return useMemo(() => catalog.revision > 0
    ? availableModels({ ...catalog, connections: catalog.connections.filter(connection => !key || key.split(',').includes(connection.adapter)) })
    : legacy.map(value => ({ value, label: value })), [catalog, legacy, key]);
}

/** Canvas presentation relationships; this does not grant execution permissions. */
export function useConnectedCards(cardId: string, options: {
  direction?: 'incoming' | 'outgoing' | 'both'; relationshipPrefix?: string; trait?: string;
} = {}) {
  const cards = useWorldStore(state => state.cards);
  const edges = useWorldStore(state => state.edges);
  const catalog = useWorldStore(state => state.catalog);
  const { direction = 'both', relationshipPrefix, trait } = options;
  return useMemo(() => {
    const ids = new Set(edges.flatMap(edge => {
      if (relationshipPrefix && !edge.relationship.startsWith(relationshipPrefix)) return [];
      if (direction !== 'outgoing' && edge.target === cardId) return [edge.source];
      if (direction !== 'incoming' && edge.source === cardId) return [edge.target];
      return [];
    }));
    return cards.filter(card => ids.has(card.id) && (!trait || getNodeType(catalog, card.type)?.traits.includes(trait)));
  }, [cards, edges, catalog, cardId, direction, relationshipPrefix, trait]);
}

/** Uses ordinary host mutation/authorization and preserves other config fields. */
export function updateCardConfig(cardId: string, config: Record<string, unknown>) {
  return useWorldStore.getState().updateCard(cardId, { config });
}

export function useReadingPreferences() { return useLibrarySettings(); }
export function setReadingPreferences(patch: Partial<{ provider: string; model: string; target: string }>) {
  useLibrarySettings.setState(patch);
}
