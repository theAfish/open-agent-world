import { AlertTriangle, Layers3, LibraryBig, Plus, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type DragEvent } from "react";
import { DECK_ICONS, DeckIcon } from "../components/DeckIcon";
import { DeckHand } from "./DeckHand";
import { writePaletteDrag, type PaletteDragPayload } from "./dragPayload";
import { useWorldStore } from "../state/worldStore";
import { useCardLibrary } from "../state/cardLibrary";
import { CatalogIcon } from "../components/CatalogIcon";
import { useEquipmentDrag } from "../state/equipment";
import { buildCardDraft } from "../state/helpers";

// Compatibility helpers have no role in ongoing deck population.
export { defaultDecks, normalizeDecks } from "./legacyDecks";

export function ComponentPalette() {
  const library = useCardLibrary();
  const [editingDeck, setEditingDeck] = useState(false);
  const [showLegions, setShowLegions] = useState(false);
  const [deckName, setDeckName] = useState("");
  const [deckIcon, setDeckIcon] = useState("folder");
  const catalog = useWorldStore(state => state.catalog);
  const legions = useWorldStore(state => state.legions);
  const legionError = useWorldStore(state => state.legionError);
  const createCard = useWorldStore(state => state.createCard);
  const instantiateLegion = useWorldStore(state => state.instantiateLegion);
  const deleteLegion = useWorldStore(state => state.deleteLegion);
  const [removing, setRemoving] = useState(false);
  const [trashActive, setTrashActive] = useState(false);
  const dragged = useRef<{ kind: "node" | "legion"; id: string }>();
  const endDrag = () => { dragged.current = undefined; setTrashActive(false); useEquipmentDrag.getState().set(); };
  useEffect(() => { void library.refresh(); }, [library.refresh]);
  const snapshot = library.snapshot;
  const deck = showLegions ? { id: "legion-library", name: "Legions", icon: "layers",
    entries: legions.map(item => ({ kind: "legion" as const, id: item.id })) }
    : snapshot?.decks.find(item => item.id === snapshot.active_deck_id);
  const beginDrag = (event: DragEvent<HTMLButtonElement>, payload: PaletteDragPayload) => {
    writePaletteDrag(event.dataTransfer, payload);
    if (payload.kind === "node") useEquipmentDrag.getState().set({ ...buildCardDraft(payload.type, { x: 0, y: 0 }, catalog.node_types.find(item => item.id === payload.type)), id: "" });
  };
  return <aside className="component-palette" aria-label="Active card deck"
    style={{ "--deck-tab-count": (snapshot?.decks.length ?? 0) + 1 } as CSSProperties}>
    <div className="deck-tabs">
      <div className="deck-tab-scroll" role="tablist" aria-label="Card decks">
        {snapshot?.decks.map(item => <button type="button" role="tab" key={item.id} aria-selected={item.id === deck?.id}
          aria-controls="active-card-deck" className={item.id === deck?.id ? "is-active" : ""} disabled={library.busy}
          onClick={() => { setEditingDeck(false); setShowLegions(false); void library.edit({ action: "activate_deck", id: item.id }); }}>
          <span className="deck-tab-summary"><DeckIcon icon={item.icon} /><small>{item.entries.length}</small></span>
          <span className="deck-tab-label">{item.name}</span>
        </button>)}
        <button type="button" role="tab" aria-selected={showLegions} aria-controls="active-card-deck"
          className={showLegions ? "is-active" : ""} onClick={() => { setEditingDeck(false); setShowLegions(true); }}>
          <span className="deck-tab-summary"><Layers3 size={16} /><small>{legions.length}</small></span>
          <span className="deck-tab-label">Legions</span>
        </button>
      </div>
      <button type="button" className={`deck-add-button ${editingDeck ? "is-active" : ""}`} onClick={() => setEditingDeck(true)} aria-label="Create a new card deck" aria-expanded={editingDeck} title="Create a new deck"><Plus size={16} /></button>
    </div>
    <div className={`deck-stage ${editingDeck ? "is-editor" : ""}`} id="active-card-deck" role="tabpanel">
      {editingDeck ? <form className="deck-editor" onSubmit={async event => {
        event.preventDefault();
        if (!deckName.trim()) return;
        const saved = await library.edit({ action: "create_deck", name: deckName.trim(), icon: deckIcon });
        if (saved) { setEditingDeck(false); setShowLegions(false); setDeckName(""); setDeckIcon("folder"); }
      }}>
        <div className="deck-editor-heading"><div><strong>New deck</strong><small>Choose a name and icon.</small></div><button type="button" aria-label="Cancel creating deck" onClick={() => setEditingDeck(false)}><X size={15} /></button></div>
        <label className="deck-name-field"><span>Deck name</span><input autoFocus aria-label="Deck name" maxLength={120} value={deckName} onChange={event => setDeckName(event.target.value)} required /></label>
        <div className="deck-quick-create"><label><DeckIcon icon={deckIcon} /><select aria-label="Deck icon" value={deckIcon} onChange={event => setDeckIcon(event.target.value)}>{Object.keys(DECK_ICONS).map(icon => <option key={icon}>{icon}</option>)}</select></label>
          <button className="deck-create-button" disabled={library.busy || !deckName.trim()}><Plus size={14} /> Create deck</button></div>
        {library.error ? <small role="alert">{library.error}</small> : null}
      </form> : <>
      <div className="deck-caption"><span><DeckIcon icon={deck?.icon} size={13} /> {deck?.name ?? "Your deck"}</span><small>Drag into the world</small>
        <div className={`deck-trash ${trashActive ? "is-active" : ""}`} role="region" aria-label="Discard card"
          title={showLegions ? "Drop to delete saved Legion" : "Drop to remove from this deck"} aria-busy={removing}
          onDragOver={event => {
            if (!dragged.current || removing || library.busy) return;
            event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = "move"; setTrashActive(true);
          }}
          onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setTrashActive(false); }}
          onDrop={async event => {
            event.preventDefault(); event.stopPropagation();
            const entry = dragged.current;
            endDrag();
            if (!entry || !deck || removing || library.busy || !deck.entries.some(item => item.kind === entry.kind && item.id === entry.id)) return;
            const label = legions.find(item => item.id === entry.id)?.name ?? entry.id;
            if (showLegions && !window.confirm(`Delete saved Legion "${label}"? Existing formations in the world will remain.`)) return;
            setRemoving(true);
            try {
              if (showLegions) await deleteLegion(entry.id);
              else await library.edit({ action: "update_deck", id: deck.id,
                entries: deck.entries.filter(item => item.kind !== entry.kind || item.id !== entry.id) });
            } finally { setRemoving(false); }
          }}><Trash2 size={20} /></div>
      </div>
      {library.error ? <button className="deck-library-error" onClick={library.show}><AlertTriangle size={13} /> Library needs attention</button> : null}
      {showLegions && legionError ? <small role="alert">{legionError}</small> : null}
      {deck?.entries.length ? <DeckHand key={deck.id} className={`palette-items ${deck.entries.length > 2 ? "has-many" : ""}`}
        style={{ "--deck-card-count": deck.entries.length } as CSSProperties}>
        {deck.entries.map(entry => {
          const definition = entry.kind === "node" ? snapshot?.card_definitions[entry.id] : undefined;
          const legion = entry.kind === "legion" ? legions.find(item => item.id === entry.id) : undefined;
          const available = entry.kind === "node" ? snapshot?.available_card_ids.includes(entry.id) : legion?.compatible;
          const label = definition?.label ?? legion?.name ?? entry.id;
          const payload: PaletteDragPayload = entry.kind === "node" ? { version: 1, kind: "node", type: entry.id }
            : { version: 1, kind: "legion", id: entry.id, revision: legion?.revision ?? 0 };
          return <button type="button" key={`${entry.kind}:${entry.id}`} className="deck-hover-button" aria-disabled={!available}
            draggable={!removing && !library.busy} onDragStart={event => {
              dragged.current = entry;
              if (available) beginDrag(event, payload);
              else { event.dataTransfer.setData("text/plain", label); event.dataTransfer.effectAllowed = "move"; }
            }} onDragEnd={endDrag}
            onClick={() => { if (available) entry.kind === "node" ? void createCard(entry.id) : void instantiateLegion(entry.id); }}
            aria-label={available ? `Place ${label}` : `${label} unavailable`} title={available ? definition?.description ?? "Deploy saved formation" : "Content unavailable. Inspect it in the Library."}>
            <span className={`palette-item palette-item--${entry.kind === "node" ? entry.id : "legion"}`} data-deck-visual>
              <span className="palette-card-corner">{definition ? <CatalogIcon definition={definition} size={15} /> : <Layers3 size={15} />}</span>
              <span className="palette-item-icon">{available ? definition ? <CatalogIcon definition={definition} size={25} /> : <Layers3 size={25} /> : <AlertTriangle size={25} />}</span>
              <span className="palette-item-copy"><strong>{label}</strong><small>{available ? definition?.description ?? "Saved formation" : "Unavailable"}</small></span>
              <span className="palette-draw">{available ? "Place" : "Unavailable"}</span>
            </span>
          </button>;
        })}
      </DeckHand> : showLegions ? <div className="deck-empty"><Layers3 size={22} /><strong>No saved Legions</strong>
        <small>Form a Legion in the world, then save it to your library.</small></div>
      : <div className="deck-empty"><LibraryBig size={22} /><strong>{snapshot ? "Build your deck" : "Loading your deck"}</strong>
        <small>Open packs, collect cards, then choose what belongs here.</small><button className="secondary-button" onClick={library.show}>Open Library</button></div>}
      </>}
    </div>
  </aside>;
}
