import { create as createStore } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { profileStorage } from '../state/profileStorage';
import { worldApi, apiErrorMessage } from '../api/client';
import { useWorldStore, mergeCards } from '../state/worldStore';
import { t } from '../i18n';
import { hasDefaultModelConfiguration, hasModelConfiguration } from '../state/modelConnections';
import { useCardLibrary } from '../state/cardLibrary';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { NODE_SURFACE_SIZE, useNodeSurfaceStore } from '../state/nodeSurfaces';
import { beginGlueEdit, persistGlue, useGlueStore, type GlueBox } from '../state/glue';
import { observeInteractions, type WorldInteraction } from '../state/interactions';
import { positionSurfaceAtNodeCenter } from '../canvas/nodeDisplacement';
import { getConnectionOptions } from '../state/relationships';
import { STEPS, stepComplete, type Baseline, type Demonstration, type Observation, type Role, type Target } from './steps';
import type { LegionSummary, WorldCard, WorldSnapshot, WorldPosition } from '../types/world';

const TYPES: Record<Role, string> = { demo: 'text', practice: 'text', agent: 'agent', conversation: 'conversation', sandbox: 'sandbox', glueA: 'text', glueB: 'text', ministerRole: 'core.minister-role', minister: 'agent', legion: 'legion' };
export interface DemoRecord { id: string; name: string; created_at?: string; contentRevision?: number }
export interface TutorialSession {
  id: string;
  step: string;
  completedDemo?: string;
  initialIds: string[];
  refs: Partial<Record<Role, string>>;
  demos: DemoRecord[];
}
interface TutorialState {
  status: 'new' | 'started' | 'skipped' | 'completed';
  session?: TutorialSession;
  view: 'hidden' | 'welcome' | 'active' | 'paused';
  busy: boolean;
  error?: string;
  target?: Target;
  quickStart?: { agentId: string; conversationId: string };
  celebration: number;
  ready: boolean;
}
export const useTutorialStore = createStore<TutorialState>()(persist((): TutorialState => ({
  status: 'new', view: 'hidden', busy: false, celebration: 0, ready: false,
}), {
  name: 'oaw-onboarding-v1', version: 1,
  storage: createJSONStorage(() => profileStorage),
  partialize: ({ status, session, quickStart }) => ({ status, session, quickStart }),
  // Future incompatible step sequences resume at a reviewable entrance.
  merge: (saved, current) => {
    const value = saved as Partial<TutorialState> | undefined;
    const session = value?.session;
    return { ...current, quickStart: value?.quickStart, status: value?.status ?? 'new', session: session && {
      ...session, step: STEPS.some(step => step.id === session.step) ? session.step : 'enter',
    } };
  },
}));

