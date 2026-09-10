import type { PluginCatalog, WorldCard } from "../types/world";

export function transformationOptions(catalog: PluginCatalog, source: WorldCard, target: WorldCard) {
  if (source.id === target.id) return [];
  const traits = catalog.node_types.find(item => item.id === source.type)?.traits ?? [];
  const operations = catalog.node_types.find(item => item.id === target.type)?.transformations ?? {};
  return Object.entries(operations).filter(([, operation]) => operation.source_traits.every(trait => traits.includes(trait)));
}
