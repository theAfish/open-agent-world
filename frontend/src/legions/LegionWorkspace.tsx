import { AppearanceButtons, SettingsButton } from '../shell/PreferenceButtons';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft, Check, GripVertical, LayoutTemplate, Pencil, Plus, Save, X } from 'lucide-react';
import { apiErrorMessage } from '../api/client';
import { CardContent } from '../cards/CardFrame';
import { WorkspaceContent } from '../cards/NodeWorkspace';
import { CatalogIcon } from '../components/CatalogIcon';
import { t, useLocale } from '../i18n';
import { nodeSurfaceSupport } from '../state/nodeSurfaces';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { WorkspaceSectionProvider, type WorkspaceSectionRegistration } from '../workspace/WorkspaceSection';
import { activateTab, activePaneView, dockPane, dropSide, layoutMinimum, paneViews, readWorkspaceLayout, removePane, resizeSplit, retainPanes, stackPane, viewKey,
  type DockSide, type WorkspaceLayout, type WorkspaceLeaf, type WorkspaceNode, type WorkspaceView } from './workspaceLayout';
import './legionWorkspace.css';
import { PublishApplication } from '../deployment/PublishApplication';

const CARD_MIME = 'application/x-oaw-workspace-view';
type RegisteredSection = WorkspaceSectionRegistration & { card_id: string };
const sides: DockSide[] = ['left', 'right', 'top', 'bottom'];
const sideLabels: Record<DockSide, string> = { left: 'Dock left', right: 'Dock right', top: 'Dock above', bottom: 'Dock below' };

export function LegionWorkspace() {
  const activeId = useLegionWorkspace(s => s.activeId);
  const card = useWorldStore(s => s.cards.find(item => item.id === activeId && item.type === 'legion'));
  return card ? <WorkspaceWindow key={card.id} card={card} /> : null;
}