export interface GuideVisuals {
  preparePlace?: (existingId?: string) => () => void;
  findSpace?: (preferred: WorldPosition, size: { width: number; height: number }) => WorldPosition;
  place: (id: string, signal: AbortSignal) => Promise<void>;
  connect: (source: string, target: string, signal: AbortSignal) => Promise<void | (() => void)>;
  move: (id: string, position: WorldPosition, signal: AbortSignal) => Promise<void>;
  focus: (ids: string[], reserveCardSpace?: boolean) => Promise<void>;
}
let visuals: GuideVisuals | undefined;
let baseline: Baseline | undefined;
let task: Promise<void> | undefined;
let abort: AbortController | undefined;
let checking: Promise<void> | undefined;
let stopping = false;
let transitioning = false;
const state = () => useTutorialStore.getState();
const world = () => useWorldStore.getState();
const currentStep = () => STEPS.find(step => step.id === state().session?.step) ?? STEPS[0];
const cardFor = (role: Role) => world().cards.find(card => card.id === state().session?.refs[role]);
function saveSession(patch: Partial<TutorialSession>) {
  const session = state().session;
  if (session) useTutorialStore.setState({ session: { ...session, ...(patch.step && patch.step !== session.step ? { completedDemo: undefined } : {}), ...patch } });
}
function observation(): Observation {
  const w = world(), surfaces = useNodeSurfaceStore.getState();
  return { cards: w.cards, edges: w.edges, selected: w.selectedCardIds, surfaces: surfaces.surfaceLevels,
    bonds: useGlueStore.getState().bonds, viewport: w.viewport, settingsOpen: w.settingsOpen, library: useCardLibrary.getState(), legionWorkspaceId: useLegionWorkspace.getState().activeId,
    settled: !surfaces.dragging && !w.positionCommitBusy && !w.historyBusy && w.syncState === 'online',
    deleted: [...Object.keys(w.cardTombstones), ...w.undoStack.flatMap(op => op.kind === 'cards-deleted' ? op.cards.map(card => card.id) : [])],
  };
}
function rebase() {
  const step = currentStep(), card = step.role && cardFor(step.role);
  baseline = { viewport: { ...world().viewport }, position: card ? { ...card.position } : undefined,
    config: card ? JSON.stringify(card.config) : undefined };
}
function goNext() {
  if (transitioning) return;
  const step = currentStep();
  const next = STEPS[STEPS.indexOf(step) + 1];
  if (!next) return;
  transitioning = true;
  try {
    // Close only this tutorial's working surfaces, at chapter boundaries the user chose.
    if (['conversation', 'sandbox', 'glue-demo', 'minister-card', 'minister', 'legion-form'].includes(next.id)) {
      for (const id of Object.values(state().session?.refs ?? {})) useNodeSurfaceStore.getState().dismiss(id);
    }
    if (next.id === 'minister' && state().session?.refs.agent) void visuals?.focus([state().session!.refs.agent!, state().session!.refs.ministerRole!].filter(Boolean));
    if (step.expects === 'connect') world().selectEdge(undefined);
    if (next.id === 'configure' && step.id === 'model-save') useWorldStore.setState({ settingsOpen: false });
    saveSession({ step: next.id, completedDemo: undefined });
    useTutorialStore.setState(s => ({ error: undefined, target: undefined, ready: false, celebration: s.celebration + (step.expects ? 1 : 0) }));
    rebase();
    if (next.id === 'legion-form') {
      world().selectCards([], { syncCanvas: true });
      void visuals?.focus(['agent', 'conversation', 'sandbox'].flatMap(role => state().session?.refs[role as Role] ?? []));
    }
    if (next.id === 'legion-open' && cardFor('legion')) void visuals?.focus([cardFor('legion')!.id]);
    if (next.expects === 'select' && next.role) {
      world().selectCards(world().selectedCardIds.filter(id => id !== state().session?.refs[next.role!]), { syncCanvas: true });
    }
  } finally { transitioning = false; }
  if (next.id === 'minister-card') void runTask(async signal => {
    await prepareDeck(['core.minister-role']); ensureActive(signal);
    if (state().session?.refs.agent) await visuals?.focus([state().session!.refs.agent!], true);
  });
  // Several genuine actions may already be satisfied (e.g. clicking also selects).
  queueMicrotask(() => observe());
}
function observe(event?: WorldInteraction) {
  const s = state();
  if (s.view !== 'active' || s.busy || stopping || transitioning || !s.session || !baseline) return;
  const step = currentStep();
  if (step.expects === 'place' && step.role && !cardFor(step.role)) {
    const used = new Set([...s.session.initialIds, ...Object.values(s.session.refs), ...s.session.demos.map(demo => demo.id)]);
    const candidates = world().cards.filter(card => !used.has(card.id) && card.type === TYPES[step.role!] && !card.parent_id && !card.equipment);
    const card = candidates.find(card => world().selectedCardIds.includes(card.id)) ?? candidates.at(-1);
    if (card) saveSession({ refs: { ...s.session.refs, [step.role]: card.id } });
  }
  if (step.id === 'minister' && cardFor('agent')?.minister) saveSession({ refs: { ...state().session!.refs, minister: cardFor('agent')!.id } });
  if (step.id === 'legion-form') {
    const parentId = cardFor('agent')?.parent_id;
    if (parentId && cardFor('conversation')?.parent_id === parentId && cardFor('sandbox')?.parent_id === parentId
      && world().cards.some(card => card.id === parentId && card.type === 'legion') && s.session.refs.legion !== parentId)
      saveSession({ refs: { ...state().session!.refs, legion: parentId } });
  }
  const complete = stepComplete(step, state().session!.refs, baseline, observation(), event);
  if (step.review) { if (state().ready !== complete) useTutorialStore.setState({ ready: complete }); }
  else if (complete) goNext();
}

