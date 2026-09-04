import {
  AlertTriangle,
  Bot,
  Boxes,
  ChevronUp,
  FileText,
  Folder,
  Image as ImageIcon,
  Layers3,
  MessagesSquare,
  Plus,
  Puzzle,
  Sparkles,
  Star,
  Trash2,
  Workflow,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type DragEvent, type FormEvent } from "react";
import { useWorldStore } from "../state/worldStore";
import type { CardType, LegionSummary, PluginCatalog } from "../types/world";
import {
  LEGACY_NODE_DRAG_MIME,
  readPaletteDrag,
  writePaletteDrag,
  type PaletteDragPayload,
} from "./dragPayload";

interface NodeDeckCard {
  kind: "node";
  type: CardType;
  icon: LucideIcon;
  label: string;
  detail: string;
}

interface LegionDeckCard {
  kind: "legion";
  id: string;
  revision: number;
  icon: LucideIcon;
  label: string;
  detail: string;
  compatible: boolean;
  issues: string[];
}

type DeckCard = NodeDeckCard | LegionDeckCard;

type DeckIconKey = "bot" | "boxes" | "workflow" | "folder" | "layers" | "sparkles" | "star" | "zap";

interface StoredDeck {
  id: string;
  label: string;
  icon: DeckIconKey;
  cardTypes: CardType[];
  custom?: boolean;
}

interface CardDeck extends StoredDeck {
  iconComponent: LucideIcon;
  cards: DeckCard[];
  legionDeck?: boolean;
}

const DECKS_KEY = "open-agent-world.decks.v2";
const LEGACY_CUSTOM_DECKS_KEY = "open-agent-world.custom-decks.v1";
const LEGIONS_DECK_ID = "__open-agent-world-legions__";

const DECK_ICONS: Record<DeckIconKey, LucideIcon> = {
  bot: Bot,
  boxes: Boxes,
  workflow: Workflow,
  folder: Folder,
  layers: Layers3,
  sparkles: Sparkles,
  star: Star,
  zap: Zap,
};

const NODE_ICONS: Record<string, LucideIcon> = {
  bot: Bot,
  "file-text": FileText,
  image: ImageIcon,
  workflow: Workflow,
  "messages-square": MessagesSquare,
  sparkles: Sparkles,
};

const isCardType = (value: unknown): value is CardType => (
  typeof value === "string" && value.length > 0
);

const isDeckIcon = (value: unknown): value is DeckIconKey => (
  value === "bot" || value === "boxes" || value === "workflow" || value === "folder"
  || value === "layers" || value === "sparkles" || value === "star" || value === "zap"
);

function defaultDecks(catalog: PluginCatalog): StoredDeck[] {
  const decks = new Map<string, StoredDeck>();
  catalog.node_types.forEach((definition) => {
    const current = decks.get(definition.deck_id);
    if (current) current.cardTypes.push(definition.id);
    else decks.set(definition.deck_id, {
      id: definition.deck_id,
      label: definition.deck_label,
      icon: isDeckIcon(definition.deck_icon) ? definition.deck_icon : "folder",
      cardTypes: [definition.id],
      custom: false,
    });
  });
  return [...decks.values()];
}

function normalizeDecks(candidates: StoredDeck[], catalog: PluginCatalog): StoredDeck[] {
  const defaults = defaultDecks(catalog);
  const merged = [
    ...defaults.filter((deck) => !candidates.some((candidate) => candidate.id === deck.id)),
    ...candidates,
  ];
  const validTypes = new Set(catalog.node_types.map((definition) => definition.id));
  const defaultHome = new Map(catalog.node_types.map((definition) => [definition.id, definition.deck_id]));
  const decks = merged.map((deck) => ({ ...deck, cardTypes: [] as CardType[] }));
  const claimed = new Set<CardType>();
  merged.forEach((candidate, index) => {
    candidate.cardTypes.forEach((type) => {
      if (validTypes.has(type) && !claimed.has(type)) {
        decks[index].cardTypes.push(type);
        claimed.add(type);
      }
    });
  });
  validTypes.forEach((type) => {
    if (claimed.has(type)) return;
    const home = decks.find((deck) => deck.id === defaultHome.get(type)) ?? decks[0];
    home?.cardTypes.push(type);
  });
  return decks;
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
      if (typeof candidate.id !== "string" || typeof candidate.label !== "string") return [];
      return [{
        id: candidate.id,
        label: candidate.label,
        icon: isDeckIcon(candidate.icon) ? candidate.icon : "folder",
        cardTypes,
        custom: candidate.custom ?? candidate.id.startsWith("custom-"),
      }];
    });
  } catch {
    return [];
  }
}