export function WorkspaceWindow({ card, locked = false, actions }: { card: WorldCard; locked?: boolean; actions?: ReactNode }) {
  useLocale();
  const dialog = useRef<HTMLDialogElement>(null);
  const cards = useWorldStore(s => s.cards);
  const catalog = useWorldStore(s => s.catalog);
  const updateCard = useWorldStore(s => s.updateCard);
  const close = useLegionWorkspace(s => s.close);
  const members = useMemo(() => cards.filter(item => item.parent_id === card.id
    && item.type !== 'legion'
    && (!catalog.node_types.find(type => type.id === item.type)?.container
      || nodeSurfaceSupport(item.type, catalog).workspace)), [cards, card.id, catalog]);
  const memberIds = useMemo(() => new Set(members.map(item => item.id)), [members]);
  const sourceKey = JSON.stringify(card.config.workspace_layout ?? null);
  const [baseKey, setBaseKey] = useState(sourceKey);
  const [baseline, setBaseline] = useState(() => readWorkspaceLayout(card.config.workspace_layout));
  const [draft, setDraft] = useState(baseline);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const savingRef = useRef(false);
  const [editing, setEditing] = useState(!locked && !baseline.root);
  const [selected, setSelected] = useState<WorkspaceView | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [closing, setClosing] = useState(false);
  const [saved, setSaved] = useState(false);
  const [drawerId, setDrawerId] = useState<string | null>(null);
  const [sections, setSections] = useState(new Map<string, RegisteredSection>());
  const registerSection = useCallback((cardId: string, section: WorkspaceSectionRegistration) => {
    const key = viewKey({ card_id: cardId, section_id: section.id });
    setSections(current => new Map(current).set(key, { ...section, card_id: cardId }));
    return () => setSections(current => { const next = new Map(current); next.delete(key); return next; });
  }, []);
  const mountedIds = useRef(new Set<string>());
  const dirty = !locked && JSON.stringify(draft) !== JSON.stringify(baseline);
  const root = useMemo(() => retainPanes(draft.root, memberIds), [draft, memberIds]);
  const placedViews = paneViews(root);
  const assigned = new Set(placedViews.map(viewKey));
  const unplaced = members.filter(member => !assigned.has(viewKey({ card_id: member.id })));
  const drawerCard = unplaced.find(member => member.id === drawerId);
  placedViews.forEach(view => mountedIds.current.add(view.card_id));
  if (drawerCard) mountedIds.current.add(drawerCard.id);
  // Stable hosts keep text drafts, terminals and plugin state mounted while a
  // card moves between branches or the user switches between edit and preview.
  const surfaceHosts = useRef(new Map<string, HTMLDivElement>());
  for (const member of members) {
    const key = viewKey({ card_id: member.id });
    if (!surfaceHosts.current.has(key)) {
      const host = document.createElement('div');
      host.className = `legion-pane-content ${nodeSurfaceSupport(member.type, catalog).workspace ? 'has-workspace' : 'has-inspector'}`;
      surfaceHosts.current.set(key, host);
    }
  }
  const hosts = new Map(surfaceHosts.current);
  sections.forEach((section, key) => hosts.set(key, section.host));
  const viewTitle = (view: WorkspaceView) => {
    const name = members.find(member => member.id === view.card_id)?.name ?? view.card_id;
    return view.section_id ? `${name} · ${sections.get(viewKey(view))?.title ?? view.section_id}` : name;
  };
  const minimum = layoutMinimum(root);
  const conflict = sourceKey !== baseKey && dirty && !busy;

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);
  useEffect(() => {
    if (drawerId && !unplaced.some(member => member.id === drawerId)) setDrawerId(null);
    for (const id of mountedIds.current) {
      if (!memberIds.has(id)) { surfaceHosts.current.delete(viewKey({ card_id: id })); mountedIds.current.delete(id); }
    }
  }, [drawerId, root, memberIds]);
  useEffect(() => {
    if (busy || dirty || sourceKey === baseKey) return;
    const layout = readWorkspaceLayout(card.config.workspace_layout);
    setBaseline(layout); setDraft(layout); setBaseKey(sourceKey);
  }, [sourceKey, baseKey, busy, dirty, card.config.workspace_layout]);

  const reset = () => {
    const layout = readWorkspaceLayout(card.config.workspace_layout);
    setBaseline(layout); setDraft(layout); setBaseKey(sourceKey);
    setError(''); setSelected(null); setClosing(false); setSaved(false);
  };
  const changeLayout = (next: WorkspaceLayout) => {
    if (paneViews(next.root).length + next.hidden_sections.length > 100) {
      setError(t('Workspace layouts support at most 100 views.')); return;
    }
    draftRef.current = next;
    setDraft(draftRef.current); setSaved(false); setError('');
  };
  const changeRoot = (next: WorkspaceNode | null) => changeLayout({ ...draftRef.current, root: next });
  const restoreSection = (view: WorkspaceView) => {
    changeLayout({ ...draftRef.current, root: removePane(root, view),
      hidden_sections: draft.hidden_sections.filter(item => viewKey(item) !== viewKey(view)) });
    setSelected(null);
  };
  const remove = (view: WorkspaceView) => {
    changeLayout({ ...draftRef.current, root: removePane(root, view), hidden_sections: view.section_id
      ? [...draft.hidden_sections.filter(item => viewKey(item) !== viewKey(view)), view] : draft.hidden_sections });
    setSelected(null);
  };
  const requestClose = () => { if (locked || busy) return; if (dirty) setClosing(true); else close(); };
  const save = async () => {
    if (locked || conflict || savingRef.current) return;
    savingRef.current = true;
    setBusy(true); setError('');
    try {
      const layout: WorkspaceLayout = { version: 2, root: retainPanes(draftRef.current.root, memberIds),
        hidden_sections: draftRef.current.hidden_sections.filter(view => memberIds.has(view.card_id)) };
      await updateCard(card.id, { config: { workspace_layout: layout } }, card.revision === undefined ? undefined : { expectedRevision: card.revision });
      const current = useWorldStore.getState().cards.find(item => item.id === card.id);
      if (!current || JSON.stringify(readWorkspaceLayout(current.config.workspace_layout)) !== JSON.stringify(layout)) {
        throw new Error(t('Layout could not be saved. Your draft is still here.'));
      }
      setBaseline(layout); setDraft(layout); setBaseKey(JSON.stringify(current.config.workspace_layout));
      setEditing(false); setSelected(null); setSaved(true); setClosing(false);
    } catch (reason) { setError(apiErrorMessage(reason)); }
    finally { savingRef.current = false; setBusy(false); }
  };
  const finishEditing = () => {
    if (dirty) void save();
    else { setEditing(false); setSelected(null); }
  };
  const finishResize = () => {
    if (!editing && JSON.stringify(draftRef.current) !== JSON.stringify(baseline)) void save();
  };
  const place = (view: WorkspaceView, target: WorkspaceView | null, side: DockSide) => {
    if (!editing || !memberIds.has(view.card_id) || busy) return;
    const next = dockPane(root, view, target, side);
    if (next === root && viewKey(view) !== (target && viewKey(target))) { setError(t('This split is too deep. Choose another region.')); return; }
    changeLayout({ ...draftRef.current, root: next, hidden_sections: draft.hidden_sections.filter(item => viewKey(item) !== viewKey(view)) });
    setSelected(null); setDragging(false);
  };
  const stack = (view: WorkspaceView, target: WorkspaceView, before?: WorkspaceView) => {
    if (!editing || busy || !memberIds.has(view.card_id)) return;
    changeLayout({ ...draftRef.current, root: stackPane(root, view, target, before),
      hidden_sections: draft.hidden_sections.filter(item => viewKey(item) !== viewKey(view)) });
    setSelected(null); setDragging(false);
  };
  const activate = (view: WorkspaceView) => {
    if (!root || busy || savingRef.current || conflict) return;
    const next = activateTab(root, view);
    if (JSON.stringify(next) === JSON.stringify(root)) return;
    changeRoot(next);
    if (!locked && !editing) void save();
  };
  const startDrag = (event: DragEvent, view: WorkspaceView) => {
    event.stopPropagation();
    event.dataTransfer.setData(CARD_MIME, viewKey(view)); event.dataTransfer.effectAllowed = 'move';
    setSelected(view); setDragging(true);
  };

  return <dialog ref={dialog} className="legion-workspace" data-legion-workspace={card.id} aria-label={t('{v0} workspace mode', { v0: card.name })}
    onCancel={event => { event.preventDefault(); if (drawerCard) setDrawerId(null); else requestClose(); }} onKeyDown={event => event.stopPropagation()}
    onDragEnd={() => { setDragging(false); setSelected(null); }}>
    <header className="legion-window-titlebar">
      <span className="legion-window-mark"><LayoutTemplate size={16} /></span>
      <div className="legion-window-title"><strong>{card.name}</strong></div>
      <span className="legion-window-status" role="status">{busy ? t('Saving...') : dirty ? t('Unsaved layout') : saved ? t('Layout saved') : ''}</span>
      {actions}
      {!locked && <><PublishApplication card={card} disabled={busy || dirty || editing} />
      <button data-tutorial="legion-edit" className="secondary-button" disabled={busy || (editing && conflict)} onClick={() => { if (editing) finishEditing(); else { setEditing(true); setSelected(null); } }}>
        {editing ? <Check size={13} /> : <Pencil size={13} />}{editing ? t('Done editing') : t('Edit layout')}
      </button>
      {editing && dirty && <button data-tutorial="legion-reset" className="secondary-button" disabled={busy} onClick={reset}>{t('Cancel layout changes')}</button>}
      {editing && <button data-tutorial="legion-save" className="primary-button" disabled={busy || !dirty || conflict} onClick={() => void save()}><Save size={13} />{busy ? t('Saving...') : t('Save layout')}</button>}
      <button data-tutorial="legion-back" className="secondary-button" disabled={busy} onClick={requestClose}><ArrowLeft size={14} />{t('Back to canvas')}</button></>}
    </header>
    {(error || conflict) && <div className="legion-window-error" role="alert">{error || t('This layout changed elsewhere. Reload it before saving.')}
      {conflict ? <button onClick={reset}>{t('Reload layout')}</button> : !editing && <>
        <button disabled={busy} onClick={() => void save()}>{t('Retry')}</button>
        <button disabled={busy} onClick={reset}>{t('Cancel layout changes')}</button>
      </>}
    </div>}
    {closing && <div className="legion-window-close-prompt" role="alert">
      <span>{t('Save your layout or discard the changes before returning to the canvas.')}</span>
      <button className="secondary-button" onClick={() => setClosing(false)}>{t('Keep editing')}</button>
      <button className="secondary-button" onClick={close}>{t('Discard layout and close')}</button>
    </div>}
    <div data-tutorial="legion-layout" className={`legion-window-main ${editing ? 'is-editing' : ''}`}>
      {editing && <aside className="legion-layout-palette" aria-label={t('Workspace cards')}>
        <header><strong>{t('Workspace cards')}</strong><span>{members.length - unplaced.length} / {members.length}</span></header>
        <p>{t('Drop on a title bar to add a tab, or on a region edge to split. You can also select a card and use the docking buttons.')}</p>
        <p>{t('Hover over a section to arrange or hide it. Restore sections from the card list.')}</p>
        <div className="legion-layout-card-list">
          {draft.hidden_sections.some(view => memberIds.has(view.card_id)) && <section className="legion-hidden-sections" aria-label={t('Hidden sections')}>
            <h3>{t('Hidden sections')}</h3>
            <p>{t('Restore hidden sections to their original cards.')}</p>
            {draft.hidden_sections.filter(view => memberIds.has(view.card_id)).map(view => {
              const title = sections.get(viewKey(view))?.title ?? view.section_id ?? '';
              const owner = members.find(member => member.id === view.card_id)!;
              return <div className="legion-hidden-section" key={viewKey(view)}>
                <strong>{title}</strong><small>{owner.name}</small>
                <button className="secondary-button" disabled={busy} aria-label={t('Restore {v0} to card', { v0: title })}
                  onClick={() => {
                    restoreSection(view);
                    if (unplaced.some(member => member.id === view.card_id)) setDrawerId(view.card_id);
                  }}><Plus size={13} />{t('Restore to card')}</button>
              </div>;
            })}
          </section>}
          {members.map(member => {
            const definition = catalog.node_types.find(item => item.id === member.type);
            const view = { card_id: member.id };
            const sectionViews = new Map<string, WorkspaceView>();
            sections.forEach(section => { if (section.card_id === member.id) {
              const item = { card_id: member.id, section_id: section.id }; sectionViews.set(viewKey(item), item);
            } });
            [...placedViews, ...draft.hidden_sections].filter(item => item.card_id === member.id && item.section_id)
              .forEach(item => sectionViews.set(viewKey(item), item));
            return <div key={member.id} className="legion-palette-owner"><button className="legion-layout-card" data-workspace-source={member.id}
              draggable={!busy} disabled={busy} aria-pressed={selected !== null && viewKey(selected) === viewKey(view)}
              onClick={() => setSelected(selected && viewKey(selected) === viewKey(view) ? null : view)}
              onDragStart={event => startDrag(event, view)}>
              <CatalogIcon definition={definition} size={18} />
              <span><strong>{member.name}</strong><small>{definition?.label ?? member.type}</small></span>
              {assigned.has(viewKey(view)) ? <Check size={14} aria-label={t('Placed in workspace')} /> : <GripVertical size={14} />}
            </button>
            {sectionViews.size > 0 && <details className="legion-section-list"><summary>{t('Card sections')}</summary>
              {[...sectionViews.values()].map(item => {
                const key = viewKey(item), section = sections.get(key);
                const hidden = draft.hidden_sections.some(view => viewKey(view) === key);
                const detached = assigned.has(key);
                return <div key={key} className="legion-section-row">
                  <button draggable={!busy && !!section} disabled={busy || !section} aria-pressed={!!selected && viewKey(selected) === key}
                    onDragStart={event => startDrag(event, item)} onClick={() => setSelected(item)}
                    title={viewTitle(item)}><GripVertical size={12} /><span>{section?.title ?? item.section_id}<small>{hidden ? t('Hidden') : detached ? t('In workspace') : t('In card')}</small></span></button>
                  {(hidden || detached) && <button disabled={busy} onClick={() => restoreSection(item)} title={t('Restore to card')}
                    aria-label={t('Restore {v0} to card', { v0: section?.title ?? item.section_id ?? '' })}><ArrowLeft size={13} /></button>}
                </div>;
              })}
            </details>}</div>;
          })}
          {!members.length && <p>{t('Add cards to this Legion on the canvas first.')}</p>}
        </div>
        <footer>{t('Unplaced cards are available in the bottom bar. All members keep their connections and continue working.')}</footer>
      </aside>}
      <main className="legion-layout-stage" aria-label={t('Workspace layout')}>
        <div className="legion-layout-root" style={{ minWidth: minimum.width, minHeight: minimum.height }}>
          {root ? <LayoutRegion node={root} path="" members={members} hosts={hosts} viewTitle={viewTitle} editing={editing && !busy} resizable={!locked && !busy && !conflict} finishResize={finishResize} selected={selected} dragging={dragging}
            stack={stack} activate={activate} selectable={!busy && !conflict}
            place={place} startDrag={startDrag} remove={remove} restore={restoreSection}
            resize={(path, ratio) => changeRoot(resizeSplit(root, path, ratio))} />
            : <div className="legion-layout-empty" onDragOver={event => { if (editing && dragging) event.preventDefault(); }}
              onDrop={event => { event.preventDefault(); if (editing && selected && event.dataTransfer.getData(CARD_MIME) === viewKey(selected)) place(selected, null, 'right'); }}>
              <LayoutTemplate size={44} /><h2>{t('Build your workspace')}</h2>
              <p>{editing ? t('Drop the first card here, then split regions to arrange the rest.') : t('Choose Edit layout to add cards to this window.')}</p>
              {editing && selected && <button className="primary-button" onClick={() => place(selected, null, 'right')}><Plus size={14} />{t('Place selected card')}</button>}
              {!locked && !editing && <button className="primary-button" onClick={() => setEditing(true)}><Pencil size={14} />{t('Edit layout')}</button>}
            </div>}
        </div>
      </main>
      <section id="legion-card-drawer" className="legion-card-drawer" hidden={!drawerCard} aria-label={t('Unplaced card details')}>
        <header className="legion-drawer-titlebar">
          {drawerCard && <><CatalogIcon definition={catalog.node_types.find(item => item.id === drawerCard.type)} size={14} /><strong>{drawerCard.name}</strong></>}
          <button className="legion-tab-close" aria-label={t('Collapse card details')} onClick={() => {
            const id = drawerId; setDrawerId(null);
            if (id) document.getElementById(`legion-tray-${id}`)?.focus();
          }}><X size={14} /></button>
        </header>
        {unplaced.filter(member => mountedIds.current.has(member.id)).map(member =>
          <PaneMount key={member.id} id={member.id} host={surfaceHosts.current.get(viewKey({ card_id: member.id }))} hidden={member.id !== drawerCard?.id} label={member.name} />)}
      </section>
    </div>
    <footer className="legion-window-footer"><span><i />{editing ? t('Layout editor') : t('Live workspace')}</span>
    <div className="legion-workspace-preferences" role="group" aria-label={t('Application preferences')}>{!locked && <SettingsButton />}<AppearanceButtons /></div>
    {!locked && !!unplaced.length && <nav className="legion-unplaced-bar" aria-label={t('Unplaced workspace cards')}>
      {unplaced.map(member => <button key={member.id} id={`legion-tray-${member.id}`} className="legion-unplaced-card"
        aria-label={member.name} title={`${member.name} · ${catalog.node_types.find(item => item.id === member.type)?.label ?? member.type}`}
        aria-expanded={drawerCard?.id === member.id} aria-controls="legion-card-drawer"
        draggable={editing && !busy} onDragStart={event => startDrag(event, { card_id: member.id })}
        onClick={() => setDrawerId(drawerCard?.id === member.id ? null : member.id)}>
        <CatalogIcon definition={catalog.node_types.find(item => item.id === member.type)} size={17} />
      </button>)}
    </nav>}
      <span className="legion-footer-hint">{locked ? t('Published application') : editing ? t('Drag dividers to resize. Removing a pane keeps its card in the Legion.') : t('Drag dividers to resize; sizes save automatically. Use Edit layout to move cards.')}</span>
    </footer>
    {members.filter(member => mountedIds.current.has(member.id)).map(member => <CardWorkspaceOwner key={member.id}
      member={member} host={surfaceHosts.current.get(viewKey({ card_id: member.id }))!} workspace={nodeSurfaceSupport(member.type, catalog).workspace}
      editing={editing && !busy} placedViews={placedViews} hiddenViews={draft.hidden_sections} register={registerSection}
      select={setSelected} startDrag={startDrag} hide={remove} />)}
  </dialog>;
}

