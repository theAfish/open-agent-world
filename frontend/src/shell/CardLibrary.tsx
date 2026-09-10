import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Archive, Check, ChevronLeft, ChevronRight, Layers3, LibraryBig, PackageOpen, Plus, Search, Store, X } from "lucide-react";
import { CatalogIcon } from "../components/CatalogIcon";
import { useCardLibrary, type DeckEntry } from "../state/cardLibrary";
import { useWorldStore } from "../state/worldStore";
import { collectedLibraryCards, formationSource, libraryCardMatches, libraryCardMetadata, type LibraryCard } from "./libraryCatalog";
import "./cardLibrary.css";

type Tab = "packs" | "cards" | "decks" | "store";
const PAGE_SIZE = 30;
const same = (a: DeckEntry, b: DeckEntry) => a.kind === b.kind && a.id === b.id;

export function CardLibrary() {
  const library = useCardLibrary();
  const legions = useWorldStore(state => state.legions);
  const deleteLegion = useWorldStore(state => state.deleteLegion);
  const modal = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<Tab>("packs");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [showInternal, setShowInternal] = useState(false);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<DeckEntry | null>(null);
  const [deckId, setDeckId] = useState("");
  const [name, setName] = useState("");
  const [rename, setRename] = useState("");
  const [reveal, setReveal] = useState<string | null>(null);
  useEffect(() => {
    if (library.open) modal.current?.showModal();
    else modal.current?.close();
  }, [library.open]);
  useEffect(() => { setPage(0); }, [query, filter, sourceFilter, showInternal, tab]);
  const snapshot = library.snapshot;
  const deck = snapshot?.decks.find(item => item.id === deckId) ?? snapshot?.decks.find(item => item.id === snapshot.active_deck_id);
  useEffect(() => { setRename(deck?.name ?? ""); }, [deck?.id, deck?.name]);
  const cards = snapshot ? collectedLibraryCards(snapshot) : [];
  const formations: LibraryCard[] = legions.map(item => ({ kind: "legion", id: item.id, label: item.name,
    description: `${item.node_count} cards · ${item.edge_count} links`, category: "Saved Legions", available: item.compatible,
    internal: false, sources: [formationSource], owners: [] }));
  const allCards = [...cards, ...formations];
  const sources = [...new Map(allCards.flatMap(item => item.sources).map(source => [source.id, source])).values()]
    .sort((a, b) => a.name.localeCompare(b.name));
  const categories = [...new Set(allCards.map(item => item.category))].sort();
  const matching = allCards.filter(item => (!filter || item.category === filter)
    && (!sourceFilter || item.sources.some(source => source.id === sourceFilter)) && libraryCardMatches(item, query));
  const internalCount = matching.filter(item => item.internal).length;
  const sourceFor = (item: LibraryCard) => item.sources.find(source => source.id === sourceFilter) ?? item.sources[0];
  const filtered = matching.filter(item => showInternal || !item.internal).sort((a, b) =>
    sourceFor(a).name.localeCompare(sourceFor(b).name) || sourceFor(a).id.localeCompare(sourceFor(b).id)
    || Number(a.internal) - Number(b.internal) || a.label.localeCompare(b.label));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);
  const pageCards = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  // Multi-pack cards appear once in All packs, and remain discoverable through every source filter.
  const groups = sources.flatMap(source => {
    const items = pageCards.filter(item => sourceFor(item).id === source.id);
    return items.length ? [{ source, items, count: filtered.filter(item => sourceFor(item).id === source.id).length }] : [];
  });
  const detail = selected && allCards.find(item => same(item, selected));
  const browsePack = (id: string) => { setTab("cards"); setQuery(""); setFilter(""); setSourceFilter(`pack:${id}`); setReveal(null); };
  const toggleCard = (entry: DeckEntry) => {
    if (!deck) return;
    const entries = deck.entries.some(item => same(item, entry)) ? deck.entries.filter(item => !same(item, entry)) : [...deck.entries, { kind: entry.kind, id: entry.id }];
    void library.edit({ action: "update_deck", id: deck.id, entries });
  };
  const deckSelect = <label className="library-deck-select">Deck<select aria-label="Selected deck" value={deck?.id ?? ""} onChange={event => setDeckId(event.target.value)}>
    {snapshot?.decks.map(item => <option key={item.id} value={item.id}>{item.name}{item.id === snapshot.active_deck_id ? " · Active" : ""}</option>)}
  </select></label>;
  const renderCard = (item: LibraryCard) => {
    const included = deck?.entries.some(entry => same(entry, item));
    return <article key={`${item.kind}:${item.id}`} className={`library-card ${selected && same(selected, item) ? "is-selected" : ""}`} style={{ "--collection-color": item.definition?.color ?? "#78967b" } as CSSProperties}>
      <button className="library-card-inspect" onClick={() => setSelected(item)} aria-label={`Inspect ${item.label}`}><CatalogIcon definition={item.definition} size={26} /><span>{item.category}</span><strong>{item.label}</strong><small>{item.description}</small>
        {!item.available && !item.internal ? <span className="library-unavailable">Unavailable</span> : null}</button>
      {item.internal && !included ? <div className="library-card-usage">{item.owners.length ? "Use through its container" : "Created by a world action"}</div> :
        <button className={`library-card-add ${included ? "is-in-deck" : ""}`} disabled={library.busy || !deck || (!included && !item.available)} onClick={() => toggleCard(item)} aria-label={`${included ? "Remove" : "Add"} ${item.label} ${included ? "from" : "to"} deck`}>
          {included ? <Check size={14} /> : <Plus size={14} />}{included ? "In deck · Remove" : "Add to deck"}</button>}
    </article>;
  };

  return <dialog ref={modal} className="card-library-modal" aria-labelledby="card-library-title" onCancel={event => { event.preventDefault(); library.close(); }}
    onClick={event => { if (event.target === event.currentTarget) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) library.close(); } }}>
    <header className="library-header"><div className="dialog-icon"><LibraryBig size={22} /></div><div><span>Your collection</span><h2 id="card-library-title">Pack & Card Library</h2></div>
      <button className="top-icon-button" aria-label="Close Library" onClick={library.close}><X size={18} /></button></header>
    <nav className="library-tabs" aria-label="Library sections">{([
      ["packs", "Packs", Archive], ["cards", "Cards", LibraryBig], ["decks", "Decks", Layers3], ["store", "Store", Store],
    ] as const).map(([id, label, Icon]) => <button key={id} className={tab === id ? "is-active" : ""} aria-pressed={tab === id} onClick={() => { setTab(id); setReveal(null); }}>
      <Icon size={16} />{label}<small>{id === "packs" ? Object.keys(snapshot?.packs ?? {}).length : id === "cards" ? allCards.length : id === "decks" ? snapshot?.decks.length ?? 0 : "Soon"}</small>
    </button>)}</nav>
    <div className="library-flow">Open a pack <ChevronRight size={12} /> Collect cards <ChevronRight size={12} /> Build a deck <ChevronRight size={12} /> Place in your world</div>
    {library.error ? <div className="library-error" role="alert">{library.error}<button onClick={() => void library.refresh()}>Refresh</button></div> : null}
    {!snapshot ? <div className="library-empty">{library.error ? "Your collection could not be loaded." : "Loading your collection…"}</div> :
      <div className="library-body">
        {tab === "packs" ? <>
          <div className="library-section-heading"><div><h3>Pack inventory</h3><p>Open an owned pack to add its contents to your Card Library.</p></div>
            <label className="library-search"><Search size={15} /><input aria-label="Search packs" placeholder="Search packs" value={query} onChange={event => setQuery(event.target.value)} /></label></div>
          {reveal && snapshot.packs[reveal] ? <div className="pack-reveal" role="status"><PackageOpen size={32} /><div><strong>{snapshot.packs[reveal].definition.name} opened</strong>
            <p>{snapshot.packs[reveal].definition.cards.length} cards are now in your collection. Choose which ones to add to a deck.</p></div>
            <button className="primary-button" onClick={() => browsePack(reveal)}>Browse cards <ChevronRight size={14} /></button></div> : null}
          <div className="pack-grid">{Object.values(snapshot.packs).filter(pack => `${pack.definition.name} ${pack.definition.description} ${pack.definition.plugin_id}`.toLowerCase().includes(query.toLowerCase())).map(pack => {
            const plugin = snapshot.plugins[pack.definition.plugin_id];
            const available = snapshot.available_pack_ids.includes(pack.definition.id);
            const newCards = pack.definition.cards.filter(id => !snapshot.collection[id]?.source_pack_ids.includes(pack.definition.id)).length;
            return <article className={`library-pack ${pack.opened ? "is-opened" : ""}`} key={pack.definition.id}>
              <div className="pack-topline"><Archive size={24} /><span className="library-badge">{pack.opened ? "Opened" : "Unopened"}</span></div>
              <h4>{pack.definition.name}</h4><p>{pack.definition.description || plugin?.descriptor.description || "A collection of capabilities for your world."}</p>
              <small className="pack-source">{plugin?.descriptor.name ?? pack.definition.plugin_id} · v{plugin?.descriptor.version}</small>
              <details><summary>{pack.definition.cards.length} cards · Preview contents</summary><ul>{pack.definition.cards.map(id => {
                const card = snapshot.card_definitions[id];
                return <li key={id}>{card ? libraryCardMetadata(snapshot, card).label : id}{card?.user_creatable === false ? " · Internal card" : ""}</li>;
              })}</ul></details>
              <div className="pack-actions"><span className="plugin-status">{!plugin?.installed ? "Plugin uninstalled" : !plugin.enabled ? "Plugin disabled" : !available ? "Pack unavailable" : "Installed · Enabled"}</span>
                {plugin?.installed && pack.definition.plugin_id !== "open-agent-world.core" ? <button className="library-text-button" disabled={library.busy} onClick={() => void library.edit({ action: "set_plugin_enabled", id: pack.definition.plugin_id, enabled: !plugin.enabled })}>{plugin.enabled ? "Disable plugin" : "Enable plugin"}</button> : null}</div>
              <button className={pack.opened && !newCards ? "secondary-button" : "primary-button"} disabled={library.busy || !available || !pack.owned || (pack.opened && !newCards)}
                onClick={async () => { const saved = await library.edit({ action: "open_pack", id: pack.definition.id }); if (saved) setReveal(pack.definition.id); }}>
                {pack.opened && !newCards ? <Check size={15} /> : <PackageOpen size={15} />}{!pack.opened ? "Open Pack" : newCards ? `Collect ${newCards} new cards` : "Collected"}</button>
              {pack.opened ? <button className="library-text-button" onClick={() => browsePack(pack.definition.id)}>View collected cards <ChevronRight size={14} /></button> : null}
            </article>;
          })}</div>
        </> : null}
        {tab === "cards" ? <>
          <div className="library-section-heading"><div><h3>Card Library</h3><p>{allCards.length} collected cards and saved formations · Grouped by source pack</p></div>{deckSelect}</div>
          <div className="library-tools"><label className="library-search"><Search size={15} /><input aria-label="Search cards" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search your cards" /></label>
            <select aria-label="Source pack" value={sourceFilter} onChange={event => setSourceFilter(event.target.value)}><option value="">All packs</option>{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select>
            <select aria-label="Card category" value={filter} onChange={event => setFilter(event.target.value)}><option value="">All categories</option>{categories.map(category => <option key={category}>{category}</option>)}</select>
            <label className="library-internal-toggle"><input type="checkbox" checked={showInternal} onChange={event => setShowInternal(event.target.checked)} />Show internal cards{internalCount ? ` (${internalCount})` : ""}</label>
            <span>{filtered.length} results</span></div>
          <div className="library-card-layout"><div>
            {groups.map(({ source, items, count }) => <details key={JSON.stringify([source.id, currentPage, query, filter, sourceFilter, showInternal])} className="library-source-group"
              open={Boolean(sourceFilter || query.trim() || filter || showInternal || sources.length === 1)}>
              <summary><Archive size={18} /><div><h4>{source.name}</h4><small>{source.pluginName}</small></div><span>{count} {count === 1 ? "card" : "cards"}</span><ChevronRight className="library-group-chevron" size={16} /></summary>
              {items.some(item => !item.internal) ? <div className="library-card-grid">{items.filter(item => !item.internal).map(renderCard)}</div> : null}
              {items.some(item => item.internal) ? <div className="library-internal-cards"><p>Internal cards · Created through containers or world actions</p><div className="library-card-grid">{items.filter(item => item.internal).map(renderCard)}</div></div> : null}
            </details>)}
            {!filtered.length ? <div className="library-empty"><LibraryBig size={30} /><strong>{allCards.length ? "No matching cards" : "Your collection starts with a pack"}</strong><p>{!showInternal && internalCount ? `${internalCount} matching internal cards are hidden. Show internal cards to inspect their purpose and container.` : allCards.length ? "Try another search, source pack or category." : "Visit Packs and open one to discover its cards."}</p><button className="secondary-button" onClick={() => { if (!showInternal && internalCount) setShowInternal(true); else { setTab("packs"); setQuery(""); } }}>{!showInternal && internalCount ? "Show internal cards" : "Browse packs"}</button></div> : null}
            {pages > 1 ? <div className="library-pagination"><button aria-label="Previous cards" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16} /></button><span>Page {currentPage + 1} of {pages}</span><button aria-label="Next cards" disabled={currentPage >= pages - 1} onClick={() => setPage(currentPage + 1)}><ChevronRight size={16} /></button></div> : null}
          </div><aside className="library-card-detail" aria-label="Card details">{detail ? <><CatalogIcon definition={detail.definition} size={36} /><span className="library-badge">{detail.category}</span><h3>{detail.label}</h3><p>{detail.description}</p>
            {detail.internal ? <div className="library-usage-detail"><small>How to use</small><p>{detail.owners.length ? `Open ${detail.owners.map(owner => owner.label).join(" or ")} to use this card. It is created inside the container.` : "This card is created by a container or world action."} It cannot be added to a deck on its own.</p>
              {detail.owners.map(owner => snapshot.collection[owner.id]?.unlocked ? <button key={owner.id} className="library-text-button" onClick={() => setSelected({ kind: "node", id: owner.id })}>Inspect {libraryCardMetadata(snapshot, owner).label} <ChevronRight size={14} /></button> : <p key={owner.id}>Open its source pack to collect {owner.label}.</p>)}</div> : null}
            {detail.definition ? <><small>Source plugin</small><p>{snapshot.plugins[detail.definition.plugin_id]?.descriptor.name ?? detail.definition.plugin_id}</p><small>Collected from</small><p>{detail.sources.map(source => source.name).join(", ")}</p><small>Collected {new Date(snapshot.collection[detail.id].unlocked_at).toLocaleDateString()}</small></> : <p>A formation you saved from your world. Its members keep their original plugin dependencies.</p>}
            {(detail.definition && (!snapshot.plugins[detail.definition.plugin_id]?.installed || !snapshot.plugins[detail.definition.plugin_id]?.enabled)) || (!detail.available && !detail.internal) ? <p className="library-unavailable">{detail.kind === "legion" ? "This formation has unavailable dependencies." : "This content is unavailable. Install or enable its plugin to use it again."}</p> : null}
            {!detail.internal || deck?.entries.some(entry => same(entry, detail)) ? <button className="secondary-button" disabled={library.busy || !deck || (!detail.available && !deck.entries.some(entry => same(entry, detail)))}
              aria-label={deck?.entries.some(entry => same(entry, detail)) ? "Remove inspected card from deck" : "Add inspected card to deck"} onClick={() => toggleCard(detail)}>
              {deck?.entries.some(entry => same(entry, detail)) ? "Remove from deck" : "Add to deck"}</button> : null}
            {detail.kind === "legion" ? <button className="library-text-button" onClick={() => { if (window.confirm(`Remove ${detail.label} from the Legion library?`)) void deleteLegion(detail.id); }}>Delete saved formation</button> : null}
          </> : <><Layers3 size={30} /><h3>Explore a card</h3><p>Select a card to inspect its purpose and origin.</p><p>Removing a card from a deck keeps it in your collection.</p></>}</aside></div>
        </> : null}
        {tab === "decks" ? <>
          <div className="library-section-heading"><div><h3>Your decks</h3><p>The active deck is the hand shown at the bottom of your world.</p></div>{deckSelect}</div>
          <div className="library-deck-actions"><form onSubmit={event => { event.preventDefault(); if (name.trim()) void library.edit({ action: "create_deck", name: name.trim() }).then(saved => { if (saved) { setDeckId(saved.active_deck_id); setName(""); } }); }}>
            <input aria-label="New deck name" placeholder="Name a new deck" maxLength={120} value={name} onChange={event => setName(event.target.value)} /><button className="secondary-button" disabled={library.busy || !name.trim()}><Plus size={14} /> Create deck</button></form></div>
          {deck ? <section className="library-deck-editor"><div className="library-section-heading"><div><h3>{deck.name}</h3><p>{deck.entries.length} cards · {deck.id === snapshot.active_deck_id ? "Active in your world" : "Ready to activate"}</p></div>
            <button className="primary-button" disabled={library.busy || deck.id === snapshot.active_deck_id} onClick={() => void library.edit({ action: "activate_deck", id: deck.id })}>{deck.id === snapshot.active_deck_id ? <Check size={15} /> : <Layers3 size={15} />}{deck.id === snapshot.active_deck_id ? "Active deck" : "Use this deck"}</button></div>
            <div className="library-deck-entries">{deck.entries.map(entry => {
              const card = allCards.find(item => same(item, entry));
              return <div key={`${entry.kind}:${entry.id}`}><CatalogIcon definition={card?.definition} size={20} /><div><strong>{card?.label ?? entry.id}</strong><small>{card?.available ? card.category : "Unavailable · Reference preserved"}</small></div><button className="secondary-button" disabled={library.busy} onClick={() => toggleCard(entry)} aria-label={`Remove ${card?.label ?? entry.id} from deck`}>Remove</button></div>;
            })}</div>
            {!deck.entries.length ? <div className="library-empty"><Layers3 size={28} /><p>This deck is ready for your cards.</p></div> : null}
            <button className="secondary-button" onClick={() => { setTab("cards"); setQuery(""); setFilter(""); setSourceFilter(""); }}>Choose cards from Library <ChevronRight size={15} /></button>
            <footer><form onSubmit={event => { event.preventDefault(); void library.edit({ action: "update_deck", id: deck.id, name: rename.trim() }); }}><input aria-label="Rename deck" maxLength={120} value={rename} onChange={event => setRename(event.target.value)} /><button className="secondary-button" disabled={library.busy || !rename.trim() || rename.trim() === deck.name}>Rename</button></form>
              <button className="library-text-button" disabled={library.busy || snapshot.decks.length < 2} onClick={() => void library.edit({ action: "delete_deck", id: deck.id })}>Delete deck</button></footer>
          </section> : null}
        </> : null}
        {tab === "store" ? <div className="library-store"><Store size={48} /><span className="library-badge">Coming later</span><h3>More worlds of possibility</h3><p>The Pack Store will be a place to discover and acquire new capabilities.</p><p>For now, packs arrive with plugins installed in Open Agent World.</p><button className="secondary-button" onClick={() => { setTab("packs"); setQuery(""); }}>Explore your installed packs</button></div> : null}
      </div>}
  </dialog>;
}
