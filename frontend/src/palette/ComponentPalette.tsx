import {
  Bot,
  Boxes,
  FileText,
  Image as ImageIcon,
  Layers3,
  Plus,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useState, type DragEvent } from "react";
import { useWorldStore } from "../state/worldStore";
import { CARD_TYPE_LABELS, type CardType } from "../types/world";

type DeckId = "agents" | "objects" | "fields";

interface DeckCard {
  type: CardType;
  icon: LucideIcon;
  detail: string;
}

interface CardDeck {
  id: DeckId;
  label: string;
  description: string;
  icon: LucideIcon;
  cards: DeckCard[];
}

// Keeping deck definitions separate makes this tray ready for user-defined
// groupings and additional card types later.
const DECKS: CardDeck[] = [
  {
    id: "agents",
    label: "Agents",
    description: "Workers and collaborators",
    icon: Bot,
    cards: [{ type: "agent", icon: Bot, detail: "Reasoning worker" }],
  },
  {
    id: "objects",
    label: "Objects",
    description: "Knowledge and resources",
    icon: Boxes,
    cards: [
      { type: "text", icon: FileText, detail: "Managed knowledge" },
      { type: "image", icon: ImageIcon, detail: "Visual resource" },
    ],
  },
  {
    id: "fields",
    label: "Fields",
    description: "Sandboxes and workspaces",
    icon: Workflow,
    cards: [{ type: "sandbox", icon: Workflow, detail: "Secure work field" }],
  },
];

export function ComponentPalette() {
  const createCard = useWorldStore((state) => state.createCard);
  const [activeDeckId, setActiveDeckId] = useState<DeckId>("agents");
  const activeDeck = DECKS.find((deck) => deck.id === activeDeckId) ?? DECKS[0];

  const beginDrag = (event: DragEvent<HTMLButtonElement>, type: CardType) => {
    event.dataTransfer.setData("application/open-agent-card", type);
    event.dataTransfer.effectAllowed = "copy";
  };

  return (
    <aside className="component-palette" aria-label="Card deck library">
      <div className="deck-tabs" role="tablist" aria-label="Card decks">
        {DECKS.map((deck) => {
          const DeckIcon = deck.icon;
          const selected = activeDeck.id === deck.id;
          return (
            <button
              type="button"
              key={deck.id}
              className={selected ? "is-active" : ""}
              role="tab"
              aria-selected={selected}
              aria-controls="active-card-deck"
              onClick={() => setActiveDeckId(deck.id)}
              title={deck.description}
            >
              <DeckIcon size={15} />
              <span>{deck.label}</span>
              <small>{deck.cards.length}</small>
            </button>
          );
        })}
      </div>

      <div className="deck-stage" id="active-card-deck" role="tabpanel">
        <div className="deck-caption">
          <span><Layers3 size={13} /> {activeDeck.label} deck</span>
          <small>{activeDeck.description}</small>
        </div>
        <div className="palette-items">
          {activeDeck.cards.map(({ type, icon: Icon, detail }) => (
            <button
              type="button"
              key={type}
              className={`palette-item palette-item--${type}`}
              draggable
              onDragStart={(event) => beginDrag(event, type)}
              onClick={() => void createCard(type)}
              aria-label={`Create ${CARD_TYPE_LABELS[type]}`}
              title="Drag to position, or click to draw at the center"
            >
              <span className="palette-card-corner"><Icon size={15} /></span>
              <span className="palette-item-icon"><Icon size={25} /></span>
              <span className="palette-item-copy"><strong>{CARD_TYPE_LABELS[type]}</strong><small>{detail}</small></span>
              <span className="palette-draw"><Plus size={12} /> Draw</span>
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}