interface RegionProps {
  stack: (view: WorkspaceView, target: WorkspaceView, before?: WorkspaceView) => void;
  activate: (view: WorkspaceView) => void; selectable: boolean;
  resizable: boolean; finishResize: () => void;
  hosts: Map<string, HTMLDivElement>;
  node: WorkspaceNode; path: string; members: WorldCard[]; editing: boolean; selected: WorkspaceView | null; dragging: boolean;
  viewTitle: (view: WorkspaceView) => string;
  place: (view: WorkspaceView, target: WorkspaceView, side: DockSide) => void;
  startDrag: (event: DragEvent, view: WorkspaceView) => void;
  remove: (view: WorkspaceView) => void; restore: (view: WorkspaceView) => void; resize: (path: string, ratio: number) => void;
}

function LayoutRegion(props: RegionProps) {
  const { node, path, resizable, resize, finishResize } = props;
  const splitRef = useRef<HTMLDivElement>(null);
  const resizeStart = useRef<{ bounds: DOMRect; ratio: number }>();
  if (node.kind !== 'split') return <WorkspacePane {...props} leaf={node} />;
  const horizontal = node.axis === 'horizontal';
  const firstMinimum = layoutMinimum(node.first), secondMinimum = layoutMinimum(node.second);
  const dimension = horizontal ? 'width' : 'height';
  // Keep each flex factor >= 1: after a track freezes at its minimum,
  // a remaining factor below 1 would leave part of the free space unused.
  const template = `minmax(${firstMinimum[dimension]}px, ${node.ratio * 100}fr) 5px minmax(${secondMinimum[dimension]}px, ${(1 - node.ratio) * 100}fr)`;
  return <div ref={splitRef} className="legion-layout-split" data-split-axis={node.axis}
    style={horizontal ? { gridTemplateColumns: template } : { gridTemplateRows: template }}>
    <LayoutRegion {...props} node={node.first} path={`${path}0`} />
    <div className={`legion-layout-divider ${resizable ? 'is-editable' : ''}`} role="separator"
      tabIndex={resizable ? 0 : -1} aria-disabled={!resizable} aria-label={t('Resize workspace regions')} aria-orientation={horizontal ? 'vertical' : 'horizontal'}
      aria-valuemin={15} aria-valuemax={85} aria-valuenow={Math.round(node.ratio * 100)}
      onPointerDown={event => {
        if (!resizable || event.button !== 0) return;
        event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
        resizeStart.current = { bounds: splitRef.current!.getBoundingClientRect(), ratio: node.ratio };
      }} onPointerMove={event => {
        const start = resizeStart.current;
        if (!start) return;
        const ratio = horizontal ? (event.clientX - start.bounds.left - 2.5) / (start.bounds.width - 5)
          : (event.clientY - start.bounds.top - 2.5) / (start.bounds.height - 5);
        resize(path, ratio);
      }} onPointerUp={event => {
        const started = resizeStart.current;
        resizeStart.current = undefined;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        if (started) finishResize();
      }}
      onPointerCancel={() => { if (resizeStart.current) resize(path, resizeStart.current.ratio); resizeStart.current = undefined; }}
      onLostPointerCapture={() => { const started = resizeStart.current; resizeStart.current = undefined; if (started) finishResize(); }}
      onKeyDown={event => {
        if (!resizable) return;
        const delta = (horizontal ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown']).indexOf(event.key);
        if (delta >= 0) { event.preventDefault(); resize(path, node.ratio + (delta === 0 ? -.025 : .025)); }
        if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); resize(path, event.key === 'Home' ? .15 : .85); }
      }} onKeyUp={event => { if (resizable && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) finishResize(); }} />
    <LayoutRegion {...props} node={node.second} path={`${path}1`} />
  </div>;
}

