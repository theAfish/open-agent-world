import type { CardType, PluginCatalog } from "../types/world";

// Read only during the one-time server-authorized pre-Pack migration.
type DeckIconKey = "bot" | "boxes" | "workflow" | "folder" | "layers" | "sparkles" | "star" | "zap";

interface StoredDeck {
  id: string;
  label: string;
  icon: DeckIconKey;
  cardTypes: CardType[];
  /** Catalog revision recorded when each card was last assigned to this deck. */
  catalogVersions?: Record<string, number>;
  custom?: boolean;
}

const DECKS_KEY = "open-agent-world.decks.v2";
const LEGACY_CUSTOM_DECKS_KEY = "open-agent-world.custom-decks.v1";
const isCardType = (value: unknown): value is CardType => (
  typeof value === "string" && value.length > 0
);

const isDeckIcon = (value: unknown): value is DeckIconKey => (
  value === "bot" || value === "boxes" || value === "workflow" || value === "folder"
  || value === "layers" || value === "sparkles" || value === "star" || value === "zap"
);

function deckRevision(catalog: PluginCatalog, type: CardType): number {
  return catalog.node_types.find((definition) => definition.id === type)?.deck_revision ?? 1;
}

export function defaultDecks(catalog: PluginCatalog): StoredDeck[] {
  const decks = new Map<string, StoredDeck>();
  catalog.node_types.filter((definition) => definition.user_creatable !== false).forEach((definition) => {
    const current = decks.get(definition.deck_id);
    if (current) {
      current.cardTypes.push(definition.id);
      current.catalogVersions![definition.id] = definition.deck_revision ?? 1;
    }
    else decks.set(definition.deck_id, {
      id: definition.deck_id,
      label: definition.deck_label,
      icon: isDeckIcon(definition.deck_icon) ? definition.deck_icon : "folder",
      cardTypes: [definition.id],
      catalogVersions: { [definition.id]: definition.deck_revision ?? 1 },
      custom: false,
    });
  });
  return [...decks.values()];
}

export function normalizeDecks(candidates: StoredDeck[], catalog: PluginCatalog): StoredDeck[] {
  const defaults = defaultDecks(catalog);
  const merged = [
    ...defaults.filter((deck) => !candidates.some((candidate) => candidate.id === deck.id)),
    ...candidates,
  ];
  const creatableTypes = catalog.node_types.filter((definition) => definition.user_creatable !== false);
  const validTypes = new Set(creatableTypes.map((definition) => definition.id));
  const defaultHome = new Map(creatableTypes.map((definition) => [definition.id, definition.deck_id]));
  const decks = merged.map((deck) => ({ ...deck, cardTypes: [] as CardType[], catalogVersions: {} as Record<string, number> }));
  const claimed = new Set<CardType>();
  merged.forEach((candidate, index) => {
    candidate.cardTypes.forEach((type) => {
      const revision = deckRevision(catalog, type);
      const recordedRevision = candidate.catalogVersions?.[type] ?? 1;
      if (validTypes.has(type) && !claimed.has(type) && revision <= recordedRevision) {
        decks[index].cardTypes.push(type);
        decks[index].catalogVersions[type] = revision;
        claimed.add(type);
      }
    });
  });
  validTypes.forEach((type) => {
    if (claimed.has(type)) return;
    const home = decks.find((deck) => deck.id === defaultHome.get(type)) ?? decks[0];
    home?.cardTypes.push(type);
    if (home) home.catalogVersions[type] = deckRevision(catalog, type);
  });
  // Retain custom folders, but remove retired catalog decks once their cards move.
  return decks.filter((deck) => deck.custom || deck.cardTypes.length > 0 || defaults.some((item) => item.id === deck.id));
}

function parseStoredDecks(value: string | null): StoredDeck[] {
  if (!value) return [];
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((deck): StoredDeck[] => {
      if (!deck || typeof deck !== "object") return [];
      const candidate = deck as Partial<StoredDeck>;
      const cardTypes = Array.isArray(candidate.cardTypes) ? candidate.cardTypes.filter(isCardType) : [];
      const catalogVersions = candidate.catalogVersions && typeof candidate.catalogVersions === "object"
        ? Object.fromEntries(Object.entries(candidate.catalogVersions).flatMap(([type, revision]) => (
          isCardType(type) && typeof revision === "number" && Number.isFinite(revision) ? [[type, revision]] : []
        ))) : undefined;
      if (typeof candidate.id !== "string" || typeof candidate.label !== "string") return [];
      return [{
        id: candidate.id,
        label: candidate.label,
        icon: isDeckIcon(candidate.icon) ? candidate.icon : "folder",
        cardTypes,
        catalogVersions,
        custom: candidate.custom ?? candidate.id.startsWith("custom-"),
      }];
    });
  } catch {
    return [];
  }
}

export function loadLegacyDecks(catalog: PluginCatalog): StoredDeck[] {
  const defaults = defaultDecks(catalog);
  if (typeof window === "undefined") return defaults;
  const stored = parseStoredDecks(window.localStorage.getItem(DECKS_KEY));
  if (stored.length > 0) {
    return normalizeDecks(stored, catalog);
  }

  const legacy = parseStoredDecks(window.localStorage.getItem(LEGACY_CUSTOM_DECKS_KEY));
  const migrated = defaults.map((deck) => ({ ...deck, cardTypes: [...deck.cardTypes] }));
  legacy.forEach((legacyDeck) => {
    legacyDeck.cardTypes.forEach((type) => {
      migrated.forEach((deck) => {
        deck.cardTypes = deck.cardTypes.filter((candidate) => candidate !== type);
      });
    });
    migrated.push({ ...legacyDeck, icon: legacyDeck.icon ?? "folder", custom: true });
  });
  return normalizeDecks(migrated, catalog);
}