/** Verify the entire world, not just the currently loaded viewport chunks. */
async function checkWelcome() {
  if (checking || state().view !== 'hidden' || world().syncState !== 'online') return checking;
  if (state().status === 'started' && state().session) {
    useTutorialStore.setState({ view: 'paused' });
    return;
  }
  if (state().status !== 'new' || world().stressCards.length) return;
  checking = (async () => {
    try {
      const snapshot = await worldApi.getWorld();
      if (state().status !== 'new' || state().view !== 'hidden') return;
      if (snapshot.nodes.length || world().cards.length) useTutorialStore.setState({ status: 'skipped' });
      else useTutorialStore.setState({ view: 'welcome' });
    } catch { /* A failed eligibility check must not present an empty, writable world. */ }
  })().finally(() => { checking = undefined; });
  return checking;
}

export async function prepareDeck(types: string[]) {
  const library = useCardLibrary.getState;
  if (!library().snapshot) await library().refresh();
  for (const type of types) {
    const snapshot = library().snapshot;
    if (!snapshot) throw new Error(library().error || 'Your card library is still loading. Try again.');
    if (!snapshot.collection[type]?.unlocked) {
      const pack = Object.values(snapshot.packs).find(item => item.owned && item.definition.cards.includes(type) && snapshot.available_pack_ids.includes(item.definition.id));
      if (!pack) throw new Error(`The ${type} card is unavailable. Open the Library to check its pack or plugin, then retry.`);
      if (!await library().edit({ action: 'open_pack', id: pack.definition.id })) throw new Error(library().error || 'The pack could not be opened. Retry.');
    }
    if (!library().snapshot?.available_card_ids.includes(type)) throw new Error(`The ${type} plugin is unavailable. Check the Library, then retry.`);
  }
  let snapshot = library().snapshot!;
  let deck = snapshot.decks.find(item => item.id === snapshot.active_deck_id);
  if (!deck) {
    const previous = new Set(snapshot.decks.map(item => item.id));
    snapshot = await library().edit({ action: 'create_deck', name: 'First world', icon: 'folder' }) ?? snapshot;
    deck = snapshot.decks.find(item => !previous.has(item.id));
    if (!deck || !await library().edit({ action: 'activate_deck', id: deck.id })) throw new Error(library().error || 'Your deck could not be created. Retry.');
  }
  const entries = [...deck.entries, ...types.filter(type => !deck!.entries.some(entry => entry.kind === 'node' && entry.id === type)).map(id => ({ kind: 'node' as const, id }))];
  if (entries.length !== deck.entries.length && !await library().edit({ action: 'update_deck', id: deck.id, entries })) throw new Error(library().error || 'Your deck could not be saved. Retry.');
  if (world().paletteCollapsed) world().togglePalette();
}