function WorkspacePane({ leaf, members, hosts, viewTitle, editing, selected, dragging, place, stack, activate, selectable, startDrag, remove, restore }: RegionProps & { leaf: WorkspaceLeaf }) {
  const view = activePaneView(leaf), id = viewKey(view);
  const views = paneViews(leaf);
  const catalog = useWorldStore(s => s.catalog);
  const [hover, setHover] = useState<DockSide | null>(null);
  const [tabHover, setTabHover] = useState<string | null>(null);
  const pane = useRef<HTMLElement>(null);
  const tabs = useRef<HTMLDivElement>(null);
  const canPlace = editing && selected && (viewKey(selected) !== id || views.length > 1);
  const canStack = canPlace;
  const selectedKey = selected && viewKey(selected);
  useEffect(() => { setHover(null); setTabHover(null); }, [selectedKey, dragging]);
  useEffect(() => {
    tabs.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [id]);
  // Portal content keeps its React parent in the card owner. Native capture uses
  // the actual pane under the pointer, including drops over live plugin controls.
  useLayoutEffect(() => {
    if (!canPlace || !dragging) return;
    const element = pane.current!;
    const dropTarget = (event: globalThis.DragEvent) => {
      const target = event.target as HTMLElement;
      const header = target.closest('.legion-pane-titlebar');
      const before = target.closest<HTMLElement>('[data-workspace-tab]')?.dataset.workspaceTab;
      const bounds = element.getBoundingClientRect();
      return { header, before, side: dropSide(event.clientX - bounds.left, event.clientY - bounds.top, bounds.width, bounds.height) };
    };
    const over = (event: globalThis.DragEvent) => {
      if (!event.dataTransfer?.types.includes(CARD_MIME)) return;
      event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = 'move';
      const target = dropTarget(event);
      setHover(target.header ? null : target.side); setTabHover(target.header ? target.before ?? '' : null);
    };
    const drop = (event: globalThis.DragEvent) => {
      if (event.dataTransfer?.getData(CARD_MIME) !== viewKey(selected)) return;
      event.preventDefault(); event.stopPropagation();
      const target = dropTarget(event);
      if (target.header) stack(selected, view, views.find(item => viewKey(item) === target.before));
      else place(selected, view, target.side);
      setHover(null); setTabHover(null);
    };
    const leave = (event: globalThis.DragEvent) => {
      if (!element.contains(event.relatedTarget as Node | null)) { setHover(null); setTabHover(null); }
    };
    element.addEventListener('dragover', over, true);
    element.addEventListener('drop', drop, true);
    element.addEventListener('dragleave', leave, true);
    return () => {
      element.removeEventListener('dragover', over, true);
      element.removeEventListener('drop', drop, true);
      element.removeEventListener('dragleave', leave, true);
    };
  }, [canPlace, dragging, selected, view, views, stack, place]);
  return <section ref={pane} className={`legion-workspace-pane ${editing ? 'is-editing' : ''} ${dragging ? 'is-dragging' : ''}`}
    data-workspace-pane={view.section_id ? id : view.card_id} data-workspace-section-pane={view.section_id} aria-label={viewTitle(view)}>
    <header className={`legion-pane-titlebar ${dragging && tabHover !== null ? 'is-tab-drop' : ''}`}>
      <div ref={tabs} className="legion-pane-tabs" role="tablist" aria-label={t('Region tabs')}>
        {views.map((tabView, index) => {
          const tabId = viewKey(tabView);
          const member = members.find(item => item.id === tabView.card_id)!;
          const tabDefinition = catalog.node_types.find(item => item.id === member.type);
          return <span key={tabId} className={`legion-tab-item ${tabId === id ? 'is-active' : ''} ${dragging && tabHover === tabId ? 'is-drop-before' : ''}`} role="presentation" data-workspace-tab={tabId}>
            <button className="legion-workspace-tab" role="tab" id={`legion-tab-${tabId}`} aria-controls={`legion-panel-${tabId}`}
              aria-selected={tabId === id} aria-disabled={!selectable} tabIndex={tabId === id ? 0 : -1}
              title={viewTitle(tabView)} draggable={editing}
              onDragStart={event => startDrag(event, tabView)} onClick={() => activate(tabView)}
              onKeyDown={event => {
                if (!selectable || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? views.length - 1 : (index + (event.key === 'ArrowLeft' ? -1 : 1) + views.length) % views.length;
                const buttons = tabs.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
                buttons?.[nextIndex]?.focus(); activate(views[nextIndex]);
              }}>
              <CatalogIcon definition={tabDefinition} size={13} /><span>{viewTitle(tabView)}</span>
            </button>
            {editing && tabView.section_id && <button className="legion-tab-close" title={t('Restore to card')}
              aria-label={t('Restore {v0} to card', { v0: viewTitle(tabView) })} onClick={() => restore(tabView)}><ArrowLeft size={12} /></button>}
            {editing && <button className="legion-tab-close" aria-label={t('Remove {v0} from layout', { v0: viewTitle(tabView) })} onClick={() => remove(tabView)}><X size={12} /></button>}
          </span>;
        })}
      </div>
      {canStack && <button className="legion-add-tab" onClick={() => stack(selected, view)} title={t('Add selected card as tab')} aria-label={t('Add selected card as tab')}><Plus size={12} />{t('Add tab')}</button>}
    </header>
    {views.map(tabView => <PaneMount key={viewKey(tabView)} id={viewKey(tabView)} host={hosts.get(viewKey(tabView))} hidden={viewKey(tabView) !== id} />)}
    {canPlace && <div className="legion-dock-targets">{sides.map(side => <button key={side} data-dock-side={side}
      onClick={() => place(selected, view, side)} onMouseEnter={() => { if (!dragging) setHover(side); }} onMouseLeave={() => { if (!dragging) setHover(null); }}
      aria-label={t('{v0}: {v1}', { v0: t(sideLabels[side]), v1: viewTitle(view) })}>{t(sideLabels[side])}</button>)}</div>}
    {canPlace && hover && <div className="legion-dock-preview" data-dock-preview={hover}><Plus size={20} /><strong>{viewTitle(selected)}</strong><span>{t(sideLabels[hover])}</span></div>}
  </section>;
}

/** Each tab keeps its existing portal host and component state while hidden. */
function PaneMount({ id, host, hidden, label }: { id: string; host?: HTMLDivElement; hidden: boolean; label?: string }) {
  const mount = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!host) return;
    const target = mount.current!;
    target.appendChild(host);
    return () => { if (host.parentNode === target) target.removeChild(host); };
  }, [host]);
  return <div ref={mount} className="legion-pane-mount" role={label ? 'region' : 'tabpanel'} id={`legion-panel-${id}`} aria-label={label} aria-labelledby={label ? undefined : `legion-tab-${id}`} hidden={hidden}>
    {!host && <p className="legion-section-unavailable">{t('This section is currently unavailable. Its layout is preserved.')}</p>}
  </div>;
}

