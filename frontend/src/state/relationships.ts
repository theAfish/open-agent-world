import type {
  CardType,
  EdgeDirection,
  PluginCatalog,
  Relationship,
  RelationshipCatalogItem,
  WorldEdge,
} from "../types/world";

export interface RelationshipOption {
  value: Relationship;
  label: string;
  shortLabel: string;
  description: string;
  directions: EdgeDirection[];
}

function option(definition: RelationshipCatalogItem): RelationshipOption {
  return {
    value: definition.id,
    label: definition.label,
    shortLabel: definition.short_label,
    description: definition.description,
    directions: definition.directions,
  };
}

function endpointMatches(
  catalog: PluginCatalog,
  type: CardType,
  types: CardType[],
  traits: string[],
): boolean {
  const node = catalog.node_types.find((definition) => definition.id === type);
  if (!node) return false;
  return (types.length === 0 || types.includes(type))
    && traits.every((trait) => node.traits.includes(trait));
}

function relationshipMatches(
  catalog: PluginCatalog,
  definition: RelationshipCatalogItem,
  sourceType: CardType,
  targetType: CardType,
): boolean {
  return endpointMatches(catalog, sourceType, definition.source_types, definition.source_traits)
    && endpointMatches(catalog, targetType, definition.target_types, definition.target_traits);
}

export function getRelationshipOptions(
  catalog: PluginCatalog,
  sourceType: CardType,
  targetType: CardType,
): RelationshipOption[] {
  return catalog.relationships
    .filter((definition) => relationshipMatches(catalog, definition, sourceType, targetType))
    .map(option);
}

export function getRelationshipOption(
  catalog: PluginCatalog,
  relationship: Relationship,
): RelationshipOption {
  const definition = catalog.relationships.find((item) => item.id === relationship);
  return definition ? option(definition) : {
    value: relationship,
    label: relationship,
    shortLabel: relationship,
    description: "Plugin relationship metadata is unavailable.",
    directions: ["forward"],
  };
}

export interface ConnectionValidation {
  valid: boolean;
  reason?: string;
  options: RelationshipOption[];
  source?: string;
  target?: string;
}

export function validateConnection(
  catalog: PluginCatalog,
  sourceId: string | null | undefined,
  targetId: string | null | undefined,
  sourceType: CardType | undefined,
  targetType: CardType | undefined,
  edges: WorldEdge[] = [],
): ConnectionValidation {
  if (!sourceId || !targetId || !sourceType || !targetType) {
    return { valid: false, reason: "Choose two world objects.", options: [] };
  }
  if (sourceId === targetId) {
    return { valid: false, reason: "An object cannot connect to itself.", options: [] };
  }

  const forwardOptions = getRelationshipOptions(catalog, sourceType, targetType);
  const reverseOptions = getRelationshipOptions(catalog, targetType, sourceType);
  const reversed = forwardOptions.length === 0 && reverseOptions.length > 0;
  const options = reversed ? reverseOptions : forwardOptions;
  if (options.length === 0) {
    return {
      valid: false,
      reason: `A ${sourceType} and ${targetType} cannot have a relationship.`,
      options: [],
    };
  }

  const canonicalSource = reversed ? targetId : sourceId;
  const canonicalTarget = reversed ? sourceId : targetId;
  if (edges.some((edge) => (
    edge.source === canonicalSource && edge.target === canonicalTarget
  ) || (
    edge.source === canonicalTarget && edge.target === canonicalSource
  ))) {
    return {
      valid: false,
      reason: "These objects already have a relationship. Select its edge to change or revoke it.",
      options,
      source: canonicalSource,
      target: canonicalTarget,
    };
  }

  return {
    valid: true,
    options,
    source: canonicalSource,
    target: canonicalTarget,
  };
}

export function isRelationshipValid(
  catalog: PluginCatalog,
  sourceType: CardType,
  targetType: CardType,
  relationship: Relationship,
): boolean {
  return getRelationshipOptions(catalog, sourceType, targetType).some(
    (option) => option.value === relationship,
  );
}