function loadDecks(catalog: PluginCatalog): StoredDeck[] {
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

function materializeDeck(deck: StoredDeck, catalog: PluginCatalog): CardDeck {
  return {
    ...deck,
    iconComponent: DECK_ICONS[deck.icon],
    cards: deck.cardTypes.flatMap((type): DeckCard[] => {
      const definition = catalog.node_types.find((item) => item.id === type);
      return definition ? [{
        kind: "node",
        type,
        icon: NODE_ICONS[definition.icon] ?? Puzzle,
        label: definition.label,
        detail: definition.description,
      }] : [];
    }),
  };
}

function materializeLegionDeck(legions: LegionSummary[]): CardDeck {
  return {
    id: LEGIONS_DECK_ID,
    label: "Legions",
    icon: "layers",
    iconComponent: Layers3,
    cardTypes: [],
    custom: false,
    legionDeck: true,
    cards: legions.map((legion) => ({
      kind: "legion",
      id: legion.id,
      revision: legion.revision,
      icon: Layers3,
      label: legion.name,
      detail: legion.compatible
        ? `${legion.node_count} cards · ${legion.edge_count} links`
        : legion.issues[0] ?? "Required plugin unavailable",
      compatible: legion.compatible,
      issues: legion.issues,
    })),
  };
}

export function ComponentPalette() {
  const createCard = useWorldStore((state) => state.createCard);
  const instantiateLegion = useWorldStore((state) => state.instantiateLegion);
  const deleteLegion = useWorldStore((state) => state.deleteLegion);
  const legions = useWorldStore((state) => state.legions);
  const legionError = useWorldStore((state) => state.legionError);
  const catalog = useWorldStore((state) => state.catalog);
  const [storedDecks, setStoredDecks] = useState<StoredDeck[]>([]);
  const [activeDeckId, setActiveDeckId] = useState("agents");
  const [editingDeck, setEditingDeck] = useState(false);
  const [deckName, setDeckName] = useState("");
  const [deckIcon, setDeckIcon] = useState<DeckIconKey>("folder");
  const [iconMenuOpen, setIconMenuOpen] = useState(false);
  const [dragOverDeckId, setDragOverDeckId] = useState<string>();
  const decks = [
    ...storedDecks.map((deck) => materializeDeck(deck, catalog)),
    materializeLegionDeck(legions),
  ];
  const activeDeck = decks.find((deck) => deck.id === activeDeckId) ?? decks[0];

  useEffect(() => {
    if (catalog.node_types.length === 0) return;
    setStoredDecks((current) => current.length > 0
      ? normalizeDecks(current, catalog)
      : loadDecks(catalog));
  }, [catalog]);

  useEffect(() => {
    if (catalog.node_types.length === 0) return;
    window.localStorage.setItem(DECKS_KEY, JSON.stringify(storedDecks));
  }, [catalog.node_types.length, storedDecks]);

  const beginDrag = (event: DragEvent<HTMLButtonElement>, payload: PaletteDragPayload) => {
    writePaletteDrag(event.dataTransfer, payload);
  };

  const moveCardToDeck = (type: CardType, targetDeckId: string) => {
    setStoredDecks((current) => current.map((deck) => ({
      ...deck,
      cardTypes: deck.id === targetDeckId
        ? Array.from(new Set([...deck.cardTypes, type]))
        : deck.cardTypes.filter((candidate) => candidate !== type),
    })));
    setActiveDeckId(targetDeckId);
    setDragOverDeckId(undefined);
  };

  const closeEditor = () => {
    setEditingDeck(false);
    setDeckName("");
    setDeckIcon("folder");
    setIconMenuOpen(false);
  };

  const SelectedDeckIcon = DECK_ICONS[deckIcon];

  const createDeck = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const label = deckName.trim();
    if (!label) return;
    const deck: StoredDeck = {
      id: `custom-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      label,
      icon: deckIcon,
      cardTypes: [],
      custom: true,
    };
    setStoredDecks((current) => [...current, deck]);
    setActiveDeckId(deck.id);
    closeEditor();
  };

  const deleteActiveDeck = () => {
    if (!activeDeck.custom) return;
    setStoredDecks((current) => {
      const removed = current.find((deck) => deck.id === activeDeck.id);
      const restoredByDeck = new Map<string, CardType[]>();
      removed?.cardTypes.forEach((type) => {
        const home = catalog.node_types.find((definition) => definition.id === type)?.deck_id;
        if (home) restoredByDeck.set(home, [...(restoredByDeck.get(home) ?? []), type]);
      });
      return current
        .filter((deck) => deck.id !== activeDeck.id)
        .map((deck) => ({
          ...deck,
          cardTypes: Array.from(new Set([...deck.cardTypes, ...(restoredByDeck.get(deck.id) ?? [])])),
        }));
    });
    setActiveDeckId("agents");
  };

  return (
    <aside className="component-palette" aria-label="Card deck library">
      <div className="deck-tabs">
        <div className="deck-tab-scroll" role="tablist" aria-label="Card decks">
          {decks.map((deck) => {
            const DeckIcon = deck.iconComponent;
            const selected = activeDeck?.id === deck.id && !editingDeck;
            const isDropTarget = dragOverDeckId === deck.id;
            return (
              <button
                type="button"
                key={deck.id}
                className={`${selected ? "is-active" : ""} ${isDropTarget ? "is-drop-target" : ""}`}
                role="tab"
                aria-selected={selected}
                aria-controls="active-card-deck"
                onClick={() => {
                  setEditingDeck(false);
                  setActiveDeckId(deck.id);
                }}
                onDragOver={(event) => {
                  if (deck.legionDeck || !event.dataTransfer.types.includes(LEGACY_NODE_DRAG_MIME)) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  setDragOverDeckId(deck.id);
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragOverDeckId(undefined);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  const payload = readPaletteDrag(event.dataTransfer);
                  if (payload?.kind === "node" && isCardType(payload.type)) moveCardToDeck(payload.type, deck.id);
                }}
                title={deck.legionDeck
                  ? `${deck.label}: ${deck.cards.length} reusable formations.`
                  : `${deck.label}: ${deck.cards.length} cards. Drop a card here to move it.`}
              >
                <DeckIcon size={15} />
                <span>{deck.label}</span>
                <small>{deck.cards.length}</small>
              </button>
            );
          })}
        </div>
        <button
          type="button"
          className={`deck-add-button ${editingDeck ? "is-active" : ""}`}
          onClick={() => {
            setIconMenuOpen(false);
            setEditingDeck(true);
          }}
          aria-label="Create a new card deck"
          aria-expanded={editingDeck}
          title="Create a custom deck"
        >
          <Plus size={15} />
        </button>
      </div>

      <div className={`deck-stage ${editingDeck ? "is-editor" : ""}`} id="active-card-deck" role="tabpanel">
        {editingDeck ? (
          <form className="deck-editor" onSubmit={createDeck}>
            <div className="deck-editor-heading">
              <div><strong>New deck</strong><small>Name a folder, then drag cards into it.</small></div>
              <button type="button" onClick={closeEditor} aria-label="Cancel creating deck"><X size={15} /></button>
            </div>
            <label className="deck-name-field">
              <span>Deck name</span>
              <input
                autoFocus
                value={deckName}
                onChange={(event) => setDeckName(event.target.value)}
                placeholder="e.g. Research kit"
                maxLength={28}
                required
              />
            </label>
            <div className="deck-icon-picker">
              <span>Icon</span>
              <button
                type="button"
                className="deck-icon-trigger"
                onClick={() => setIconMenuOpen((open) => !open)}
                aria-haspopup="menu"
                aria-expanded={iconMenuOpen}
              >
                <SelectedDeckIcon size={14} />
                <span>{deckIcon}</span>
                <ChevronUp size={13} className={iconMenuOpen ? "" : "is-closed"} />
              </button>
              {iconMenuOpen ? <div className="deck-icon-menu" role="menu" aria-label="Choose a deck icon">
                {(Object.entries(DECK_ICONS) as Array<[DeckIconKey, LucideIcon]>).map(([key, Icon]) => (
                <button
                  type="button"
                  key={key}
                  className={deckIcon === key ? "is-selected" : ""}
                  onClick={() => {
                    setDeckIcon(key);
                    setIconMenuOpen(false);
                  }}
                  aria-label={`Use ${key} icon`}
                  aria-pressed={deckIcon === key}
                  role="menuitemradio"
                  aria-checked={deckIcon === key}
                >
                  <Icon size={14} />
                </button>
                ))}
              </div> : null}
            </div>
            <button type="submit" className="deck-create-button" disabled={!deckName.trim()}>
              <Plus size={14} /> Create deck
            </button>
          </form>
        ) : (
          <>
            <div className="deck-caption">
              <span><Layers3 size={13} /> {activeDeck?.label} deck</span>
              <small>{activeDeck?.legionDeck
                ? legionError
                  ? `Library unavailable: ${legionError}`
                  : "Deploy a saved formation with all of its internal links."
                : "Drag a card onto another deck tab to move it."}</small>
              {activeDeck?.custom ? (
                <button type="button" className="deck-delete-button" onClick={deleteActiveDeck} title="Delete this custom deck">
                  <Trash2 size={12} /> Delete
                </button>
              ) : null}
            </div>
            {activeDeck?.cards.length ? (
              <div className={`palette-items ${activeDeck.cards.length > 2 ? "has-many" : ""}`}>
                {activeDeck.cards.map((item) => {
                  const Icon = item.icon;
                  if (item.kind === "node") {
                    return (
                      <button
                        type="button"
                        key={`node:${item.type}`}
                        className={`palette-item palette-item--${item.type}`}
                        draggable
                        onDragStart={(event) => beginDrag(event, { version: 1, kind: "node", type: item.type })}
                        onClick={() => void createCard(item.type)}
                        aria-label={`Create ${item.label}`}
                        title="Drag to the canvas to create, or onto a deck tab to move"
                      >
                        <span className="palette-card-corner"><Icon size={15} /></span>
                        <span className="palette-item-icon"><Icon size={25} /></span>
                        <span className="palette-item-copy"><strong>{item.label}</strong><small>{item.detail}</small></span>
                        <span className="palette-draw"><Plus size={12} /> Draw</span>
                      </button>
                    );
                  }
                  const issueText = item.issues.join(" ") || "One or more required plugins are unavailable.";
                  return (
                    <div className="legion-palette-entry" key={`legion:${item.id}`}>
                      <button
                        type="button"
                        className={`palette-item palette-item--legion ${item.compatible ? "" : "is-incompatible"}`}
                        draggable={item.compatible}
                        onDragStart={(event) => {
                          if (item.compatible) beginDrag(event, {
                            version: 1,
                            kind: "legion",
                            id: item.id,
                            revision: item.revision,
                          });
                        }}
                        onClick={() => void instantiateLegion(item.id)}
                        disabled={!item.compatible}
                        aria-label={`Deploy Legion ${item.label}`}
                        title={item.compatible ? "Drag to deploy this complete formation" : issueText}
                      >
                        <span className="palette-card-corner">
                          {item.compatible ? <Icon size={15} /> : <AlertTriangle size={15} />}
                        </span>
                        <span className="palette-item-icon"><Icon size={25} /></span>
                        <span className="palette-item-copy"><strong>{item.label}</strong><small>{item.detail}</small></span>
                        <span className="palette-draw"><Plus size={12} /> Deploy</span>
                      </button>
                      <button
                        type="button"
                        className="legion-card-delete"
                        onClick={() => {
                          if (window.confirm(`Remove ${item.label} from the Legion library?`)) {
                            void deleteLegion(item.id);
                          }
                        }}
                        aria-label={`Delete Legion ${item.label}`}
                        title="Delete this Legion card"
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="deck-empty">
                {activeDeck?.legionDeck
                  ? legionError ? <AlertTriangle size={22} /> : <Layers3 size={22} />
                  : <Folder size={22} />}
                <strong>{activeDeck?.legionDeck
                  ? legionError ? "Legion library unavailable" : "No Legions collected"
                  : "Empty deck"}</strong>
                <small>{activeDeck?.legionDeck
                  ? legionError ?? "Select two or more canvas cards to save a formation."
                  : "Drag a card onto this deck tab."}</small>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
