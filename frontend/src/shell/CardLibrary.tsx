import { reportInteraction } from '../state/interactions';
import { useMotionPresence } from './useMotionPresence';
import { finishLabel } from "../cards/cardFinish";
import { t, useLocale } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { OverlayScrollbarsComponent, type OverlayScrollbarsComponentRef } from 'overlayscrollbars-react';
import type { PartialOptions } from 'overlayscrollbars';
import 'overlayscrollbars/overlayscrollbars.css';
import '../components/scrollbars.css';
import { Archive, CalendarDays, Check, ChevronRight, Layers3, LibraryBig, Minus, Plus, Search, Store, Tag, X } from "lucide-react";
import { useCardLibrary, type DeckEntry } from "../state/cardLibrary";
import { useWorldStore } from "../state/worldStore";
import { collectionDependencies, ensureCardsCollected } from "../state/cardDependencies";
import { collectedLibraryCards, collectedLibraryLegions, compareLibraryCards, libraryCardMatches, libraryCardMetadata, type LibraryCard } from "./libraryCatalog";
import { LibraryPack } from "./LibraryPack";
import { PackInstaller } from "./PackInstaller";
import { InstalledPackDetails } from "./InstalledPackDetails";
import { installedPackStatus, packInventory } from "./installedPacks";
import { usePackInstallations } from "../state/packInstallations";
import type { PackInstallations } from "../types/packs";
import { PackStore } from "./PackStore";
import { PackCreator } from "./PackCreator";
import { PackOperations } from './PackOperations';
import { LibraryCard as PhysicalLibraryCard, LibraryCardSurface } from "./LibraryCard";
import { LibraryCardGroups } from './LibraryCardGroups';
import "./cardLibrary.css";
import { startPalettePointerDrag } from "../palette/pointerDrag";
import { isHosted } from '../auth/hostedSession';
import { Modal } from '../components/Modal';
import { PackIDE } from '../factory/PackIDE';

type Tab = "packs" | "cards" | "store";
const same = (a: DeckEntry, b: DeckEntry) => a.kind === b.kind && a.id === b.id;
const scrollOptions: PartialOptions = {
  overflow: { x: 'hidden', y: 'scroll' },
  scrollbars: { theme: 'os-theme-oaw', autoHide: 'leave', autoHideDelay: 700, clickScroll: 'instant' },
};