function center(dx = 0, dy = 0): WorldPosition {
  const v = world().viewport;
  return { x: (v.width * .48 - v.x) / v.zoom - 48 + dx, y: (v.height * .43 - v.y) / v.zoom - 48 + dy };
}
function ensureActive(signal: AbortSignal) { signal.throwIfAborted(); }
async function create(role: Role, position: WorldPosition, temporary: boolean, signal: AbortSignal) {
  ensureActive(signal);
  const existing = cardFor(role);
  if (existing) return existing;
  const card = await world().createCard(TYPES[role], position);
  if (!card) throw new Error('The card could not be placed. Check the connection and retry.');
  // Register even if Skip was pressed while creation was in flight.
  saveSession({ refs: { ...state().session?.refs, [role]: card.id },
    ...(temporary ? { demos: [...(state().session?.demos ?? []), { id: card.id, name: card.name, created_at: card.created_at, contentRevision: Number(card.config.revision ?? 0) }] } : {}) });
  ensureActive(signal);
  if (temporary) {
    await world().updateCard(card.id, { name: role === 'demo' ? 'Tutorial · placement demo' : `Tutorial · ${role === 'glueA' ? 'stick me' : 'stick with me'}` });
    const saved = world().cards.find(item => item.id === card.id)!;
    saveSession({ demos: state().session!.demos.map(demo => demo.id === card.id ? { ...demo, name: saved.name } : demo) });
  }
  return world().cards.find(item => item.id === card.id)!;
}
function requireCard(role: Role) {
  const card = cardFor(role);
  if (!card) throw new Error(`The ${TYPES[role]} card is missing. Use “Recover this step” to place a replacement.`);
  return card;
}
async function demonstrate(action: Demonstration, signal: AbortSignal) {
  switch (action) {
    case 'library': useCardLibrary.setState({ tab: 'packs' }); useCardLibrary.getState().show(); break;
    case 'deck': await prepareDeck(['text', 'agent', 'conversation', 'sandbox']); break;
    case 'place': {
      const bridge = visuals;
      const reveal = bridge?.preparePlace?.(cardFor('demo')?.id);
      try {
        const preferred = center(-190, 50);
        const card = await create('demo', bridge?.findSpace?.(preferred, { width: 380, height: 240 }) ?? preferred, true, signal);
        await bridge?.focus([card.id]);
        ensureActive(signal);
        useTutorialStore.setState({ target: 'demo' });
        await bridge?.place(card.id, signal);
      } finally { reveal?.(); }
      break;
    }
    case 'connect': {
      const agent = requireCard('agent'), conversation = requireCard('conversation');
      await visuals?.focus([agent.id, conversation.id]);
      if (world().edges.some(edge => edge.source === agent.id && edge.target === conversation.id && edge.relationship === 'participate')) break;
      const option = getConnectionOptions(world().catalog, agent.type, conversation.type).find(item => item.value === 'participate');
      if (!option) throw new Error('This catalog does not provide Participate for these cards. Check their plugins in the Library.');
      const clearTrace = await visuals?.connect(agent.id, conversation.id, signal);
      try {
        ensureActive(signal);
        if (world().pendingConnection) throw new Error('Finish or close your current capability chooser, then retry this demonstration.');
        world().requestConnection(agent.id, conversation.id);
        await world().createConnection(option.value);
        if (!world().edges.some(edge => edge.source === agent.id && edge.target === conversation.id && edge.relationship === option.value)) throw new Error('The relationship was not saved. Retry after checking the capability chooser.');
        world().selectEdge(undefined);
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      } finally { clearTrace?.(); }
      break;
    }
    case 'glue': {
      const preferred = center(-25, 20);
      const space = visuals?.findSpace?.(preferred, { width: 880, height: 350 }) ?? preferred;
      const a = await create('glueA', { x: space.x - 195, y: space.y }, true, signal);
      const b = await create('glueB', { x: a.position.x + 390, y: a.position.y }, true, signal);
      useTutorialStore.setState({ target: 'glueA' });
      await visuals?.focus([a.id, b.id]);
      await visuals?.move(b.id, { x: a.position.x + NODE_SURFACE_SIZE.preview.width, y: a.position.y }, signal);
      ensureActive(signal);
      const end = beginGlueEdit();
      try {
        const boxes = Object.fromEntries([requireCard('glueA'), requireCard('glueB')].map(card => [card.id, {
          ...positionSurfaceAtNodeCenter(card.position, 'preview'), ...NODE_SURFACE_SIZE.preview, level: 'preview',
        }])) as Record<string, GlueBox>;
        useGlueStore.getState().setLayout(boxes, { a: a.id, b: b.id, side: 'right' });
        await persistGlue();
        // The same authoritative positions and glue layout used by a canvas drag.
        await visuals?.move(a.id, { x: a.position.x + 35, y: a.position.y - 55 }, signal);
      } finally { end(); }
      break;
    }
    case 'unglue': {
      const a = requireCard('glueA'), b = requireCard('glueB');
      const end = beginGlueEdit();
      try {
        useGlueStore.getState().detach(a.id);
        await persistGlue([a.id]);
        await visuals?.move(b.id, { x: a.position.x + 390, y: a.position.y }, signal);
      } finally { end(); }
      break;
    }

  }
}

