import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight, ChevronDown, GitBranch, Plus, Save, Trash2, X } from 'lucide-react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t, useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import type { MachineDocument, MachineMember, MachinePresentation, MachineRuntime, PreviewEvent, PreviewResult, StateMachineEventDescriptor } from './apiTypes';
import { useStateMachineEditor } from './editorStore';
import { machineFromDocument, splitMachine, projectView, ruleForOwner } from './documents';
import { MachineGraph, type Selection, type GraphAnchor } from './MachineGraph';
import { nextStatePosition } from './graphLayout';
import { connectStates, createEntity, makeId, removeEntity, removeState, stateMachineEditorAvailable, validateMachine, type MachineEntity, type StateMachine, type TransitionRule } from './model';
import { createProgram } from './expressions';
import { RuntimeHistory } from './RuntimeHistory';
import { EventPicker } from './EventPicker';
import { TriggerProgramEditor } from './TriggerProgramEditor';
import { ActionEditor } from './ActionEditor';
import { CommandEditor } from './CommandEditor';
import './stateMachine.css';

type StateAddress = { entityId: string; stateId: string };
type Entry = { machine: StateMachine; document: MachineDocument; presentation: MachinePresentation; base: string; baseDefinition: string };
const contentKey = (entry: Pick<Entry, 'machine' | 'presentation'>) => JSON.stringify(splitMachine(entry.machine, entry.presentation));
const definitionKey = (machine: StateMachine) => JSON.stringify(splitMachine(machine).definition);

export function StateMachineEditor() {
  const activeId = useStateMachineEditor(s => s.activeId);
  const card = useWorldStore(s => s.cards.find(item => item.id === activeId));
  const catalog = useWorldStore(s => s.catalog);
  return card && stateMachineEditorAvailable(card, catalog) ? <EditorWindow key={card.id} card={card} /> : null;
}

