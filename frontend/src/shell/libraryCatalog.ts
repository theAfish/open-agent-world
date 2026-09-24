import { t } from "../i18n";
import type { DeckEntry, LibrarySnapshot } from "../state/cardLibrary";
import type { LegionSummary, NodeTypeCatalogItem } from "../types/world";

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

export function displayDeckName(deck: { id: string; name: string }): string {
  if (deck.id === 'starter' && deck.name === 'My deck') return t('My deck');
  if (deck.id === 'saved-legions' && deck.name === 'Legions') return t('Legions');
  return deck.name;
}

export function libraryCardMetadata(snapshot: LibrarySnapshot, definition: NodeTypeCatalogItem) {
  const pluginName = snapshot.plugins[definition.plugin_id]?.descriptor.name || definition.plugin_id;
  // Only an explicit member type identifies an owner; matching broad traits does not.
  const owners = Object.values(snapshot.card_definitions).filter(card => card.container?.member_type === definition.id);
  const duplicateLabel = Object.values(snapshot.card_definitions).some(card => card.id !== definition.id && card.label === definition.label);
  const context = owners.length === 1 ? owners[0].label : duplicateLabel ? pluginName : "";
  return { owners, label: context ? `${t(context)} · ${t(definition.label)}` : t(definition.label) };
}

export function collectedLibraryCards(snapshot: LibrarySnapshot): LibraryCard[] {
  return Object.values(snapshot.collection).filter(entry => entry.unlocked).flatMap(entry => {
    const definition = snapshot.card_definitions[entry.card_id];
    if (!definition) return [];
    const pluginName = snapshot.plugins[definition.plugin_id]?.descriptor.name || definition.plugin_id;
    const sources = [...new Set(entry.source_pack_ids)].map(id => ({
      id: `pack:${id}`, name: t(snapshot.packs[id]?.definition.name ?? id),
      pluginName: snapshot.plugins[snapshot.packs[id]?.definition.plugin_id]?.descriptor.name || pluginName,
    }));
    if (!sources.length) sources.push({ id: `plugin:${definition.plugin_id}`, name: pluginName, pluginName: "Other collected cards" });
    return [{ kind: "node", id: entry.card_id, ...libraryCardMetadata(snapshot, definition),
      description: t(definition.description), category: definition.deck_label,
      available: snapshot.available_card_ids.includes(entry.card_id), internal: definition.user_creatable === false,
      definition, sources }];
  });
}

export function collectedLibraryLegions(snapshot: LibrarySnapshot, legions: LegionSummary[]): LibraryCard[] {
  return legions.flatMap(item => {
    const packIds = [...new Set(snapshot.preset_pack_ids?.[item.id] ?? [])]
      .filter(id => snapshot.packs[id]?.owned && snapshot.packs[id].opened);
    const sources: LibrarySource[] = item.preset ? packIds.map(id => {
      const pack = snapshot.packs[id].definition;
      return { id: `pack:${id}`, name: t(pack.name),
        pluginName: snapshot.plugins[pack.plugin_id]?.descriptor.name || pack.plugin_id };
    }) : [{ ...formationSource, name: t(formationSource.name), pluginName: t(formationSource.pluginName) }];
    // Installing a preset is not collecting it. Never fall back to a global
    // preset category, nor infer ownership from the types of its member cards.
    if (!sources.length) return [];
    return [{ kind: "legion", id: item.id, label: item.name,
      description: t("{v0} cards · {v1} links", { v0: String(item.node_count), v1: String(item.edge_count) }),
      category: item.preset ? "Legions" : "Saved Legions",
      available: item.compatible && (!item.preset || packIds.some(id => snapshot.available_pack_ids.includes(id))),
      internal: false, sources, owners: [] }];
  });
}

/** Order within a source pack, before pagination: formations, ordinary cards, internal cards. */
export function compareLibraryCards(a: LibraryCard, b: LibraryCard): number {
  return Number(a.internal) - Number(b.internal)
    || Number(b.kind === "legion") - Number(a.kind === "legion")
    || a.label.localeCompare(b.label) || a.id.localeCompare(b.id);
}

export function libraryCardMatches(card: LibraryCard, query: string) {
  return [card.label, card.definition?.label, card.description, card.category,
    ...card.sources.flatMap(source => [source.name, source.pluginName]), ...card.owners.map(owner => owner.label)]
    .join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
}
