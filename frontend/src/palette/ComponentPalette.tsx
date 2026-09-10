import { AlertTriangle, Layers3, LibraryBig, Plus, X } from "lucide-react";
import { useEffect, useState, type CSSProperties, type DragEvent } from "react";
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
  const [deckName, setDeckName] = useState("");
  const [deckIcon, setDeckIcon] = useState("folder");
  const catalog = useWorldStore(state => state.catalog);
  const legions = useWorldStore(state => state.legions);
  const createCard = useWorldStore(state => state.createCard);
  const instantiateLegion = useWorldStore(state => state.instantiateLegion);
  useEffect(() => { void library.refresh(); }, [library.refresh]);
  const snapshot = library.snapshot;
  const deck = snapshot?.decks.find(item => item.id === snapshot.active_deck_id);
  const beginDrag = (event: DragEvent<HTMLButtonElement>, payload: PaletteDragPayload) => {
    writePaletteDrag(event.dataTransfer, payload);
    if (payload.kind === "node") useEquipmentDrag.getState().set({ ...buildCardDraft(payload.type, { x: 0, y: 0 }, catalog.node_types.find(item => item.id === payload.type)), id: "" });
  };
  return <aside className="component-palette" aria-label="Active card deck">
    <div className="deck-tabs">
      <div className="deck-tab-scroll" role="tablist" aria-label="Card decks">
        {snapshot?.decks.map(item => <button type="button" role="tab" key={item.id} aria-selected={item.id === deck?.id}
          aria-controls="active-card-deck" className={item.id === deck?.id ? "is-active" : ""} disabled={library.busy}
          onClick={() => { setEditingDeck(false); void library.edit({ action: "activate_deck", id: item.id }); }}>
          <span className="deck-tab-summary"><DeckIcon icon={item.icon} /><small>{item.entries.length}</small></span>
          <span className="deck-tab-label">{item.name}</span>
        </button>)}
      </div>
      <button type="button" className={`deck-add-button ${editingDeck ? "is-active" : ""}`} onClick={() => setEditingDeck(true)} aria-label="Create a new card deck" aria-expanded={editingDeck} title="Create a new deck"><Plus size={16} /></button>
    </div>
    <div className={`deck-stage ${editingDeck ? "is-editor" : ""}`} id="active-card-deck" role="tabpanel">
      {editingDeck ? <form className="deck-editor" onSubmit={async event => {
        event.preventDefault();
        if (!deckName.trim()) return;
        const saved = await library.edit({ action: "create_deck", name: deckName.trim(), icon: deckIcon });
        if (saved) { setEditingDeck(false); setDeckName(""); setDeckIcon("folder"); }
      }}>
        <div className="deck-editor-heading"><div><strong>New deck</strong><small>Choose a name and icon.</small></div><button type="button" aria-label="Cancel creating deck" onClick={() => setEditingDeck(false)}><X size={15} /></button></div>
        <label className="deck-name-field"><span>Deck name</span><input autoFocus aria-label="Deck name" maxLength={120} value={deckName} onChange={event => setDeckName(event.target.value)} required /></label>
        <div className="deck-quick-create"><label><DeckIcon icon={deckIcon} /><select aria-label="Deck icon" value={deckIcon} onChange={event => setDeckIcon(event.target.value)}>{Object.keys(DECK_ICONS).map(icon => <option key={icon}>{icon}</option>)}</select></label>
          <button className="deck-create-button" disabled={library.busy || !deckName.trim()}><Plus size={14} /> Create deck</button></div>
        {library.error ? <small role="alert">{library.error}</small> : null}
      </form> : <>
      <div className="deck-caption"><span><DeckIcon icon={deck?.icon} size={13} /> {deck?.name ?? "Your deck"}</span><small>Drag into the world · Manage cards in the Library</small></div>
      {library.error ? <button className="deck-library-error" onClick={library.show}><AlertTriangle size={13} /> Library needs attention</button> : null}
      {deck?.entries.length ? <DeckHand key={deck.id} className={`palette-items ${deck.entries.length > 2 ? "has-many" : ""}`}
        style={{ "--deck-card-count": deck.entries.length } as CSSProperties}>
        {deck.entries.map(entry => {
          const definition = entry.kind === "node" ? snapshot?.card_definitions[entry.id] : undefined;
          const legion = entry.kind === "legion" ? legions.find(item => item.id === entry.id) : undefined;
          const available = entry.kind === "node" ? snapshot?.available_card_ids.includes(entry.id) : legion?.compatible;
          const label = definition?.label ?? legion?.name ?? entry.id;
          const payload: PaletteDragPayload = entry.kind === "node" ? { version: 1, kind: "node", type: entry.id }
            : { version: 1, kind: "legion", id: entry.id, revision: legion?.revision ?? 0 };
          return <button type="button" key={`${entry.kind}:${entry.id}`} className="deck-hover-button" disabled={!available}
            draggable={!!available} onDragStart={event => beginDrag(event, payload)} onDragEnd={() => useEquipmentDrag.getState().set()}
            onClick={() => entry.kind === "node" ? void createCard(entry.id) : void instantiateLegion(entry.id)}
            aria-label={available ? `Place ${label}` : `${label} unavailable`} title={available ? definition?.description ?? "Deploy saved formation" : "Content unavailable. Inspect it in the Library."}>
            <span className={`palette-item palette-item--${entry.kind === "node" ? entry.id : "legion"}`} data-deck-visual>
              <span className="palette-card-corner">{definition ? <CatalogIcon definition={definition} size={15} /> : <Layers3 size={15} />}</span>
              <span className="palette-item-icon">{available ? definition ? <CatalogIcon definition={definition} size={25} /> : <Layers3 size={25} /> : <AlertTriangle size={25} />}</span>
              <span className="palette-item-copy"><strong>{label}</strong><small>{available ? definition?.description ?? "Saved formation" : "Unavailable"}</small></span>
              <span className="palette-draw">{available ? "Place" : "Unavailable"}</span>
            </span>
          </button>;
        })}
      </DeckHand> : <div className="deck-empty"><LibraryBig size={22} /><strong>{snapshot ? "Build your deck" : "Loading your deck"}</strong>
        <small>Open packs, collect cards, then choose what belongs here.</small><button className="secondary-button" onClick={library.show}>Open Library</button></div>}
      </>}
    </div>
  </aside>;
}