function EditorWindow({ card }: { card: WorldCard }) {
  useLocale();
  const cards = useWorldStore(s => s.cards), catalog = useWorldStore(s => s.catalog);
  const close = useStateMachineEditor(s => s.close);
  const dialog = useRef<HTMLDialogElement>(null), board = useRef<HTMLDivElement>(null);
  const [dialogReady, setDialogReady] = useState(false);
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const entriesRef = useRef(entries); entriesRef.current = entries;
  const loading = useRef(new Map<string, Promise<Entry>>());
  const [path, setPath] = useState<string[]>([card.id]);
  const activeId = path.at(-1)!;
  const entry = entries[activeId];
  const activeCard = cards.find(item => item.id === activeId);
  const editable = !!activeCard && stateMachineEditorAvailable(activeCard, catalog);
  const [members, setMembers] = useState<Record<string, MachineMember[]>>({});
  const [expanded, setExpanded] = useState<Record<string, string[]>>({});
  const [groups, setGroups] = useState<Record<string, string>>({});
  const [picker, setPicker] = useState<'legion' | 'member' | 'group'>();
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState<Selection>({ entityId: card.id });
  const [anchor, setAnchor] = useState<GraphAnchor>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [closing, setClosing] = useState(false);
  const [conditionValid, setConditionValid] = useState(true), [actionValid, setActionValid] = useState(true);
  const programValid = conditionValid && actionValid;
  const setProgramValid = (valid: boolean) => { setConditionValid(valid); setActionValid(valid); };
  const [scopeKey, setScopeKey] = useState('default');
  const [connecting, setConnecting] = useState(false);
  const [connectionSource, setConnectionSource] = useState<StateAddress>();
  const [conditionsOpen, setConditionsOpen] = useState(false), [actionsOpen, setActionsOpen] = useState(false);
  const [simulationOpen, setSimulationOpen] = useState(false), [runtimeOpen, setRuntimeOpen] = useState(false);
  const [runtime, setRuntime] = useState<MachineRuntime>();
  const [events, setEvents] = useState<StateMachineEventDescriptor[]>([]);
  const [preview, setPreview] = useState<PreviewResult>();
  const [trace, setTrace] = useState<PreviewEvent[]>([]);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [syntheticStates, setSyntheticStates] = useState<Record<string, string>>({});
  const dirtyEntries = Object.entries(entries).filter(([, item]) => contentKey(item) !== item.base);
  const dirty = dirtyEntries.length > 0;
  // Viewport, anchor visibility and node positions are presentation changes.
  // Only edits to the definition (or an unfinished input) need a close warning.
  const definitionDirty = Object.values(entries).some(item => definitionKey(item.machine) !== item.baseDefinition);
  const saveEntries = Object.entries(entries).filter(([, item]) => contentKey(item) !== item.base || (item.document.definition && !item.document.definition_version));
  const loadEntry = useCallback((id: string): Promise<Entry> => {
    if (entriesRef.current[id]) return Promise.resolve(entriesRef.current[id]);
    const pending = loading.current.get(id);
    if (pending) return pending;
    const request = worldApi.getStateMachine(id).then(document => {
      const object = useWorldStore.getState().cards.find(item => item.id === id);
      if (!object) throw new Error('Object is no longer available.');
      const restored = machineFromDocument(document);
      if (document.definition && !restored) throw new Error('This saved configuration cannot be opened yet.');
      const machine: StateMachine = stateMachineEditorAvailable(object, useWorldStore.getState().catalog) && restored
        ? restored : { version: 2, entities: [], rules: [] };
      const initial = { machine, document, presentation: document.presentation, base: '', baseDefinition: definitionKey(machine) };
      initial.base = contentKey(initial);
      entriesRef.current = { ...entriesRef.current, [id]: initial }; setEntries(entriesRef.current);
      return initial;
    }).finally(() => loading.current.delete(id));
    loading.current.set(id, request); return request;
  }, [card.id]);
  useEffect(() => {
    const element = dialog.current!, previous = document.activeElement as HTMLElement | null;
    element.showModal(); setDialogReady(true);
    void worldApi.getStateMachineEvents().then(response => setEvents(response.events)).catch(() => {});
    return () => { element.close(); previous?.focus(); };
  }, []);
  useEffect(() => {
    let active = true;
    let refreshTimer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const result = await worldApi.getStateMachineRuntime(activeId);
        if (active) setRuntime(result);
      } catch { /* Background diagnostics must not interrupt editing. */ }
      finally { if (active) refreshTimer = setTimeout(() => void refresh(), 3000); }
    };
    setError('');
    void loadEntry(activeId).catch(cause => setError(apiErrorMessage(cause)));
    void worldApi.getStateMachineMembers(activeId).then(items => setMembers(current => ({ ...current, [activeId]: items }))).catch(cause => setError(apiErrorMessage(cause)));
    setRuntime(undefined); void refresh(); setPreview(undefined); setTrace([]); setSyntheticStates({});
    return () => { active = false; clearTimeout(refreshTimer); };
  }, [activeId, loadEntry]);
  useEffect(() => {
    for (const reference of entry?.machine.references ?? []) {
      void loadEntry(reference.card_id).catch(cause => setError(apiErrorMessage(cause)));
    }
  }, [entry?.machine.references, loadEntry]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (definitionDirty || !programValid) event.preventDefault(); };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [definitionDirty, programValid]);
  const activeGroup = entry?.machine.entities.find(entity => entity.id === groups[activeId] && entity.ownership !== 'system') ?? entry?.machine.entities.find(entity => entity.ownership !== 'system');
  const canvasKey = '__canvas__';
  const included = [...new Set([activeId, ...expanded[activeId] ?? [], ...entry?.machine.references?.map(reference => reference.card_id) ?? []])].filter(id => !!entries[id]);
  const ownerEntry = (id: string) => ({ cardId: id, machine: entries[id].machine, version: entries[id].document.definition_version });
  const view = entry ? projectView(activeId, included.map(ownerEntry)) : undefined;
  const allEntities = view?.machine.entities ?? [];
  const canonicalRules: TransitionRule[] = allEntities.flatMap(group => (group.projection ?? []).map((projection, index) => ({
    id: `canonical:${group.id}:${index}`, name: projection.label ?? t('Canonical runtime transition'), enabled: true, canonical: true,
    trigger: {entity_id: group.id, event: projection.event}, conditions: [],
    effects: [{entity_id: group.id, from_state: projection.from_state, to_state: projection.to_state}],
  })));
  const allRules = [...view?.machine.rules ?? [], ...canonicalRules];
  const contextMachine: StateMachine = view?.machine ?? { version: 2, entities: [], rules: [] };
  const graphMachine: StateMachine = { ...contextMachine, rules: allRules,
    entities: allEntities.map(entity => ({ ...entity, parent_id: undefined })) };
  const selectedEntity = allEntities.find(entity => 'entityId' in selection && entity.id === selection.entityId);
  const selectedState = selectedEntity?.states.find(state => 'stateId' in selection && state.id === selection.stateId);
  const rule = allRules.find(item => 'ruleId' in selection && item.id === selection.ruleId);
  const ownerId = (rule ? view?.ruleOwners[rule.id]?.cardId : view?.entityOwners[selectedEntity?.id ?? '']?.cardId) ?? activeId;
  const selectedOriginalId = view?.entityOwners[selectedEntity?.id ?? '']?.entityId;
  const currentInstances = runtime?.instances.filter(instance => instance.definition_version === entry?.document.definition_version) ?? [];
  const instanceIds = new Set(currentInstances.map(instance => instance.id));
  const ruleStatus: Record<string, string> = {};
  for (const receipt of [...runtime?.diagnostics ?? []].reverse()) {
    if (!instanceIds.has(String(receipt.instance_id))) continue;
    const observations = Array.isArray(receipt.rules) ? receipt.rules as {rule_id:string; reason:string}[] : [];
    for (const observation of observations) ruleStatus[observation.rule_id] = observation.reason;
  }
  for (const action of [...runtime?.actions ?? []].reverse()) if (instanceIds.has(String(action.instance_id))) ruleStatus[String(action.rule_id)] = String(action.status);
  const currentEntity = selectedEntity?.ownership !== 'system' ? selectedEntity ?? activeGroup : activeGroup;
  const issues = useMemo(() => Object.values(entries).flatMap(item => (item.machine.entities.length || item.machine.rules.length) ? validateMachine(item.machine) : []), [entries]);
  const edit = (id: string, machine: StateMachine) => {
    setEntries(current => ({ ...current, [id]: { ...current[id], machine } })); setError(''); setClosing(false); setPreview(undefined); setTrace([]);
  };
  const editRule = (patch: Partial<TransitionRule>) => {
    if (!rule || !view || rule.canonical) return;
    const changed = ruleForOwner(view, ownerEntry(ownerId), { ...rule, ...patch });
    edit(ownerId, { ...entries[ownerId].machine, version: 2, references: changed.references,
      rules: entries[ownerId].machine.rules.map(item => item.id === changed.rule.id ? changed.rule : item) });
  };
  const editEntity = (patch: Partial<MachineEntity>) => selectedEntity && selectedEntity.ownership !== 'system' && edit(ownerId, { ...entries[ownerId].machine,
    entities: entries[ownerId].machine.entities.map(entity => entity.id === selectedOriginalId ? { ...entity, ...patch } : entity) });
  const cancelConnection = () => { setConnecting(false); setConnectionSource(undefined); };
  const dismiss = () => { setSelection({ entityId: activeGroup?.id ?? activeId }); setAnchor(undefined); setProgramValid(true); };
  const requestClose = () => { if (busy) return; if (definitionDirty || !programValid) setClosing(true); else close(); };
  const save = async () => {
    if (issues.length || !programValid) return;
    setBusy(true); setError('');
    try {
      for (const [id, item] of [...saveEntries].sort(([a], [b]) => Number(a === card.id) - Number(b === card.id))) {
        const split = splitMachine(item.machine, item.presentation);
        const document = await worldApi.saveStateMachine(id, split.definition, split.presentation, item.document.revision);
        const machine = machineFromDocument(document);
        if (!machine) throw new Error('The saved definition could not be read.');
        const saved = { machine, document, presentation: document.presentation, base: '', baseDefinition: definitionKey(machine) };
        saved.base = contentKey(saved); setEntries(current => ({ ...current, [id]: saved }));
      }
      setClosing(false);
    } catch (cause) { setError(apiErrorMessage(cause)); } finally { setBusy(false); }
  };
  const navigate = async (id: string, nextPath = [...path, id]) => {
    if (!programValid) { setError(t('Finish editing the condition before switching.')); return; }
    try { const loaded = await loadEntry(id); setPath(nextPath); setSelection({ entityId: loaded.machine.entities[0]?.id ?? id }); setAnchor(undefined); setPicker(undefined); setQuery(''); cancelConnection(); }
    catch (cause) { setError(apiErrorMessage(cause)); }
  };
  const expandMember = async (id: string) => {
    if (!programValid) return;
    const current = expanded[activeId] ?? [];
    if (current.includes(id)) { setExpanded(previous => ({ ...previous, [activeId]: current.filter(item => item !== id) })); return; }
    try { await loadEntry(id); setExpanded(previous => ({ ...previous, [activeId]: [...current, id] })); } catch (cause) { setError(apiErrorMessage(cause)); }
  };
  const addState = () => {
    if (!entry || !editable || !programValid) return;
    const targetGroup = currentEntity?.ownership !== 'system' ? currentEntity : entry.machine.entities.find(entity => entity.ownership !== 'system');
    if (!targetGroup) {
      const entity = createEntity(entry.machine.entities.length ? t('User states') : activeCard!.name, 'group', activeId);
      if (!entry.machine.entities.length) entity.id = activeId;
      const state = { id: makeId('state'), label: t('New state'), position: nextStatePosition(entry.machine.entities.flatMap(entity => entity.states)) };
      entity.states = [state]; entity.initial_state = state.id;
      edit(activeId, { ...entry.machine, entities: [...entry.machine.entities, entity] });
      setGroups(current => ({ ...current, [activeId]: entity.id }));
      cancelConnection(); setSelection({ entityId: entity.id, stateId: state.id }); setAnchor(undefined);
      return;
    }
    const binding = view!.entityOwners[targetGroup.id];
    const id = binding.cardId;
    const position = nextStatePosition(entries[id].machine.entities.flatMap(entity => entity.states));
    const state = { id: makeId('state'), label: t('New state'), position };
    edit(id, { ...entries[id].machine, entities: entries[id].machine.entities.map(entity => entity.id === binding.entityId ? { ...entity, initial_state: entity.initial_state || state.id, states: [...entity.states, state] } : entity) });
    if (id === activeId) setGroups(current => ({...current, [activeId]: binding.entityId}));
    cancelConnection(); setSelection({ entityId: targetGroup.id, stateId: state.id }); setAnchor(undefined);
  };
  const addRule = (start: StateAddress, end: StateAddress, nextAnchor?: GraphAnchor) => {
    if (!entry || !programValid) return;
    let next: TransitionRule;
    try { next = connectStates(contextMachine, start, end); }
    catch (cause) { setError(apiErrorMessage(cause)); cancelConnection(); return; }
    const startOwner = view!.entityOwners[start.entityId].cardId, endOwner = view!.entityOwners[end.entityId].cardId;
    const targetOwner = startOwner === endOwner ? startOwner : activeId;
    const changed = ruleForOwner(view!, ownerEntry(targetOwner), next);
    const machine: StateMachine = { ...entries[targetOwner].machine, version: 2, references: changed.references, rules: [...entries[targetOwner].machine.rules, changed.rule] };
    edit(targetOwner, machine);
    const nextView = projectView(activeId, included.map(id => id === targetOwner ? { ...ownerEntry(id), machine } : ownerEntry(id)));
    const ruleId = Object.entries(nextView.ruleOwners).find(([, owner]) => owner.cardId === targetOwner && owner.ruleId === changed.rule.id)![0];
    cancelConnection(); setSelection({ ruleId }); setAnchor(nextAnchor); setConditionsOpen(false); setActionsOpen(false);
  };
  const chooseSelection = (next: Selection, nextAnchor?: GraphAnchor) => {
    if (!programValid) { setError(t('Finish editing the condition before switching.')); return; }
    if (connecting && 'entityId' in next && next.stateId) {
      const address = { entityId: next.entityId, stateId: next.stateId };
      if (!connectionSource) { setConnectionSource(address); setSelection(next); } else addRule(connectionSource, address, nextAnchor);
      return;
    }
    cancelConnection(); setSelection(next); setAnchor(nextAnchor); setConditionsOpen(false); setActionsOpen(false); setError('');
  };
  const beginConnection = () => { if (programValid) { setConnecting(true); setAnchor(undefined); setConnectionSource(selectedEntity && selectedState ? { entityId: selectedEntity.id, stateId: selectedState.id } : undefined); } };
  const stateOptions = (id: string) => allEntities.find(entity => entity.id === id)?.states.map(state => <option key={state.id} value={state.id}>{state.label}</option>);
  const runPreview = async () => {
    if (!rule || rule.trigger.event === 'unconfigured') return;
    setPreviewBusy(true); setError('');
    const ownRule = entries[ownerId].machine.rules.find(item => item.id === view?.ruleOwners[rule.id].ruleId)!;
    const next = [...trace, { ...ownRule.trigger, event_id: makeId('preview'), scope_key: 'preview', time_ms: trace.length * 1000 }];
    try {
      const inverse = Object.fromEntries(Object.entries(view!.entityIds[ownerId]).map(([local, shown]) => [shown, local]));
      const states = Object.fromEntries(Object.entries(syntheticStates).filter(([id]) => inverse[id]).map(([id, state]) => [inverse[id], state]));
      const result = await worldApi.previewStateMachine(entries[ownerId].machine, next, Object.keys(states).length ? states : undefined);
      setPreview({ ...result, states: Object.fromEntries(Object.entries(result.states).map(([id, state]) => [view!.entityIds[ownerId][id] ?? id, state])), last_rule_id: result.last_rule_id ? Object.entries(view!.ruleOwners).find(([, owner]) => owner.cardId === ownerId && owner.ruleId === result.last_rule_id)?.[0] : undefined }); setTrace(next);
    }
    catch (cause) { setError(apiErrorMessage(cause)); } finally { setPreviewBusy(false); }
  };
  const refreshRuntime = async () => { try {
    const result = await worldApi.getStateMachineRuntime(activeId); setRuntime(result);
    setEntries(current => { const item = current[activeId]; if (!item) return current;
      return { ...current, [activeId]: { ...item, document: { ...item.document,
        enabled: result.instances.some(instance => instance.enabled && instance.definition_version === item.document.definition_version) } } }; });
  } catch (cause) { setError(apiErrorMessage(cause)); } };
  const filteredMembers = (members[activeId] ?? []).filter(member => (member.state_machine_editor || member.has_members) && member.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const stageRect = board.current?.getBoundingClientRect();
  const panelLeft = anchor && stageRect ? Math.min(Math.max(12, anchor.x - stageRect.left + 16), Math.max(12, stageRect.width - 360)) : undefined;
  const panelTop = anchor && stageRect ? Math.min(Math.max(12, anchor.y - stageRect.top + 12), Math.max(12, stageRect.height - 370)) : 16;
  return createPortal(<dialog ref={dialog} className="sm-dialog" aria-label={t('State machine editor')} onCancel={event => { event.preventDefault(); if (connecting) cancelConnection(); else requestClose(); }} onKeyDown={event => event.stopPropagation()} onPointerDown={event => event.stopPropagation()} onWheel={event => event.stopPropagation()}>
    <header className="sm-header"><div className="sm-header-icon"><GitBranch size={22} /></div><div><span className="sm-eyebrow">{t('State machine')}</span><h2>{card.name}</h2></div>
      <span className="sm-save-status">{t(busy ? 'Saving…' : dirty || !programValid ? 'Unsaved changes' : entry?.document.definition && !entry.document.definition_version ? 'Node type definition' : entry?.document.definition ? 'Saved' : 'No state machine')}</span>
      <button className="primary-button" disabled={busy || !!issues.length || !programValid || !saveEntries.length} onClick={() => void save()}><Save size={15} />{t('Save changes')}</button>
      {entry?.machine.status_entity_id && !entry.document.enabled && <button className="secondary-button" disabled={busy || dirty || !programValid} onClick={async () => { try { await worldApi.enableStateMachine(activeId, entry.document.definition_version, 'default'); await refreshRuntime(); } catch (cause) { setError(apiErrorMessage(cause)); } }}>{t('Apply saved version')}</button>}
      <button className="icon-button" disabled={busy} onClick={requestClose} aria-label={t('Close state machine editor')}><X size={21} /></button>
    </header>
    {(error || issues.length > 0) && <div role="alert" className="sm-error">{error || issues[0]}</div>}
    {closing && <div className="sm-close-prompt"><span>{t('Keep editing or discard your unsaved configuration?')}</span><button className="secondary-button" onClick={() => setClosing(false)}>{t('Keep editing')}</button><button className="secondary-button" onClick={close}>{t('Discard changes')}</button></div>}
    <div className={`sm-body ${busy ? 'is-busy' : ''}`} ref={element => { if (element) element.inert = busy; }}>
      <section className="sm-stage">
        <nav className="sm-breadcrumbs" aria-label={t('State machine navigation')}>
          {path.map((id, index) => <span key={id}><button className="sm-text-button" onClick={() => { if (index < path.length - 1) void navigate(id, path.slice(0, index + 1)); else { setPicker(picker === 'legion' ? undefined : 'legion'); setQuery(''); } }}>{cards.find(item => item.id === id)?.name ?? id}<ChevronDown size={13} /></button><span>/</span></span>)}
          <button className="sm-text-button" onClick={() => { setPicker(picker === 'member' ? undefined : 'member'); setQuery(''); }}>{t('Member')}<ChevronDown size={13} /></button><span>/</span>
          <button className="sm-text-button" onClick={() => { setPicker(picker === 'group' ? undefined : 'group'); setQuery(''); }}>{t('State canvas')}<ChevronDown size={13} /></button>
          <span className="sm-definition-status">{entry?.document.enabled ? t('Applied') : t('Draft · automation disabled')}</span>
        </nav>
        {picker && <section className="sm-navigation-picker" aria-label={t('Object picker')}>
          <input aria-label={t('Search objects')} value={query} onChange={event => setQuery(event.target.value)} placeholder={t('Search objects')} autoFocus />
          <button className="icon-button" aria-label={t('Close picker')} onClick={() => setPicker(undefined)}><X size={15} /></button>
          <div className="sm-picker-results">
            {picker === 'group' ? <>{entry?.machine.entities.filter(entity => entity.ownership !== 'system' && entity.label.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(entity => <button className="sm-list-item" key={entity.id} onClick={() => { setGroups(current => ({ ...current, [activeId]: entity.id })); chooseSelection({ entityId: entity.id }); setPicker(undefined); }}>{entity.label}</button>)}
              {entry && activeGroup && activeGroup.ownership !== 'system' && <div className="sm-group-settings"><label>{t('Group name')}<input value={activeGroup.label} maxLength={200} onChange={event => edit(activeId, {...entry.machine, entities: entry.machine.entities.map(group => group.id === activeGroup.id ? {...group, label: event.target.value} : group)})} /></label><button className="sm-delete" disabled={entry.machine.entities.length <= 1} onClick={() => { edit(activeId, removeEntity(entry.machine, activeGroup.id)); setPicker(undefined); setSelection({entityId: activeId}); }}><Trash2 size={13} />{t('Delete group')}</button></div>}
              <button className="sm-text-button" onClick={() => { if (!entry) return; const entity = createEntity(t('New group'), 'group', activeId); edit(activeId, { ...entry.machine, entities: [...entry.machine.entities, entity] }); setGroups(current => ({ ...current, [activeId]: entity.id })); setSelection({ entityId: entity.id }); setPicker(undefined); }}><Plus size={13} />{t('Add group')}</button></>
              : picker === 'legion' ? cards.filter(item => (item.id === card.id || (item.type === 'legion' && stateMachineEditorAvailable(item, catalog))) && item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(item => <button className="sm-list-item" key={item.id} onClick={() => void navigate(item.id, [item.id])}>{item.name}<ArrowRight size={14} /></button>)
              : <>{filteredMembers.map(member => <div className="sm-member-reference" key={member.id}><button className="sm-list-item" onClick={() => void navigate(member.id)}>{member.name}<ArrowRight size={14} /></button><button className="sm-text-button" disabled={!member.state_machine_editor || !member.has_definition} aria-pressed={(expanded[activeId] ?? []).includes(member.id)} onClick={() => void expandMember(member.id)}>{t((expanded[activeId] ?? []).includes(member.id) ? 'Hide states' : 'Show states')}</button></div>)}{!filteredMembers.length && <p className="sm-help">{t('No members at this level.')}</p>}</>}
          </div>
        </section>}
        <div className="sm-toolbar">
          <button className="secondary-button" disabled={!entry || !editable || !programValid} onClick={addState}><Plus size={15} />{t('Add state')}</button>
          <button className={`secondary-button ${connecting ? 'is-selected' : ''}`} disabled={!allEntities.some(entity => entity.states.length)} aria-pressed={connecting} onClick={connecting ? cancelConnection : beginConnection}><GitBranch size={15} />{t('Connect states')}</button>
          <button className="sm-text-button" aria-expanded={simulationOpen} onClick={() => { setSimulationOpen(value => !value); setRuntimeOpen(false); }}>{t('Simulate')}</button>
          <button className="sm-text-button" aria-expanded={runtimeOpen} onClick={() => { setRuntimeOpen(value => !value); setSimulationOpen(false); void refreshRuntime(); }}>{t('Runtime')}</button>
          {entry && <label className="sm-primary-select">{t('Primary display group')}<select aria-label={t('Primary display group')} value={entry.machine.status_entity_id ?? ''} onChange={event => edit(activeId, {...entry.machine, status_entity_id: event.target.value || undefined})}><option value="">{t('Default')}</option>{entry.machine.entities.map(group => <option key={group.id} value={group.id}>{group.label}{group.ownership === 'system' ? ' 🔒' : ''}</option>)}</select></label>}
          <span className="sm-toolbar-tip">{t('Click to edit. Drag to arrange.')}</span>
        </div>
        {connecting && <div className="sm-connection-guide" role="status"><span>{connectionSource ? t('Choose the target state.') : t('Choose the starting state.')}</span><button className="sm-text-button" onClick={cancelConnection}>{t('Cancel connection')}</button></div>}
        <div className="sm-board" ref={board}>
          {!entry && <p role="status" className="sm-loading">{t('Loading state definition…')}</p>}
          {entry && !entry.machine.entities.length && <p role="status" className="sm-loading">{t(editable ? 'No states yet. Add a state to configure this workflow.' : 'This object does not offer a state machine editor.')}</p>}
          {dialogReady && entry && !!entry.machine.entities.length && <MachineGraph key={activeId} machine={graphMachine} ownerLabels={Object.fromEntries(cards.map(card => [card.id, card.name]))} selection={selection} snapshot={preview?.states ?? currentInstances.find(instance => instance.enabled)?.states ?? {}} lastRule={preview?.last_rule_id} ruleStatus={ruleStatus} connecting={connecting} connectionSource={connectionSource}
            viewport={entry.presentation.viewports?.[canvasKey]} onViewportChange={viewport => setEntries(current => ({ ...current, [activeId]: { ...current[activeId], presentation: { ...current[activeId].presentation, viewports: { ...current[activeId].presentation.viewports, [canvasKey]: viewport } } } }))}
            onSelect={chooseSelection} onConnect={addRule} onPaneClick={() => { if (programValid) dismiss(); }}
            onMove={(entityId, stateId, position) => { const binding = view!.entityOwners[entityId], id = binding.cardId; edit(id, { ...entries[id].machine, entities: entries[id].machine.entities.map(entity => entity.id === binding.entityId ? { ...entity, states: entity.states.map(state => state.id === stateId ? { ...state, position } : state) } : entity) }); }} />}
          {!connecting && (selectedState || rule) && <aside className="sm-inspector sm-context-editor" style={{ left: panelLeft, right: panelLeft === undefined ? 18 : undefined, top: panelTop, maxHeight: stageRect ? Math.max(140, stageRect.height - panelTop - 12) : undefined }} aria-label={t(rule ? 'Transition editor' : 'State editor')}>
            <div className="sm-section-heading">{t(rule ? 'Connection' : 'State')}<button className="icon-button" aria-label={t('Close contextual editor')} disabled={!programValid} onClick={dismiss}><X size={16} /></button></div>
            {selectedState && selectedEntity && <>
              {selectedEntity.ownership === 'system' ? <p className="sm-help">🔒 SYSTEM · {selectedState.label}</p> : <><label>{t('State name')}<input maxLength={200} value={selectedState.label} onChange={event => editEntity({ states: selectedEntity.states.map(state => state.id === selectedState.id ? { ...state, label: event.target.value } : state) })} /></label>
              <label className="sm-check"><input type="checkbox" checked={selectedEntity.initial_state === selectedState.id} onChange={() => editEntity({ initial_state: selectedState.id })} />{t('Start here')}</label></>}
              <button className="sm-text-button" onClick={beginConnection}><ArrowRight size={14} />{t('Connect from here')}</button>
              {selectedEntity.ownership !== 'system' && <button className="sm-delete" disabled={selectedEntity.states.length <= 1} onClick={() => { edit(ownerId, removeState(entries[ownerId].machine, selectedOriginalId!, selectedState.id)); dismiss(); }}><Trash2 size={14} />{t('Delete state')}</button>}
            </>}
            {rule?.canonical && <><p className="sm-help">🔒 SYSTEM · {t('Canonical runtime transition')}</p><details className="sm-more"><summary>{t('Runtime fact')}</summary>{allEntities.find(group => group.id === rule.trigger.entity_id)?.projection?.filter(projection => projection.to_state === rule.effects[0]?.to_state && projection.from_state === rule.effects[0]?.from_state).map(projection => <p key={projection.event}>{projection.label && <span>{t(projection.label)}<br /></span>}<code>{projection.event}</code></p>)}</details></>}
            {rule && !rule.canonical && <>
              {rule.trigger.event === 'unconfigured' && <p className="sm-unconfigured" role="status">{t('Unconfigured · choose a trigger')}</p>}
              {rule.trigger.state_id && rule.trigger.event.startsWith('state.') ? <label>{t('When')} {allEntities.find(group => group.id === rule.trigger.entity_id)?.label} / {allEntities.find(group => group.id === rule.trigger.entity_id)?.states.find(state => state.id === rule.trigger.state_id)?.label}<select aria-label={t('State phase')} value={rule.trigger.event} onChange={event => { const trigger = {...rule.trigger, event: event.target.value}; editRule({trigger, program: rule.program ? {...rule.program, signals: rule.program.signals.map((signal, index) => index === 0 ? {...signal, match: trigger} : signal)} : undefined}); }}><option value="state.entered">{t('On enter')}</option><option value="state.exited">{t('On exit')}</option><option value="state.current">{t('While in state')}</option></select></label>
                : <EventPicker value={rule.trigger} machine={contextMachine} cards={cards} catalog={catalog} events={events} onChange={trigger => editRule({ trigger, enabled: trigger.event !== 'unconfigured', program: rule.program ? { ...rule.program, signals: rule.program.signals.map((signal, index) => index === 0 ? { ...signal, match: trigger } : signal) } : undefined })} />}
              {rule.command && <CommandEditor key={rule.id} request={rule.command} group={allEntities.find(group => group.id === rule.command!.entity_id)!} onChange={command => editRule({command})} onValidityChange={setActionValid} />}
              <button className="sm-text-button" aria-expanded={conditionsOpen} disabled={conditionsOpen && !conditionValid} onClick={() => setConditionsOpen(value => !value)}><Plus size={13} />{t('Condition')}</button>
              <button className="sm-text-button" aria-expanded={actionsOpen} disabled={actionsOpen && !actionValid} onClick={() => setActionsOpen(value => !value)}><Plus size={13} />{t('Then… / Also update…')}</button>
              {conditionsOpen && <TriggerProgramEditor key={rule.id} hidePrimaryPicker program={rule.program ?? createProgram(rule.trigger)} machine={contextMachine} cards={cards} catalog={catalog} onChange={program => editRule({ program, trigger: program.signals[0].match, enabled: program.signals[0].match.event !== 'unconfigured' })} onValidityChange={setConditionValid} />}
              {actionsOpen && <div className="sm-expanded-options">
                <ActionEditor actions={rule.actions ?? []} cardId={ownerId} onChange={actions => editRule({ actions })} onValidityChange={setActionValid} />
                {rule.effects.map((effect, index) => <div className="sm-effect" key={index}>
                  <label>{t('Affected object')}<select value={effect.entity_id} onChange={event => editRule({ effects: rule.effects.map((item, i) => i === index ? { entity_id: event.target.value, from_state: '*', to_state: allEntities.find(entity => entity.id === event.target.value)!.initial_state } : item) })}>{allEntities.filter(entity => entity.ownership !== 'system').map(entity => <option key={entity.id} value={entity.id}>{entity.label}</option>)}</select></label>
                  <div className="sm-from-to"><label>{t('From state')}<select value={effect.from_state} onChange={event => editRule({ effects: rule.effects.map((item, i) => i === index ? { ...item, from_state: event.target.value } : item) })}><option value="*">{t('Any state')}</option>{stateOptions(effect.entity_id)}</select></label><ArrowRight size={15} /><label>{t('To state')}<select value={effect.to_state} onChange={event => editRule({ effects: rule.effects.map((item, i) => i === index ? { ...item, to_state: event.target.value } : item) })}>{stateOptions(effect.entity_id)}</select></label></div>
                  {index > 0 && <button className="sm-text-button" onClick={() => editRule({ effects: rule.effects.filter((_, i) => i !== index) })}>{t('Remove')}</button>}
                </div>)}
                <button className="sm-text-button" disabled={allEntities.filter(entity => entity.ownership !== 'system').every(entity => rule.effects.some(effect => effect.entity_id === entity.id))} onClick={() => { const entity = allEntities.find(entity => entity.ownership !== 'system' && !rule.effects.some(effect => effect.entity_id === entity.id)); if (entity) editRule({ effects: [...rule.effects, { entity_id: entity.id, from_state: '*', to_state: entity.initial_state }] }); }}><Plus size={13} />{t('Change another object too')}</button>
              </div>}
              {ownerId === activeId && ruleStatus[rule.id] && <details className="sm-more"><summary>{t('Runtime status')}</summary><p className="sm-help">{ruleStatus[rule.id].replaceAll('_', ' ')}</p></details>}
              <details className="sm-more"><summary>{t('Advanced')}</summary>
                <label>{t('Connection name')}<input maxLength={200} value={rule.name} onChange={event => editRule({ name: event.target.value })} /></label>
                <label className="sm-check"><input type="checkbox" disabled={rule.trigger.event === 'unconfigured'} checked={rule.enabled} onChange={event => editRule({ enabled: event.target.checked })} />{t('Connection enabled')}</label>
                <label>{t('Trigger object')}<select value={rule.trigger.entity_id} onChange={event => editRule({ trigger: { entity_id: event.target.value, event: 'unconfigured' }, program: undefined, enabled: false })}>{allEntities.map(entity => <option key={entity.id} value={entity.id}>{entity.label}</option>)}</select></label>
                {rule.conditions.map((condition, index) => <div className="sm-condition" key={index}><select aria-label={t('Condition object')} value={condition.entity_id} onChange={event => editRule({ conditions: rule.conditions.map((item, i) => i === index ? { entity_id: event.target.value, state_id: allEntities.find(entity => entity.id === event.target.value)!.initial_state } : item) })}>{allEntities.map(entity => <option key={entity.id} value={entity.id}>{entity.label}</option>)}</select><select aria-label={t('Condition state')} value={condition.state_id} onChange={event => editRule({ conditions: rule.conditions.map((item, i) => i === index ? { ...item, state_id: event.target.value } : item) })}>{stateOptions(condition.entity_id)}</select><button className="icon-button" aria-label={t('Remove condition')} onClick={() => editRule({ conditions: rule.conditions.filter((_, i) => i !== index) })}><X size={13} /></button></div>)}
                <button className="sm-text-button" onClick={() => { const entity = allEntities.find(entity => !rule.conditions.some(condition => condition.entity_id === entity.id)); if (entity) editRule({ conditions: [...rule.conditions, { entity_id: entity.id, state_id: entity.initial_state }] }); }}>{t('Add state condition')}</button>
              </details>
              <button className="sm-delete" onClick={() => { edit(ownerId, { ...entries[ownerId].machine, rules: entries[ownerId].machine.rules.filter(item => item.id !== view?.ruleOwners[rule.id].ruleId) }); dismiss(); }}><Trash2 size={14} />{t('Delete connection')}</button>
            </>}
          </aside>}
        </div>
        {simulationOpen && <section className="sm-progress-panel" aria-label={t('Simulation')}>
          <div><strong>{t('Simulation')}</strong><span className="sm-help">{t('Synthetic events use the backend evaluator. Actions are previews only.')}</span><button className="sm-text-button" onClick={() => { setTrace([]); setPreview(undefined); }}>{t('Reset')}</button></div>
          <details><summary>{t('Synthetic starting states')}</summary><div className="sm-synthetic-states">{allEntities.map(entity => <label key={entity.id}>{entity.label}<select value={syntheticStates[entity.id] ?? entity.initial_state} onChange={event => { setSyntheticStates(current => ({ ...Object.fromEntries(allEntities.map(item => [item.id, current[item.id] ?? item.initial_state])), [entity.id]: event.target.value })); setTrace([]); setPreview(undefined); }}>{stateOptions(entity.id)}</select></label>)}</div></details>
          <button className="secondary-button" disabled={!rule || rule.canonical || rule.trigger.event === 'unconfigured' || previewBusy || !programValid} onClick={() => void runPreview()}>{t('Send selected trigger')}</button>
          {!rule && <span className="sm-help">{t('Select a connection to send a synthetic trigger.')}</span>}
          {preview?.steps.at(-1) && <div role="status">{preview.steps.at(-1)!.rules.map(result => <span className="sm-diagnostic" key={result.rule_id}>{allRules.find(rule => rule.id === result.rule_id)?.name}: {result.reason}</span>)}{preview.steps.at(-1)!.actions?.map(action => <span className="sm-diagnostic" key={action.id}>{t('Action preview')}: {action.capability ?? action.action ?? action.kind}</span>)}</div>}
        </section>}
        {runtimeOpen && <section className="sm-progress-panel" aria-label={t('Runtime diagnostics')}>
          <div><strong>{t('Runtime')}</strong><button className="sm-text-button" onClick={() => void refreshRuntime()}>{t('Refresh')}</button></div>
          <div className="sm-runtime-summary"><strong>{t(definitionDirty ? 'Changes awaiting save' : entry?.document.enabled ? 'Applied' : 'Changes awaiting application')}</strong></div>
          {!entry?.machine.status_entity_id && <label>{t('Instance scope')}<input value={scopeKey} onChange={event => setScopeKey(event.target.value)} /></label>}
          {entry && !runtime?.instances.some(instance => instance.enabled && instance.definition_version === entry.document.definition_version && (instance.scope_key ?? 'default') === (entry.machine.status_entity_id ? 'default' : scopeKey)) && <button className="secondary-button" disabled={dirty || !entry.document.definition_version || !programValid || !scopeKey.trim()} onClick={async () => { try { await worldApi.enableStateMachine(activeId, entry.document.definition_version, entry.machine.status_entity_id ? 'default' : scopeKey); await refreshRuntime(); } catch (cause) { setError(apiErrorMessage(cause)); } }}>{t('Apply saved version')}</button>}
          <div className="sm-runtime-instances">{runtime?.instances.map(instance => <div className="sm-runtime-instance" key={instance.id}><span>{instance.scope_key && instance.scope_key !== 'default' ? `${instance.scope_key} ? ` : ''} v{instance.definition_version} ? {instance.enabled ? t('Enabled') : t('Disabled')}</span>{instance.enabled && <button className="sm-text-button" onClick={async () => { try { await worldApi.disableStateMachine(activeId, instance.id); await refreshRuntime(); } catch (cause) { setError(apiErrorMessage(cause)); } }}>{t('Disable automation')}</button>}</div>)}</div>
          <RuntimeHistory diagnostics={runtime?.diagnostics ?? []} actions={runtime?.actions ?? []} entities={allEntities} />
        </section>}
      </section>
    </div>
  </dialog>, document.body);
}