export function disposableDemos(demos: DemoRecord[], snapshot: WorldSnapshot, bonds: { a: string; b: string }[]) {
  const owned = new Set(demos.map(demo => demo.id));
  return demos.filter(demo => {
    const card = snapshot.nodes.find(item => item.id === demo.id);
    return card && card.type === 'text' && card.name === demo.name && card.created_at === demo.created_at
      && !card.parent_id && !card.equipment && Number(card.config.revision ?? 0) === (demo.contentRevision ?? 0) && !String(card.config.content ?? '')
      && !snapshot.nodes.some(item => item.parent_id === card.id || item.equipment?.owner_id === card.id)
      && !snapshot.edges.some(edge => edge.source === card.id || edge.target === card.id)
      && !bonds.some(bond => (bond.a === card.id || bond.b === card.id) && (!owned.has(bond.a) || !owned.has(bond.b)));
  });
}
async function cleanup() {
  const demos = state().session?.demos ?? [];
  if (!demos.length) return;
  const snapshot = await worldApi.getWorld();
  const glue = await worldApi.getGlue();
  const safe = disposableDemos(demos, snapshot, glue.bonds);
  const ids = new Set(safe.map(demo => demo.id));
  const retained = demos.filter(demo => snapshot.nodes.some(card => card.id === demo.id) && !ids.has(demo.id));
  if (ids.size) {
    useWorldStore.setState(s => ({ cards: mergeCards(s.cards, snapshot.nodes.filter(card => ids.has(card.id)), s.cardTombstones) }));
    await world().deleteCards([...ids]);
    const remaining = await worldApi.getWorld();
    const failed = safe.filter(demo => remaining.nodes.some(card => card.id === demo.id));
    saveSession({ demos: failed });
    for (const id of ids) if (!failed.some(demo => demo.id === id)) useNodeSurfaceStore.getState().dismiss(id);
    if (failed.length) throw new Error('Some props could not be removed. Your progress is saved; reconnect and retry cleanup.');
  }
  saveSession({ demos: [] });
  if (retained.length) world().pushToast({ tone: 'neutral', title: 'Kept your changed tutorial cards', detail: 'Props you edited, connected, or attached to your own cards belong to your world now.' });
}

/** Persist the result before waiting, so reload/resume never replays a finished gesture. */
async function finishDemonstration(signal: AbortSignal) {
  ensureActive(signal);
  const step = currentStep();
  if (!step.result) { goNext(); return; }
  saveSession({ completedDemo: step.id });
  useTutorialStore.setState({ target: step.result.target });
  await new Promise<void>((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, 700);
    signal.addEventListener('abort', cancel, { once: true });
  });
  ensureActive(signal);
}

function runTask(work: (signal: AbortSignal) => Promise<void>) {
  if (state().busy || stopping) return task;
  abort = new AbortController();
  const signal = abort.signal;
  useTutorialStore.setState({ busy: true, error: undefined });
  task = work(signal)
    .catch(error => { if (!signal.aborted) useTutorialStore.setState({ error: apiErrorMessage(error) }); })
    .finally(() => { task = undefined; abort = undefined; if (!stopping) { useTutorialStore.setState({ busy: false }); observe(); } });
  return task;
}