/** One owner tree drives both inline and detached sections, including when only a section is placed. */
function CardWorkspaceOwner({ member, host, workspace, editing, placedViews, hiddenViews, register, select, startDrag, hide }: {
  member: WorldCard; host: HTMLDivElement; workspace: boolean; editing: boolean;
  placedViews: WorkspaceView[]; hiddenViews: WorkspaceView[];
  register: (cardId: string, section: WorkspaceSectionRegistration) => () => void;
  select: (view: WorkspaceView) => void; startDrag: (event: DragEvent, view: WorkspaceView) => void; hide: (view: WorkspaceView) => void;
}) {
  const registerOwned = useCallback((section: WorkspaceSectionRegistration) => register(member.id, section), [member.id, register]);
  const sectionView = (id: string) => ({ card_id: member.id, section_id: id });
  return createPortal(<WorkspaceSectionProvider cardId={member.id} editing={editing}
    detachedSectionIds={new Set(placedViews.filter(view => view.card_id === member.id && view.section_id).map(view => view.section_id!))}
    hiddenSectionIds={new Set(hiddenViews.filter(view => view.card_id === member.id).map(view => view.section_id!))}
    register={registerOwned} onSelect={id => select(sectionView(id))} onDragStart={(event, id) => startDrag(event, sectionView(id))}
    onHide={id => hide(sectionView(id))}>
    {workspace ? <WorkspaceContent card={member} /> : <CardContent card={member} level="inspector" />}
  </WorkspaceSectionProvider>, host);
}
