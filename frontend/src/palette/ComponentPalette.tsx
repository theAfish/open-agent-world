import {
  Bot,
  Boxes,
  ChevronUp,
  FileText,
  Folder,
  Image as ImageIcon,
  Layers3,
  Plus,
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
import { CARD_TYPE_LABELS, type CardType } from "../types/world";

interface DeckCard {
  type: CardType;
  icon: LucideIcon;
  detail: string;
}

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
}

const DECKS_KEY = "open-agent-world.decks.v2";
const LEGACY_CUSTOM_DECKS_KEY = "open-agent-world.custom-decks.v1";

const CARD_CATALOG: Record<CardType, DeckCard> = {
  agent: { type: "agent", icon: Bot, detail: "Reasoning worker" },
  text: { type: "text", icon: FileText, detail: "Managed knowledge" },
  image: { type: "image", icon: ImageIcon, detail: "Visual resource" },
  sandbox: { type: "sandbox", icon: Workflow, detail: "Secure work field" },
};

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

const DEFAULT_DECKS: StoredDeck[] = [
  { id: "agents", label: "Agents", icon: "bot", cardTypes: ["agent"] },
  { id: "objects", label: "Objects", icon: "boxes", cardTypes: ["text", "image"] },
  { id: "fields", label: "Fields", icon: "workflow", cardTypes: ["sandbox"] },
];

const DEFAULT_HOME: Record<CardType, string> = {
  agent: "agents",
  text: "objects",
  image: "objects",
  sandbox: "fields",
};

const isCardType = (value: unknown): value is CardType => (
  value === "agent" || value === "text" || value === "image" || value === "sandbox"
);

const isDeckIcon = (value: unknown): value is DeckIconKey => (
  value === "bot" || value === "boxes" || value === "workflow" || value === "folder"
  || value === "layers" || value === "sparkles" || value === "star" || value === "zap"
);

function normalizeDecks(candidates: StoredDeck[]): StoredDeck[] {
  const decks = candidates.map((deck) => ({ ...deck, cardTypes: [] as CardType[] }));
  const claimed = new Set<CardType>();
  candidates.forEach((candidate, index) => {
    candidate.cardTypes.forEach((type) => {
      if (!claimed.has(type)) {
        decks[index].cardTypes.push(type);
        claimed.add(type);
      }
    });
  });
  (Object.keys(CARD_CATALOG) as CardType[]).forEach((type) => {
    if (claimed.has(type)) return;
    const home = decks.find((deck) => deck.id === DEFAULT_HOME[type]) ?? decks[0];
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
        custom: candidate.custom ?? !DEFAULT_DECKS.some((item) => item.id === candidate.id),
      }];
    });
  } catch {
    return [];
  }
}

function loadDecks(): StoredDeck[] {
  if (typeof window === "undefined") return DEFAULT_DECKS;
  const stored = parseStoredDecks(window.localStorage.getItem(DECKS_KEY));
  if (stored.length > 0) {
    const missingDefaults = DEFAULT_DECKS.filter((deck) => !stored.some((item) => item.id === deck.id));
    return normalizeDecks([...missingDefaults, ...stored]);
  }

  const legacy = parseStoredDecks(window.localStorage.getItem(LEGACY_CUSTOM_DECKS_KEY));
  const migrated = DEFAULT_DECKS.map((deck) => ({ ...deck, cardTypes: [...deck.cardTypes] }));
  legacy.forEach((legacyDeck) => {
    legacyDeck.cardTypes.forEach((type) => {
      migrated.forEach((deck) => {
        deck.cardTypes = deck.cardTypes.filter((candidate) => candidate !== type);
      });
    });
    migrated.push({ ...legacyDeck, icon: legacyDeck.icon ?? "folder", custom: true });
  });
  return normalizeDecks(migrated);
}

function materializeDeck(deck: StoredDeck): CardDeck {
  return {
    ...deck,
    iconComponent: DECK_ICONS[deck.icon],
    cards: deck.cardTypes.map((type) => CARD_CATALOG[type]),
  };
}

export function ComponentPalette() {
  const createCard = useWorldStore((state) => state.createCard);
  const [storedDecks, setStoredDecks] = useState<StoredDeck[]>(loadDecks);
  const [activeDeckId, setActiveDeckId] = useState("agents");
  const [editingDeck, setEditingDeck] = useState(false);
  const [deckName, setDeckName] = useState("");
  const [deckIcon, setDeckIcon] = useState<DeckIconKey>("folder");
  const [iconMenuOpen, setIconMenuOpen] = useState(false);
  const [dragOverDeckId, setDragOverDeckId] = useState<string>();
  const decks = storedDecks.map(materializeDeck);
  const activeDeck = decks.find((deck) => deck.id === activeDeckId) ?? decks[0];

  useEffect(() => {
    window.localStorage.setItem(DECKS_KEY, JSON.stringify(storedDecks));
  }, [storedDecks]);

  const beginDrag = (event: DragEvent<HTMLButtonElement>, type: CardType) => {
    event.dataTransfer.setData("application/open-agent-card", type);
    event.dataTransfer.effectAllowed = "copyMove";
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
        restoredByDeck.set(DEFAULT_HOME[type], [...(restoredByDeck.get(DEFAULT_HOME[type]) ?? []), type]);
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
                  if (!event.dataTransfer.types.includes("application/open-agent-card")) return;
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
                  const type = event.dataTransfer.getData("application/open-agent-card");
                  if (isCardType(type)) moveCardToDeck(type, deck.id);
                }}
                title={`${deck.label}: ${deck.cards.length} cards. Drop a card here to move it.`}
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
              <small>Drag a card onto another deck tab to move it.</small>
              {activeDeck?.custom ? (
                <button type="button" className="deck-delete-button" onClick={deleteActiveDeck} title="Delete this custom deck">
                  <Trash2 size={12} /> Delete
                </button>
              ) : null}
            </div>
            {activeDeck?.cards.length ? (
              <div className={`palette-items ${activeDeck.cards.length > 2 ? "has-many" : ""}`}>
                {activeDeck.cards.map(({ type, icon: Icon, detail }) => (
                  <button
                    type="button"
                    key={type}
                    className={`palette-item palette-item--${type}`}
                    draggable
                    onDragStart={(event) => beginDrag(event, type)}
                    onClick={() => void createCard(type)}
                    aria-label={`Create ${CARD_TYPE_LABELS[type]}`}
                    title="Drag to the canvas to create, or onto a deck tab to move"
                  >
                    <span className="palette-card-corner"><Icon size={15} /></span>
                    <span className="palette-item-icon"><Icon size={25} /></span>
                    <span className="palette-item-copy"><strong>{CARD_TYPE_LABELS[type]}</strong><small>{detail}</small></span>
                    <span className="palette-draw"><Plus size={12} /> Draw</span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="deck-empty"><Folder size={22} /><strong>Empty deck</strong><small>Drag a card onto this deck tab.</small></div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
