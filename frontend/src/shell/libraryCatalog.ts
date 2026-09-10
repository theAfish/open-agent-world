import type { DeckEntry, LibrarySnapshot } from "../state/cardLibrary";
import type { NodeTypeCatalogItem } from "../types/world";

export interface LibrarySource { id: string; name: string; pluginName: string }
export interface LibraryCard extends DeckEntry {
  label: string;
  description: string;
  category: string;
  available: boolean;
  internal: boolean;
  definition?: NodeTypeCatalogItem;
  sources: LibrarySource[];
  owners: NodeTypeCatalogItem[];
}

export const formationSource: LibrarySource = { id: "saved-legions", name: "Saved Legions", pluginName: "Your formations" };

export function libraryCardMetadata(snapshot: LibrarySnapshot, definition: NodeTypeCatalogItem) {
  const pluginName = snapshot.plugins[definition.plugin_id]?.descriptor.name || definition.plugin_id;
  // Only an explicit member type identifies an owner; matching broad traits does not.
  const owners = Object.values(snapshot.card_definitions).filter(card => card.container?.member_type === definition.id);
  const duplicateLabel = Object.values(snapshot.card_definitions).some(card => card.id !== definition.id && card.label === definition.label);
  const context = owners.length === 1 ? owners[0].label : duplicateLabel ? pluginName : "";
  return { owners, label: context ? `${context} · ${definition.label}` : definition.label };
}

export function collectedLibraryCards(snapshot: LibrarySnapshot): LibraryCard[] {
  return Object.values(snapshot.collection).filter(entry => entry.unlocked).flatMap(entry => {
    const definition = snapshot.card_definitions[entry.card_id];
    if (!definition) return [];
    const pluginName = snapshot.plugins[definition.plugin_id]?.descriptor.name || definition.plugin_id;
    const sources = [...new Set(entry.source_pack_ids)].map(id => ({
      id: `pack:${id}`, name: snapshot.packs[id]?.definition.name ?? id,
      pluginName: snapshot.plugins[snapshot.packs[id]?.definition.plugin_id]?.descriptor.name || pluginName,
    }));
    if (!sources.length) sources.push({ id: `plugin:${definition.plugin_id}`, name: pluginName, pluginName: "Other collected cards" });
    return [{ kind: "node", id: entry.card_id, ...libraryCardMetadata(snapshot, definition),
      description: definition.description, category: definition.deck_label,
      available: snapshot.available_card_ids.includes(entry.card_id), internal: definition.user_creatable === false,
      definition, sources }];
  });
}

export function libraryCardMatches(card: LibraryCard, query: string) {
  return [card.label, card.definition?.label, card.description, card.category,
    ...card.sources.flatMap(source => [source.name, source.pluginName]), ...card.owners.map(owner => owner.label)]
    .join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
}
