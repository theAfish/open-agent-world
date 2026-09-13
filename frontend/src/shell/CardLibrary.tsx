import { t, useLocale } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { Archive, Check, ChevronLeft, ChevronRight, Layers3, LibraryBig, Plus, Search, Store, X } from "lucide-react";
import { CatalogIcon } from "../components/CatalogIcon";
import { useCardLibrary, type DeckEntry } from "../state/cardLibrary";
import { useWorldStore } from "../state/worldStore";
import { collectedLibraryCards, displayDeckName, formationSource, libraryCardMatches, libraryCardMetadata, type LibraryCard } from "./libraryCatalog";
import { LibraryPack } from "./LibraryPack";
import { LibraryCard as PhysicalLibraryCard } from "./LibraryCard";
import "./cardLibrary.css";

type Tab = "packs" | "cards" | "decks" | "store";
const PAGE_SIZE = 30;
const same = (a: DeckEntry, b: DeckEntry) => a.kind === b.kind && a.id === b.id;

export function CardLibrary() {
  useLocale();
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
  const sourcePack = sourceFilter.startsWith("pack:") ? snapshot?.packs[sourceFilter.slice(5)] : undefined;
  const sourcePlugin = sourcePack && snapshot?.plugins[sourcePack.definition.plugin_id];
  const newSourceCards = sourcePack?.definition.cards.filter(id => !snapshot?.collection[id]?.source_pack_ids.includes(sourcePack.definition.id)).length ?? 0;
  const deck = snapshot?.decks.find(item => item.id === deckId) ?? snapshot?.decks.find(item => item.id === snapshot.active_deck_id);
  useEffect(() => { setRename(deck?.name ?? ""); }, [deck?.id, deck?.name]);
  const cards = snapshot ? collectedLibraryCards(snapshot) : [];
  const formations: LibraryCard[] = legions.map(item => ({ kind: "legion", id: item.id, label: item.name,
    description: t("{v0} cards · {v1} links", { v0: String(item.node_count), v1: String(item.edge_count) }), category: "Saved Legions", available: item.compatible,
    internal: false, sources: [{ ...formationSource, name: t(formationSource.name), pluginName: t(formationSource.pluginName) }], owners: [] }));
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
  const deckSelect = <label className="library-deck-select">{t("Deck")}<select aria-label={t("Selected deck")} value={deck?.id ?? ""} onChange={event => setDeckId(event.target.value)}>
    {snapshot?.decks.map(item => <option key={item.id} value={item.id}>{displayDeckName(item)}{item.id === snapshot.active_deck_id ? t(" · Active") : ""}</option>)}
  </select></label>;
  const renderCard = (item: LibraryCard) => {
    const included = deck?.entries.some(entry => same(entry, item));
    return <PhysicalLibraryCard key={`${item.kind}:${item.id}`} selected={Boolean(selected && same(selected, item))} color={item.definition?.color ?? "#78967b"}>
      <button className="library-card-inspect" onClick={() => setSelected(item)} aria-label={t("Inspect {v0}", { v0: String(item.label) })}><CatalogIcon definition={item.definition} size={26} /><span>{t(item.category)}</span><strong>{item.label}</strong><small>{item.description}</small>
        {!item.available && !item.internal ? <span className="library-unavailable">{t("Unavailable")}</span> : null}</button>
      {item.internal && !included ? <div className="library-card-usage">{item.owners.length ? t("Use through its container") : t("Created by a world action")}</div> :
        <button className={`library-card-add ${included ? "is-in-deck" : ""}`} disabled={library.busy || !deck || (!included && !item.available)} onClick={() => toggleCard(item)} aria-label={t(included ? 'Remove {v0} from deck' : 'Add {v0} to deck', { v0: item.label })}>
          {included ? <Check size={14} /> : <Plus size={14} />}{included ? t("In deck · Remove") : t("Add to deck")}</button>}
    </PhysicalLibraryCard>;
  };

  return <dialog ref={modal} className="card-library-modal" aria-labelledby="card-library-title" onCancel={event => { event.preventDefault(); library.close(); }}
    onClick={event => { if (event.target === event.currentTarget) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) library.close(); } }}>
    <header className="library-header"><div className="dialog-icon"><LibraryBig size={22} /></div><div><span>{t("Your collection")}</span><h2 id="card-library-title">{t("Pack & Card Library")}</h2></div>
      <button className="top-icon-button" aria-label={t("Close Library")} onClick={library.close}><X size={18} /></button></header>
    <nav className="library-tabs" aria-label={t("Library sections")}>{([
      ["packs", t("Packs"), Archive], ["cards", t("Cards"), LibraryBig], ["decks", t("Decks"), Layers3], ["store", t("Store"), Store],
    ] as const).map(([id, label, Icon]) => <button key={id} className={tab === id ? "is-active" : ""} aria-pressed={tab === id} onClick={() => { setTab(id); setReveal(null); }}>
      <Icon size={16} />{label}<small>{id === "packs" ? Object.keys(snapshot?.packs ?? {}).length : id === "cards" ? allCards.length : id === "decks" ? snapshot?.decks.length ?? 0 : t("Soon")}</small>
    </button>)}</nav>
    <div className="library-flow">{t("Open a pack")} <ChevronRight size={12} /> {t("Collect cards")} <ChevronRight size={12} /> {t("Build a deck")} <ChevronRight size={12} /> {t("Place in your world")}</div>
    {library.error ? <div className="library-error" role="alert">{library.error}<button onClick={() => void library.refresh()}>{t("Refresh")}</button></div> : null}
    {!snapshot ? <div className="library-empty">{library.error ? t("Your collection could not be loaded.") : t("Loading your collection…")}</div> :
      <div className="library-body">
        {tab === "packs" ? <>
          <div className="library-section-heading"><div><h3>{t("Pack inventory")}</h3><p>{t("Open an owned pack to add its contents to your Card Library.")}</p></div>
            <label className="library-search"><Search size={15} /><input aria-label={t("Search packs")} placeholder={t("Search packs")} value={query} onChange={event => setQuery(event.target.value)} /></label></div>
          <div className="pack-grid">{Object.values(snapshot.packs).filter(pack => `${pack.definition.name} ${pack.definition.description} ${t(pack.definition.name)} ${t(pack.definition.description)} ${pack.definition.plugin_id}`.toLowerCase().includes(query.toLowerCase())).map(pack =>
            <LibraryPack key={pack.definition.id} pack={pack} snapshot={snapshot} onOpened={setReveal} onBrowse={browsePack} />
          )}</div>
          {reveal && snapshot.packs[reveal] ? <span className="library-announcement" role="status">{t(snapshot.packs[reveal].definition.name)} {t("opened. Click its empty wrapper to view cards.")}</span> : null}
        </> : null}
        {tab === "cards" ? <>
          <div className="library-section-heading"><div><h3>{t("Card Library")}</h3><p>{allCards.length} {t("collected cards and saved formations · Grouped by source pack")}</p></div>{deckSelect}</div>
          {sourcePack ? <div className="library-source-actions" aria-label={t("Source pack controls")}>
            <span>{t(sourcePack.definition.name)} · {!sourcePlugin?.installed ? t("Plugin uninstalled") : !sourcePlugin.enabled ? t("Plugin disabled") : t("Installed · Enabled")}</span>
            {(!sourcePack.opened || newSourceCards > 0) ? <button className="secondary-button" disabled={library.busy || !sourcePack.owned || !snapshot.available_pack_ids.includes(sourcePack.definition.id)}
              onClick={() => void library.edit({ action: "open_pack", id: sourcePack.definition.id })}>{sourcePack.opened ? t("Collect {v0} new {v1}", { v0: String(newSourceCards), v1: t(newSourceCards === 1 ? "card" : "cards") }) : t("Open pack")}</button> : null}
            {sourcePlugin?.installed && sourcePack.definition.plugin_id !== "open-agent-world.core" ? <button className="library-text-button" disabled={library.busy}
              onClick={() => void library.edit({ action: "set_plugin_enabled", id: sourcePack.definition.plugin_id, enabled: !sourcePlugin.enabled })}>{sourcePlugin.enabled ? t("Disable plugin") : t("Enable plugin")}</button> : null}
          </div> : null}
          <div className="library-tools"><label className="library-search"><Search size={15} /><input aria-label={t("Search cards")} value={query} onChange={event => setQuery(event.target.value)} placeholder={t("Search your cards")} /></label>
            <select aria-label={t("Source pack")} value={sourceFilter} onChange={event => setSourceFilter(event.target.value)}><option value="">{t("All packs")}</option>{sourcePack && !sources.some(source => source.id === sourceFilter) ? <option value={sourceFilter}>{t(sourcePack.definition.name)}</option> : null}{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select>
            <select aria-label={t("Card category")} value={filter} onChange={event => setFilter(event.target.value)}><option value="">{t("All categories")}</option>{categories.map(category => <option key={category} value={category}>{t(category)}</option>)}</select>
            <label className="library-internal-toggle"><input type="checkbox" checked={showInternal} onChange={event => setShowInternal(event.target.checked)} />{t("Show internal cards")}{internalCount ? ` (${internalCount})` : ""}</label>
            <span>{filtered.length} {t("results")}</span></div>
          <div className="library-card-layout"><div>
            {groups.map(({ source, items, count }) => <details key={JSON.stringify([source.id, currentPage, query, filter, sourceFilter, showInternal])} className="library-source-group"
              open={Boolean(sourceFilter || query.trim() || filter || showInternal || sources.length === 1)}>
              <summary><Archive size={18} /><div><h4>{source.name}</h4><small>{t(source.pluginName)}</small></div><span>{count} {t(count === 1 ? "card" : "cards")}</span><ChevronRight className="library-group-chevron" size={16} /></summary>
              {items.some(item => !item.internal) ? <div className="library-card-grid">{items.filter(item => !item.internal).map(renderCard)}</div> : null}
              {items.some(item => item.internal) ? <div className="library-internal-cards"><p>{t("Internal cards · Created through containers or world actions")}</p><div className="library-card-grid">{items.filter(item => item.internal).map(renderCard)}</div></div> : null}
            </details>)}
            {!filtered.length ? <div className="library-empty"><LibraryBig size={30} /><strong>{allCards.length ? t("No matching cards") : t("Your collection starts with a pack")}</strong><p>{!showInternal && internalCount ? t("{v0} matching internal cards are hidden. Show internal cards to inspect their purpose and container.", { v0: String(internalCount) }) : allCards.length ? t("Try another search, source pack or category.") : t("Visit Packs and open one to discover its cards.")}</p><button className="secondary-button" onClick={() => { if (!showInternal && internalCount) setShowInternal(true); else { setTab("packs"); setQuery(""); } }}>{!showInternal && internalCount ? t("Show internal cards") : t("Browse packs")}</button></div> : null}
            {pages > 1 ? <div className="library-pagination"><button aria-label={t("Previous cards")} disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16} /></button><span>{t("Page")} {currentPage + 1} {t("of")} {pages}</span><button aria-label={t("Next cards")} disabled={currentPage >= pages - 1} onClick={() => setPage(currentPage + 1)}><ChevronRight size={16} /></button></div> : null}
          </div><aside className="library-card-detail" aria-label={t("Card details")}>{detail ? <><CatalogIcon definition={detail.definition} size={36} /><span className="library-badge">{t(detail.category)}</span><h3>{detail.label}</h3><p>{detail.description}</p>
            {detail.internal ? <div className="library-usage-detail"><small>{t("How to use")}</small><p>{detail.owners.length ? t("Open {v0} to use this card. It is created inside the container.", { v0: String(detail.owners.map(owner => owner.label).join(" or ")) }) : t("This card is created by a container or world action.")} {t("It cannot be added to a deck on its own.")}</p>
              {detail.owners.map(owner => snapshot.collection[owner.id]?.unlocked ? <button key={owner.id} className="library-text-button" onClick={() => setSelected({ kind: "node", id: owner.id })}>{t("Inspect")} {libraryCardMetadata(snapshot, owner).label} <ChevronRight size={14} /></button> : <p key={owner.id}>{t("Open its source pack to collect")} {owner.label}.</p>)}</div> : null}
            {detail.definition ? <><small>{t("Source plugin")}</small><p>{snapshot.plugins[detail.definition.plugin_id]?.descriptor.name ?? detail.definition.plugin_id}</p><small>{t("Collected from")}</small><p>{detail.sources.map(source => source.name).join(", ")}</p><small>{t("Collected")} {new Date(snapshot.collection[detail.id].unlocked_at).toLocaleDateString(useLocale.getState().locale)}</small></> : <p>{t("A formation you saved from your world. Its members keep their original plugin dependencies.")}</p>}
            {(detail.definition && (!snapshot.plugins[detail.definition.plugin_id]?.installed || !snapshot.plugins[detail.definition.plugin_id]?.enabled)) || (!detail.available && !detail.internal) ? <p className="library-unavailable">{detail.kind === "legion" ? t("This formation has unavailable dependencies.") : t("This content is unavailable. Install or enable its plugin to use it again.")}</p> : null}
            {!detail.internal || deck?.entries.some(entry => same(entry, detail)) ? <button className="secondary-button" disabled={library.busy || !deck || (!detail.available && !deck.entries.some(entry => same(entry, detail)))}
              aria-label={deck?.entries.some(entry => same(entry, detail)) ? t("Remove inspected card from deck") : t("Add inspected card to deck")} onClick={() => toggleCard(detail)}>
              {deck?.entries.some(entry => same(entry, detail)) ? t("Remove from deck") : t("Add to deck")}</button> : null}
            {detail.kind === "legion" ? <button className="library-text-button" onClick={() => { if (window.confirm(t("Remove {v0} from the Legion library?", { v0: String(detail.label) }))) void deleteLegion(detail.id); }}>{t("Delete saved formation")}</button> : null}
          </> : <><Layers3 size={30} /><h3>{t("Explore a card")}</h3><p>{t("Select a card to inspect its purpose and origin.")}</p><p>{t("Removing a card from a deck keeps it in your collection.")}</p></>}</aside></div>
        </> : null}
        {tab === "decks" ? <>
          <div className="library-section-heading"><div><h3>{t("Your decks")}</h3><p>{t("The active deck is the hand shown at the bottom of your world.")}</p></div>{deckSelect}</div>
          <div className="library-deck-actions"><form onSubmit={event => { event.preventDefault(); if (name.trim()) void library.edit({ action: "create_deck", name: name.trim() }).then(saved => { if (saved) { setDeckId(saved.active_deck_id); setName(""); } }); }}>
            <input aria-label={t("New deck name")} placeholder={t("Name a new deck")} maxLength={120} value={name} onChange={event => setName(event.target.value)} /><button className="secondary-button" disabled={library.busy || !name.trim()}><Plus size={14} /> {t("Create deck")}</button></form></div>
          {deck ? <section className="library-deck-editor"><div className="library-section-heading"><div><h3>{displayDeckName(deck)}</h3><p>{deck.entries.length} {t("cards ·")} {deck.id === snapshot.active_deck_id ? t("Active in your world") : t("Ready to activate")}</p></div>
            <button className="primary-button" disabled={library.busy || deck.id === snapshot.active_deck_id} onClick={() => void library.edit({ action: "activate_deck", id: deck.id })}>{deck.id === snapshot.active_deck_id ? <Check size={15} /> : <Layers3 size={15} />}{deck.id === snapshot.active_deck_id ? t("Active deck") : t("Use this deck")}</button></div>
            <div className="library-deck-entries">{deck.entries.map(entry => {
              const card = allCards.find(item => same(item, entry));
              return <div key={`${entry.kind}:${entry.id}`}><CatalogIcon definition={card?.definition} size={20} /><div><strong>{card?.label ?? entry.id}</strong><small>{card?.available ? t(card.category) : t("Unavailable · Reference preserved")}</small></div><button className="secondary-button" disabled={library.busy} onClick={() => toggleCard(entry)} aria-label={t("Remove {v0} from deck", { v0: String(card?.label ?? entry.id) })}>{t("Remove")}</button></div>;
            })}</div>
            {!deck.entries.length ? <div className="library-empty"><Layers3 size={28} /><p>{t("This deck is ready for your cards.")}</p></div> : null}
            <button className="secondary-button" onClick={() => { setTab("cards"); setQuery(""); setFilter(""); setSourceFilter(""); }}>{t("Choose cards from Library")} <ChevronRight size={15} /></button>
            <footer><form onSubmit={event => { event.preventDefault(); void library.edit({ action: "update_deck", id: deck.id, name: rename.trim() }); }}><input aria-label={t("Rename deck")} maxLength={120} value={rename} onChange={event => setRename(event.target.value)} /><button className="secondary-button" disabled={library.busy || !rename.trim() || rename.trim() === deck.name}>{t("Rename")}</button></form>
              <button className="library-text-button" disabled={library.busy || snapshot.decks.length < 2} onClick={() => void library.edit({ action: "delete_deck", id: deck.id })}>{t("Delete deck")}</button></footer>
          </section> : null}
        </> : null}
        {tab === "store" ? <div className="library-store"><Store size={48} /><span className="library-badge">{t("Coming later")}</span><h3>{t("More worlds of possibility")}</h3><p>{t("The Pack Store will be a place to discover and acquire new capabilities.")}</p><p>{t("For now, packs arrive with plugins installed in Open Agent World.")}</p><button className="secondary-button" onClick={() => { setTab("packs"); setQuery(""); }}>{t("Explore your installed packs")}</button></div> : null}
      </div>}
  </dialog>;
}
