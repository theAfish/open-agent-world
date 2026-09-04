import { getNodeType, getRelationshipDefinition } from "./catalog";
import type { PluginCatalog, WorldCard, WorldEdge } from "../types/world";

export interface LegionSelectionSummary {
  cards: WorldCard[];
  internalEdges: WorldEdge[];
  externalEdges: WorldEdge[];
  unsupportedCards: WorldCard[];
  unsupportedEdges: WorldEdge[];
}

export function summarizeLegionSelection(
  cards: readonly WorldCard[],
  edges: readonly WorldEdge[],
  selectedIds: readonly string[],
  catalog: PluginCatalog,
): LegionSelectionSummary {
  const selected = new Set(selectedIds);
  const selectedCards = cards.filter((card) => selected.has(card.id));
  const internalEdges = edges.filter((edge) => selected.has(edge.source) && selected.has(edge.target));
  const externalEdges = edges.filter((edge) => selected.has(edge.source) !== selected.has(edge.target));
  const unsupportedCards = selectedCards.filter((card) => {
    const definition = getNodeType(catalog, card.type);
    return card.ephemeral || !definition || definition.templateable !== true;
  });
  const unsupportedEdges = internalEdges.filter((edge) => (
    getRelationshipDefinition(catalog, edge.relationship)?.templateable !== true
  ));
  return { cards: selectedCards, internalEdges, externalEdges, unsupportedCards, unsupportedEdges };
}
