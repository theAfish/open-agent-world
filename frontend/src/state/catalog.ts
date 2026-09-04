import type { CardType, NodeTypeCatalogItem, PluginCatalog, Relationship } from "../types/world";

export const EMPTY_CATALOG: PluginCatalog = {
  plugins: [],
  node_types: [],
  relationships: [],
};

export function getNodeType(
  catalog: PluginCatalog,
  type: CardType,
): NodeTypeCatalogItem | undefined {
  return catalog.node_types.find((definition) => definition.id === type);
}

export function getNodeTypeLabel(catalog: PluginCatalog, type: CardType): string {
  return getNodeType(catalog, type)?.label ?? type;
}

export function getRelationshipDefinition(
  catalog: PluginCatalog,
  relationship: Relationship,
) {
  return catalog.relationships.find((definition) => definition.id === relationship);
}
