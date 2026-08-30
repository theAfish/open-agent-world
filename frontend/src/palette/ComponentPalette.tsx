import {
  Bot,
  Boxes,
  ChevronLeft,
  ChevronRight,
  FileText,
  FlaskConical,
  Image as ImageIcon,
  Plus,
  Trash2,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import type { DragEvent } from "react";
import { useWorldStore } from "../state/worldStore";
import { CARD_TYPE_LABELS, type CardType } from "../types/world";

const ITEMS: Array<{
  type: CardType;
  icon: LucideIcon;
  detail: string;
}> = [
  { type: "agent", icon: Bot, detail: "Reasoning worker" },
  { type: "text", icon: FileText, detail: "Managed text" },
  { type: "image", icon: ImageIcon, detail: "Visual resource" },
  { type: "sandbox", icon: Workflow, detail: "Secure workplace" },
];

export function ComponentPalette() {
  const collapsed = useWorldStore((state) => state.paletteCollapsed);
  const toggle = useWorldStore((state) => state.togglePalette);
  const createCard = useWorldStore((state) => state.createCard);
  const stressCount = useWorldStore((state) => state.stressCards.length);
  const generateStress = useWorldStore((state) => state.generateStressWorld);
  const clearStress = useWorldStore((state) => state.clearStressWorld);

  const beginDrag = (event: DragEvent<HTMLButtonElement>, type: CardType) => {
    event.dataTransfer.setData("application/open-agent-card", type);
    event.dataTransfer.effectAllowed = "copy";
  };

  return (
    <aside className={`component-palette ${collapsed ? "is-collapsed" : ""}`} aria-label="World object palette">
      <header>
        <div className="palette-title">
          <div className="palette-mark"><Boxes size={17} /></div>
          {!collapsed ? <div><span>Object library</span><strong>Place into world</strong></div> : null}
        </div>
        <button type="button" className="icon-button" onClick={toggle} aria-label={collapsed ? "Expand object palette" : "Collapse object palette"}>
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </header>

      <div className="palette-items">
        {ITEMS.map(({ type, icon: Icon, detail }) => (
          <button
            type="button"
            key={type}
            className={`palette-item palette-item--${type}`}
            draggable
            onDragStart={(event) => beginDrag(event, type)}
            onClick={() => void createCard(type)}
            aria-label={`Create ${CARD_TYPE_LABELS[type]}`}
            title={collapsed ? `Create ${CARD_TYPE_LABELS[type]}` : "Drag to position, or click to place at center"}
          >
            <span className="palette-item-icon"><Icon size={18} /></span>
            {!collapsed ? (
              <span className="palette-item-copy"><strong>{CARD_TYPE_LABELS[type]}</strong><small>{detail}</small></span>
            ) : null}
            {!collapsed ? <Plus size={14} className="palette-plus" /> : null}
          </button>
        ))}
      </div>

      {!collapsed ? (
        <section className="developer-tools">
          <div className="developer-heading"><FlaskConical size={13} /><span>Scale probe</span></div>
          <p>Populate distant chunks with lightweight local cards.</p>
          {stressCount === 0 ? (
            <button type="button" onClick={() => generateStress(1500)}>Generate 1,500 cards</button>
          ) : (
            <button type="button" onClick={clearStress}><Trash2 size={13} /> Clear {stressCount.toLocaleString()}</button>
          )}
        </section>
      ) : null}
    </aside>
  );
}