export function CardLibrary() {
  const { locale } = useLocale();
  const library = useCardLibrary();
  const hosted = isHosted();
  const presence = useMotionPresence(library.open);
  const legions = useWorldStore(state => state.legions);
  const deleteLegion = useWorldStore(state => state.deleteLegion);
  const modal = useRef<HTMLDialogElement>(null);
  const tab = hosted && library.tab === 'store' ? 'packs' : library.tab;
  const setTab = (tab: Tab) => useCardLibrary.setState({ tab });
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [showInternal, setShowInternal] = useState(false);
  const [selected, setSelected] = useState<DeckEntry | null>(null);
  const detailSidebar = useRef<OverlayScrollbarsComponentRef>(null);
  useEffect(() => {
    if (library.open && selected?.kind === 'node') reportInteraction({ type: 'card-inspected', cardType: selected.id });
  }, [library.open, selected]);
  useEffect(() => {
    const viewport = detailSidebar.current?.osInstance()?.elements().viewport;
    if (viewport) viewport.scrollTop = 0;
  }, [selected?.kind, selected?.id]);
  const [installAction, setInstallAction] = useState<HTMLDivElement | null>(null);
  const [inspectedPack, setInspectedPack] = useState<string | null>(null);
  const [packNotice, setPackNotice] = useState('');
  const [showPackIDE, setShowPackIDE] = useState(false);
  const [projectBusy, setProjectBusy] = useState(false);
  const [projectDirty, setProjectDirty] = useState(false);
  const installations = usePackInstallations(library.open && !hosted);
  const acceptInstallations = (value: PackInstallations) => {
    const removed = installations.state?.versions.some(previous => previous.selected
      && !value.versions.some(item => item.id === previous.id && item.selected))
      || installations.state?.inventory?.some(previous => previous.enrolled !== false
        && value.inventory?.some(item => item.id === previous.id && item.enrolled === false));
    setPackNotice(removed ? value.restart_required ? 'Pack removed. Restart OAW to finish uninstalling.' : 'Pack uninstalled.' : '');
    if (removed) setInspectedPack(null);
    installations.accept(value);
    void library.refresh();
  };
  useEffect(() => {
    if (library.inspectedEntry) {
      setSelected(library.inspectedEntry);
      setQuery(""); setFilter(""); setSourceFilter("");
    }
  }, [library.inspectedEntry]);
  const cancelPointer = useRef<() => void>();
  const suppressClick = useRef(false);
  const [reveal, setReveal] = useState<string | null>(null);
  useEffect(() => {
    if (!library.open) return;
    const previous = document.activeElement as HTMLElement | null;
    const canvas = document.querySelector<HTMLElement>('.world-canvas');
    const wasInert = canvas?.inert ?? false;
    if (canvas) canvas.inert = true;
    modal.current?.querySelector<HTMLElement>('button')?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape' && !document.body.classList.contains('is-palette-dragging')) {
        event.preventDefault(); library.close();
      }
      if (event.key === 'Tab') {
        const surfaces = [modal.current, document.querySelector('.component-palette.is-library-open'), document.querySelector('.tutorial-guide')];
        const controls = surfaces.flatMap(surface => [...(surface?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, a[href], [tabindex="0"]') ?? [])])
          .filter(element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
        const index = controls.indexOf(document.activeElement as HTMLElement);
        if (controls.length) {
          event.preventDefault();
          const next = index < 0 ? (event.shiftKey ? controls.length - 1 : 0) : (index + (event.shiftKey ? controls.length - 1 : 1)) % controls.length;
          controls[next].focus();
        }
      }
    };
    window.addEventListener('keydown', key);
    return () => { cancelPointer.current?.(); window.removeEventListener('keydown', key); if (canvas) canvas.inert = wasInert; previous?.focus(); };
  }, [library.open]);
  const snapshot = library.snapshot;
  const inventory = snapshot ? packInventory(snapshot, installations.state) : [];
  const currentPacks = inventory.filter(item => item.inventory?.enrolled !== false);
  const uninstalledSources = inventory.filter(item => item.inventory?.enrolled === false);
  const packDetail = inventory.find(item => item.pack.definition.id === inspectedPack);
  const sourcePack = sourceFilter.startsWith("pack:") ? snapshot?.packs[sourceFilter.slice(5)] : undefined;
  const sourceIssues = inventory.find(item => item.pack.definition.id === sourcePack?.definition.id)?.failures ?? [];
  const sourcePlugin = sourcePack && snapshot?.plugins[sourcePack.definition.plugin_id];
  const newSourceCards = sourcePack?.definition.cards.filter(id => !snapshot?.collection[id]?.source_pack_ids.includes(sourcePack.definition.id)).length ?? 0;
  const deck = snapshot?.decks.find(item => item.id === snapshot.active_deck_id);
  const cards = snapshot ? collectedLibraryCards(snapshot) : [];
  const formations = snapshot ? collectedLibraryLegions(snapshot, legions) : [];
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
    || compareLibraryCards(a, b));
  // Multi-pack cards appear once in All packs, and remain discoverable through every source filter.
  const groups = sources.flatMap(source => {
    const items = filtered.filter(item => sourceFor(item).id === source.id);
    return items.length ? [{ source, items }] : [];
  });
  const detail = selected && allCards.find(item => same(item, selected));
  const detailLegion = detail?.kind === "legion" ? legions.find(item => item.id === detail.id) : undefined;
  const detailInDeck = Boolean(detail && deck?.entries.some(entry => same(entry, detail)));
  const collectedAt = snapshot && detail?.definition ? snapshot.collection[detail.id].unlocked_at : undefined;
  const dependencyIds = detailLegion ? detailLegion.required_card_ids ?? detailLegion.node_types : [];
  const dependencies = snapshot && collectionDependencies(snapshot, dependencyIds);
  const browsePack = (id: string) => { setTab("cards"); setQuery(""); setFilter(""); setSourceFilter(`pack:${id}`); setReveal(null); };
  const toggleCard = (entry: DeckEntry) => {
    if (!deck) return;
    const entries = deck.entries.some(item => same(item, entry)) ? deck.entries.filter(item => !same(item, entry)) : [...deck.entries, { kind: entry.kind, id: entry.id }];
    void library.edit(deck.entries.some(item => same(item, entry))
      ? { action: "update_deck", id: deck.id, entries }
      : { action: "move_entry", id: deck.id, entry: { kind: entry.kind, id: entry.id } });
  };
  const renderCard = (item: LibraryCard) => {
    const included = deck?.entries.some(entry => same(entry, item));
    const deckAction = t(included ? 'Remove {v0} from deck' : 'Add {v0} to deck', { v0: item.label });
    return <PhysicalLibraryCard key={`${item.kind}:${item.id}`} selected={Boolean(selected && same(selected, item))} included={Boolean(included)} color={item.definition?.color ?? "#78967b"}>
      <button className="library-card-inspect" draggable={false} data-legion-preview={item.kind === "legion" ? item.id : undefined} data-can-drag={item.available && !item.internal && !library.busy}
        onPointerDown={event => {
          if (!item.available || item.internal || library.busy || event.button !== 0 || !event.isPrimary) return;
          suppressClick.current = false;
          cancelPointer.current = startPalettePointerDrag(event.nativeEvent, event.currentTarget, { entry: { kind: item.kind, id: item.id } }, {
            start: () => { suppressClick.current = true; }, end: () => {},
          });
        }} data-library-card={item.id} onClick={event => {
          if (suppressClick.current && event.detail !== 0) { suppressClick.current = false; return; }
          setSelected(item);
        }} aria-label={t("Inspect {v0}", { v0: String(item.label) })} aria-description={item.description} title={item.description}><LibraryCardSurface card={item} data-deck-visual className="library-card-stock">
        {!item.available && !item.internal ? <span className="library-unavailable">{t("Unavailable")}</span> : null}</LibraryCardSurface></button>
      {item.internal && !included ? <div className="library-card-usage">{item.owners.length ? t("Use through its container") : t("Created by a world action")}</div> :
        <button className={`library-card-add ${included ? "is-in-deck" : ""}`} disabled={library.busy || !deck || (!included && !item.available)} onClick={() => toggleCard(item)} aria-label={deckAction} title={deckAction}>
          {included ? <Check size={16} aria-hidden="true" /> : <Plus size={16} aria-hidden="true" />}</button>}
    </PhysicalLibraryCard>;
  };

  return <><div className="library-backdrop" hidden={!presence.present} data-motion={presence.closing ? 'closing' : 'open'} onClick={library.close} aria-hidden="true" />
    <dialog ref={modal} open={presence.present} data-motion={presence.closing ? 'closing' : 'open'} aria-hidden={!library.open} {...(!library.open ? { inert: '' } : {})} className="card-library-modal" aria-labelledby="card-library-title">
    <header className="library-header"><div className="dialog-icon"><LibraryBig size={20} /></div><h2 id="card-library-title">{t("Pack & Card Library")}</h2>
      <button className="top-icon-button" aria-label={t("Close Library")} onClick={library.close}><X size={18} /></button></header>
    <div className="library-tab-row"><nav className="library-tabs" aria-label={t("Library sections")}>{([
      ["packs", t("Packs"), Archive], ["cards", t("Cards"), LibraryBig], ["store", t("Store"), Store],
    ] as const).filter(([id]) => !hosted || id !== 'store').map(([id, label, Icon]) => <button key={id} data-tutorial={`library-tab-${id}`} className={tab === id ? "is-active" : ""} aria-pressed={tab === id} onClick={() => { setTab(id); setReveal(null); setInspectedPack(null); }}>
      <Icon size={16} />{label}{id !== "store" && <small>{id === "packs" ? currentPacks.length : allCards.length}</small>}
    </button>)}</nav><div className="library-tab-actions" ref={setInstallAction} /></div>
    {library.error ? <div className="library-error" role="alert">{library.error}<button onClick={() => void library.refresh()}>{t("Refresh")}</button></div> : null}
    {!snapshot ? <div className="library-empty">{library.error ? t("Your collection could not be loaded.") : t("Loading your collection…")}</div> :
      <div className={`library-body${tab === "cards" ? " library-body--cards" : ""}`}>
        {tab === "packs" ? <>
          {library.open && !hosted ? <PackInstaller actionTarget={installAction} installations={installations.state} onInstalled={value => { acceptInstallations(value); setInspectedPack(null); setQuery(''); }} /> : null}
          <PackOperations operations={installations.state?.operations?.filter(operation => (!packDetail && operation.state !== 'failed') || operation.pack_id === packDetail?.inventory?.id || packDetail?.versions.some(item => item.id === operation.pack_id))} />
          {installations.error && <p className="pack-action-error" role="alert">{installations.error}</p>}
          {packNotice && <p className="pack-restart-note" role="status">{t(packNotice)}</p>}
          {packDetail ? <InstalledPackDetails key={packDetail.pack.definition.id} {...packDetail} snapshot={snapshot}
            onBack={() => setInspectedPack(null)} onBrowse={browsePack} onChanged={acceptInstallations} /> : <>
          <div className="library-section-heading"><div><h3>{t("Pack inventory")}</h3><p>{t("Open an owned pack to add its contents to your Card Library.")}</p></div>
            <label className="library-search"><Search size={15} /><input aria-label={t("Search packs")} placeholder={t("Search packs")} value={query} onChange={event => setQuery(event.target.value)} /></label></div>
          <div className="pack-grid pack-catalog-grid">{currentPacks.filter(({ pack }) => `${pack.definition.name} ${pack.definition.description} ${t(pack.definition.name)} ${t(pack.definition.description)} ${pack.definition.plugin_id}`.toLowerCase().includes(query.toLowerCase())).map(({ pack, versions, inventory: runtime, registered, failures }) =>
            <LibraryPack key={pack.definition.id} pack={pack} snapshot={snapshot} onOpened={setReveal} onBrowse={browsePack}
              onInspect={(versions.length || runtime || failures.length || (!hosted && installations.state && !snapshot.plugins[pack.definition.plugin_id]?.installed)) ? () => setInspectedPack(pack.definition.id) : undefined}
              failed={failures.length > 0 || runtime?.environments.some(environment => environment.state === 'failed')}
              versions={versions} version={runtime?.version} status={versions.length ? installedPackStatus(versions) : runtime?.enrolled === false ? runtime.loaded ? 'Uninstall pending restart' : 'Pack uninstalled' : undefined} contentCountKnown={registered} />
          )}
          </div>
          {uninstalledSources.length > 0 && <details className="pack-detail-section">
            <summary>{t('Uninstalled Packs')} ({uninstalledSources.length})</summary>
            {uninstalledSources.map(({ pack }) => <button key={pack.definition.id} className="library-text-button" onClick={() => setInspectedPack(pack.definition.id)}>{t('View pack {name}', { name: t(pack.definition.name) })}</button>)}
          </details>}
          {installations.state?.failures?.filter(failure => !inventory.some(item => item.failures.includes(failure))).map((failure, index) =>
            <details key={`failure:${index}`} className="pack-action-error" aria-label={failure.id}>
              <summary>{failure.id} · {t('Unavailable')}</summary>
              <p>{failure.error}</p>
              <p>{t('Repair or reinstall this Pack, then restart OAW to retry. Saved cards and connections are retained.')}</p>
            </details>)}
          {reveal && snapshot.packs[reveal] ? <span className="library-announcement" role="status">{t(snapshot.packs[reveal].definition.name)} {t("opened. Click its empty wrapper to view cards.")}</span> : null}
          </>}
        </> : null}
        {tab === "cards" ? <><div className={`library-card-layout${detail ? " has-card-detail" : ""}`}><OverlayScrollbarsComponent className="library-card-collection" data-tutorial="library-cards" options={scrollOptions} defer>
          <div className="library-tools"><label className="library-search"><Search size={15} /><input aria-label={t("Search cards")} value={query} onChange={event => setQuery(event.target.value)} placeholder={t("Search your cards")} /></label>
            <select aria-label={t("Source pack")} value={sourceFilter} onChange={event => setSourceFilter(event.target.value)}><option value="">{t("All packs")}</option>{sourcePack && !sources.some(source => source.id === sourceFilter) ? <option value={sourceFilter}>{t(sourcePack.definition.name)}</option> : null}{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select>
            <select aria-label={t("Card category")} value={filter} onChange={event => setFilter(event.target.value)}><option value="">{t("All categories")}</option>{categories.map(category => <option key={category} value={category}>{t(category)}</option>)}</select>
            <label className="library-internal-toggle"><input type="checkbox" checked={showInternal} onChange={event => setShowInternal(event.target.checked)} />{t("Show internal cards")}{internalCount ? ` (${internalCount})` : ""}</label>
            <span>{filtered.length} {t("results")}</span></div>
          {sourcePack ? <div className="library-source-actions" aria-label={t("Source pack controls")}>
            <span>{t(sourcePack.definition.name)} · {!sourcePlugin?.installed ? t("Pack uninstalled") : !sourcePlugin.enabled ? t("Pack disabled") : t("Installed · Enabled")}</span>
            {(installations.state?.versions.some(item => (item.owner ?? item.id) === sourcePack.definition.plugin_id)
              || installations.state?.inventory?.some(item => item.owner === sourcePack.definition.plugin_id)
              || (!hosted && installations.state && !sourcePlugin?.installed && !sourcePack.dismissed)) && <button className="library-text-button"
              onClick={() => { setInspectedPack(sourcePack.definition.id); setTab('packs'); }}>{t('Pack details')}</button>}
            {(!sourcePack.opened || newSourceCards > 0) ? <button className="secondary-button" disabled={library.busy || !sourcePack.owned || !snapshot.available_pack_ids.includes(sourcePack.definition.id)}
              onClick={() => void library.edit({ action: "open_pack", id: sourcePack.definition.id })}>{sourcePack.opened ? t("Collect {v0} new {v1}", { v0: String(newSourceCards), v1: t(newSourceCards === 1 ? "card" : "cards") }) : t("Open pack")}</button> : null}
            {!hosted && sourcePlugin?.installed && sourcePack.definition.plugin_id !== "open-agent-world.core" ? <button className="library-text-button" disabled={library.busy}
              onClick={() => void library.edit({ action: "set_plugin_enabled", id: sourcePack.definition.plugin_id, enabled: !sourcePlugin.enabled })}>{sourcePlugin.enabled ? t("Disable Pack") : t("Enable Pack")}</button> : null}
          </div> : null}
          {sourceIssues.map((failure, index) => <p key={index} className="pack-action-error" role="alert">{failure.error}</p>)}
            {groups.length > 0 && <LibraryCardGroups key={JSON.stringify([query, filter, sourceFilter, showInternal])} groups={groups} renderCard={renderCard} />}
            {!filtered.length ? <div className="library-empty"><LibraryBig size={30} /><strong>{allCards.length ? t("No matching cards") : t("Your collection starts with a pack")}</strong><p>{!showInternal && internalCount ? t("{v0} matching internal cards are hidden. Show internal cards to inspect their purpose and container.", { v0: String(internalCount) }) : allCards.length ? t("Try another search, source pack or category.") : t("Visit Packs and open one to discover its cards.")}</p><button className="secondary-button" onClick={() => { if (!showInternal && internalCount) setShowInternal(true); else { setTab("packs"); setQuery(""); } }}>{!showInternal && internalCount ? t("Show internal cards") : t("Browse packs")}</button></div> : null}
          </OverlayScrollbarsComponent><OverlayScrollbarsComponent ref={detailSidebar} className="library-card-sidebar" options={scrollOptions} defer><aside className="library-card-detail" aria-label={t("Card details")}>{detail ? <>
            <div className="library-detail-stage"><LibraryCardSurface key={`${detail.kind}:${detail.id}`} card={detail} className="library-card-preview" size="standard" quality="showcase" /></div>
            <div className="library-detail-content">
            <header className="library-detail-heading">
              <div className="library-detail-tags"><span className="library-detail-category"><Tag size={11} aria-hidden="true" />{t(detail.category)}</span>
                {detail.kind === "node" && <span className="library-finish-metadata" data-finish={detail.finish} title={`${t("Finish")}: ${t(finishLabel(detail.finish))}`}><i aria-hidden="true" />{t(finishLabel(detail.finish))}</span>}</div>
              <h3>{detail.label}</h3><p>{detail.description}</p>
            </header>
            {detailLegion ? <section className="library-dependencies" aria-label={t("Dependency details")}>
              <h4>{t("Dependency details")}</h4>
              {detailLegion.issues.length ? <ul className="library-unavailable">{detailLegion.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul> : null}
              <ul>{dependencyIds.map(id => <li key={id}>{t(snapshot.card_definitions[id]?.label ?? id)} <code>{id}</code>{!snapshot.collection[id]?.unlocked ? ` — ${t("Not collected")}` : ""}</li>)}</ul>
              {dependencies?.packs.length ? <><p>{t("Required packs")}: {dependencies.packs.map(pack => t(pack.definition.name)).join(", ")}</p>
                <button className="secondary-button" disabled={library.busy || !detailLegion.compatible || Boolean(dependencies.blocked.length)} onClick={() => void ensureCardsCollected(dependencyIds)}>{t("Open required packs…")}</button></> : null}
              {dependencies?.blocked.length ? <p className="library-unavailable">{t("Required cards have no available owned pack: {cards}", { cards: dependencies.blocked.join(", ") })}</p> : null}
            </section> : null}
            {detail.internal ? <div className="library-usage-detail"><small>{t("How to use")}</small><p>{detail.owners.length ? t("Open {v0} to use this card. It is created inside the container.", { v0: String(detail.owners.map(owner => owner.label).join(" or ")) }) : t("This card is created by a container or world action.")} {t("It cannot be added to a deck on its own.")}</p>
              {detail.owners.map(owner => snapshot.collection[owner.id]?.unlocked ? <button key={owner.id} className="library-text-button" onClick={() => setSelected({ kind: "node", id: owner.id })}>{t("Inspect")} {libraryCardMetadata(snapshot, owner).label} <ChevronRight size={14} /></button> : <p key={owner.id}>{t("Open its source pack to collect")} {owner.label}.</p>)}</div> : null}
            {detail.definition ? <dl className="library-detail-origin">
              <div><dt><Archive size={12} aria-hidden="true" />{t("Source Pack")}</dt><dd>{snapshot.plugins[detail.definition.plugin_id]?.descriptor.name ?? detail.definition.plugin_id}</dd></div>
              <div><dt>{t("Collected from")}</dt><dd><ul className="library-detail-source-tags">{detail.sources.map(source => <li key={source.id}>{source.name}</li>)}</ul></dd></div>
            </dl> : <p>{t(legions.find(item => item.id === detail.id)?.preset
              ? "A Pack preset. Deploy it, customize its members and workspace, then save your own copy."
              : "A formation you saved from your world. Its members keep their original Pack dependencies.")}</p>}
            {detailLegion?.preset ? <dl className="library-detail-origin"><div><dt>{t("Collected from")}</dt><dd><ul className="library-detail-source-tags">{detail.sources.map(source => <li key={source.id}>{source.name}</li>)}</ul></dd></div></dl> : null}
            {collectedAt && <div className="library-detail-collected"><CalendarDays size={13} aria-hidden="true" /><span>{t("Collected")}</span>
              <time dateTime={collectedAt} title={new Date(collectedAt).toLocaleString(locale)}>{new Date(collectedAt).toLocaleDateString(locale, { year: "numeric", month: "short", day: "numeric" })}</time></div>}
            {detailLegion && !detailLegion.preset ? <PackCreator key={detailLegion.id} legion={detailLegion} /> : null}
            {(detail.definition && (!snapshot.plugins[detail.definition.plugin_id]?.installed || !snapshot.plugins[detail.definition.plugin_id]?.enabled)) || (!detail.available && !detail.internal) ? <p className="library-unavailable">{detail.kind === "legion" ? t("This formation has unavailable dependencies.") : t("This content is unavailable. Install or enable its Pack to use it again.")}</p> : null}
            </div>
            {(!detail.internal || detailInDeck || (detailLegion && !detailLegion.preset)) && <footer className="library-detail-actions">
            {!detail.internal || detailInDeck ? <button className={`${detailInDeck ? "secondary" : "primary"}-button library-detail-deck-action`} disabled={library.busy || !deck || (!detail.available && !detailInDeck)}
              aria-label={detailInDeck ? t("Remove inspected card from deck") : t("Add inspected card to deck")} onClick={() => toggleCard(detail)}>
              {detailInDeck ? <Minus size={15} aria-hidden="true" /> : <Plus size={15} aria-hidden="true" />}{detailInDeck ? t("Remove from deck") : t("Add to current deck")}</button> : null}
            {detail.kind === "legion" && !legions.find(item => item.id === detail.id)?.preset ? <button className="library-text-button" onClick={() => { if (window.confirm(t("Remove {v0} from the Legion library?", { v0: String(detail.label) }))) void deleteLegion(detail.id); }}>{t("Delete saved formation")}</button> : null}
            </footer>}
          </> : <div className="library-detail-empty"><span className="library-detail-formation"><Layers3 size={30} aria-hidden="true" /></span><h3>{t("Explore a card")}</h3><p>{t("Select a card to inspect its purpose and origin.")}</p><p>{t("Removing a card from a deck keeps it in your collection.")}</p></div>}</aside></OverlayScrollbarsComponent></div>
        </> : null}
        {tab === "store" && library.open ? <PackStore installations={installations.state} /> : null}
      </div>}
    {!hosted && <details className="library-developer-tools"><summary>{t('Developer tools')}</summary>
      <button className="library-text-button" onClick={() => setShowPackIDE(true)}>Pack IDE <span className="pack-ide-experimental" title={t('Visual authoring is still in development.')}>{t('Experimental')}</span></button>
    </details>}
  </dialog>{showPackIDE && <Modal title="Pack IDE" className="pack-ide-modal" portalHost={document.body} busy={projectBusy} onClose={() => {
    if (!projectDirty || window.confirm('Discard your unsaved project edits?')) setShowPackIDE(false);
  }}><PackIDE onDirtyChange={setProjectDirty} onBusyChange={setProjectBusy} onClose={() => {
    if (!projectDirty || window.confirm('Discard your unsaved project edits?')) setShowPackIDE(false);
  }} /></Modal>}</>;
}