export const tutorial = {
  checkWelcome,
  attach(bridge: GuideVisuals) {
    visuals = bridge;
    const unsubscribers = [useWorldStore.subscribe(() => {
      if (state().view === 'welcome' && !state().busy && world().cards.length) void tutorial.directly();
      observe(); void checkWelcome();
    }),
      useNodeSurfaceStore.subscribe(() => observe()), useCardLibrary.subscribe(() => observe()),
      useLegionWorkspace.subscribe((next, previous) => observe(previous.activeId && !next.activeId
        ? { type: 'legion-workspace-closed', cardId: previous.activeId } : undefined)), observeInteractions(observe)];
    void checkWelcome();
    return () => { unsubscribers.forEach(off => off()); if (visuals === bridge) visuals = undefined; };
  },
  async start() {
    if (state().busy || stopping) return;
    useTutorialStore.setState({ busy: true, error: undefined, ready: false });
    try {
      const snapshot = await worldApi.getWorld();
      useTutorialStore.setState({ status: 'started', view: 'active', session: {
        id: crypto.randomUUID(), step: 'enter', initialIds: snapshot.nodes.map(card => card.id), refs: {}, demos: [],
      } });
      rebase();
    } catch (error) { useTutorialStore.setState({ error: apiErrorMessage(error) }); }
    finally { useTutorialStore.setState({ busy: false }); }
  },
  pause() { if (!state().busy) useTutorialStore.setState({ view: 'paused' }); },
  resume() { useTutorialStore.setState({ view: 'active', error: undefined }); rebase(); observe(); },
  async replay() {
    if (state().busy || stopping) return;
    if (state().session) {
      await tutorial.exit('skipped');
      if (state().session) return;
    }
    await tutorial.start();
  },
  async directly() {
    useTutorialStore.setState({ status: 'skipped', view: 'hidden', error: undefined });
  },
  async fromBlueprint(blueprint: LegionSummary, preset: boolean) {
    if (state().busy) return;
    useTutorialStore.setState({ busy: true, error: undefined });
    try {
      await prepareDeck(['agent', 'conversation', 'text', 'sandbox']);
      const instance = await world().instantiateLegion(blueprint.id, undefined, { blueprint, preset, unwrap: true });
      if (!instance) throw new Error('The blueprint could not be placed. Check the notification and retry.');
      const agent = instance.nodes.find(card => card.type === 'agent');
      const conversation = instance.nodes.find(card => card.type === 'conversation');
      useTutorialStore.setState({ status: 'skipped', view: 'hidden',
        quickStart: agent && conversation ? { agentId: agent.id, conversationId: conversation.id } : undefined });
      await visuals?.focus(instance.nodes.map(card => card.id));
      if (agent && !hasModelConfiguration(world().modelCatalog, agent.config.model)) useWorldStore.setState({ settingsOpen: true });
    } catch (error) { useTutorialStore.setState({ error: apiErrorMessage(error) }); }
    finally { useTutorialStore.setState({ busy: false }); }
  },
  async continue() {
    if (state().busy || stopping) return;
    const step = currentStep();
    if (state().session?.completedDemo === step.id) { goNext(); return; }
    if (step.id === 'deck-build') {
      if (!baseline || !stepComplete(step, state().session!.refs, baseline, observation())) return;
      return runTask(async signal => {
        const library = useCardLibrary.getState();
        const id = library.snapshot!.active_deck_id;
        if (!await library.edit({ action: 'activate_deck', id })) throw new Error(library.error || 'Your deck could not be activated. Retry.');
        ensureActive(signal); library.close(); goNext();
      });
    }
    if (step.id === 'minister') { if (state().ready) goNext(); return; }
    if (step.id === 'finish') return tutorial.exit('completed');
    if (step.id === 'workflow') await visuals?.focus(state().session?.refs.demo ? [state().session!.refs.demo!] : []);
    if (step.action) return tutorial.perform(step.action);
    if (!step.expects || step.optional || (step.review && state().ready)) goNext();
  },
  perform(action: Demonstration) {
    if (state().session?.completedDemo === currentStep().id) return;
    return runTask(async signal => { await demonstrate(action, signal); await finishDemonstration(signal); });
  },
  async exit(status: 'skipped' | 'completed') {
    if (stopping) return;
    stopping = true;
    abort?.abort();
    useTutorialStore.setState({ busy: true, error: undefined });
    await task;
    try {
      await cleanup();
      useTutorialStore.setState({ status, session: undefined, view: 'hidden', target: undefined, ready: false });
      if (!hasDefaultModelConfiguration(world().modelCatalog)) {
        useWorldStore.setState({ settingsOpen: true });
        world().pushToast({ tone: 'neutral', title: t('Set up your default model'),
          detail: t('Add your API key, check the Base URL, and choose a default model in Settings before using your agents.') });
      }
    } catch (error) { useTutorialStore.setState({ view: 'paused', error: apiErrorMessage(error) }); }
    finally { stopping = false; useTutorialStore.setState({ busy: false }); }
  },
  recover() {
    return runTask(async signal => {
      const step = currentStep(), role = step.role;
      if ((step.id === 'minister' && !cardFor('ministerRole') && !cardFor('agent')?.minister) || step.id === 'minister-card') {
        await prepareDeck(['core.minister-role']); ensureActive(signal);
        saveSession({ step: 'minister-card' }); rebase();
        if (state().session?.refs.agent) await visuals?.focus([state().session!.refs.agent!], true);
        return;
      }
      // Refresh missing subjects from the authoritative world before deciding a
      // replacement is necessary. Panning/culling is not deletion.
      const needed: Role[] = step.id.startsWith('legion-') ? ['agent', 'conversation', 'sandbox', ...(step.id === 'legion-form' ? [] : ['legion' as const])]
        : step.action === 'connect' || step.expects === 'connect' ? ['agent', step.action === 'connect' ? 'conversation' : 'sandbox']
        : ['glue', 'unglue'].includes(step.action ?? '') || step.id.startsWith('glue') ? ['glueA', 'glueB'] : role ? [role] : [];
      if (needed.some(item => state().session?.refs[item] && !cardFor(item))) {
        const snapshot = await worldApi.getWorld();
        ensureActive(signal);
        const ids = new Set(needed.map(item => state().session?.refs[item]));
        useWorldStore.setState(s => ({ cards: mergeCards(s.cards, snapshot.nodes.filter(card => ids.has(card.id)), s.cardTombstones) }));
      }
      const absent = needed.find(item => !cardFor(item));
      if (step.id.startsWith('legion-') && (!absent || absent === 'legion')) {
        if (absent === 'legion') { saveSession({ step: 'legion-form' }); rebase(); }
        if (!cardFor('legion') || step.id === 'legion-form') await visuals?.focus(['agent', 'conversation', 'sandbox'].map(role => requireCard(role as Role).id));
        else if (['legion-layout', 'legion-return'].includes(step.id)) useLegionWorkspace.getState().open(cardFor('legion')!.id);
        else await visuals?.focus([cardFor('legion')!.id]);
        return;
      }
      if (absent && absent !== role) {
        const placement = STEPS.find(item => item.expects === 'place' && item.role === absent);
        saveSession({ step: placement?.id ?? (absent === 'minister' ? 'minister' : 'glue-demo') });
        useTutorialStore.setState({ error: undefined });
        rebase(); return;
      }
      if (step.action && state().session?.completedDemo !== step.id) { await demonstrate(step.action, signal); await finishDemonstration(signal); return; }
      if (step.result && state().session?.completedDemo === step.id) {
        const roles = step.participants ?? [step.result.target as Role];
        await visuals?.focus(roles.flatMap(role => cardFor(role) ? [cardFor(role)!.id] : []));
        return;
      }
      if (role && !cardFor(role)) {
        // Return to the actual placement step; never manufacture user completion.
        const placement = STEPS.find(item => item.expects === 'place' && item.role === role);
        if (placement) { saveSession({ step: placement.id }); rebase(); return; }
        if (role === 'glueA' || role === 'glueB') { saveSession({ step: 'glue-demo' }); rebase(); return; }
        if (role === 'minister') { saveSession({ step: 'minister' }); rebase(); return; }
      }
      if (role) {
        const card = cardFor(role);
        if (card) {
          if (['configure', 'open'].includes(step.expects ?? '')) useNodeSurfaceStore.getState().openInspector(card.id);
          await visuals?.focus([card.id]);
        }
      }
      if (step.expects === 'connect') await visuals?.focus([requireCard('agent').id, requireCard('sandbox').id]);
      if (step.expects === 'place') await prepareDeck([TYPES[role!]]).catch(error => useTutorialStore.setState({ error: apiErrorMessage(error) }));
    });
  },
};
