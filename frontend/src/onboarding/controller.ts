import { create as createStore } from 'zustand';
import { persist } from 'zustand/middleware';
import { worldApi, apiErrorMessage } from '../api/client';
import { useWorldStore, mergeCards } from '../state/worldStore';
import { useCardLibrary } from '../state/cardLibrary';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { beginGlueEdit, persistGlue, useGlueStore, type GlueBox } from '../state/glue';
import { observeInteractions, type WorldInteraction } from '../state/interactions';
import { positionSurfaceAtNodeCenter } from '../canvas/nodeDisplacement';
import { getConnectionOptions } from '../state/relationships';
import { STEPS, stepComplete, type Baseline, type Demonstration, type Observation, type Role, type Target } from './steps';
import type { WorldCard, WorldSnapshot, WorldPosition } from '../types/world';

const TYPES: Record<Role, string> = { demo: 'text', practice: 'text', agent: 'agent', conversation: 'conversation', sandbox: 'sandbox', glueA: 'text', glueB: 'text', minister: 'core.minister' };
export interface DemoRecord { id: string; name: string; created_at?: string; contentRevision?: number }
export interface TutorialSession {
  id: string;
  step: string;
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
  celebration: number;
  ready: boolean;
}
export const useTutorialStore = createStore<TutorialState>()(persist((): TutorialState => ({
  status: 'new', view: 'hidden', busy: false, celebration: 0, ready: false,
}), {
  name: 'oaw-onboarding-v1', version: 1,
  partialize: ({ status, session }) => ({ status, session }),
  // Future incompatible step sequences resume at a reviewable entrance.
  merge: (saved, current) => {
    const value = saved as Partial<TutorialState> | undefined;
    const session = value?.session;
    return { ...current, status: value?.status ?? 'new', session: session && {
      ...session, step: STEPS.some(step => step.id === session.step) ? session.step : 'enter',
    } };
  },
}));

export interface GuideVisuals {
  findSpace?: (preferred: WorldPosition, size: { width: number; height: number }) => WorldPosition;
  place: (id: string, signal: AbortSignal) => Promise<void>;
  connect: (source: string, target: string, signal: AbortSignal) => Promise<void>;
  move: (id: string, position: WorldPosition, signal: AbortSignal) => Promise<void>;
  focus: (ids: string[]) => Promise<void>;
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
  if (session) useTutorialStore.setState({ session: { ...session, ...patch } });
}
function observation(): Observation {
  const w = world(), surfaces = useNodeSurfaceStore.getState();
  return { cards: w.cards, edges: w.edges, selected: w.selectedCardIds, surfaces: surfaces.surfaceLevels,
    bonds: useGlueStore.getState().bonds, viewport: w.viewport,
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
    if (['conversation', 'sandbox', 'glue-demo', 'minister'].includes(next.id)) {
      for (const id of Object.values(state().session?.refs ?? {})) useNodeSurfaceStore.getState().dismiss(id);
    }
    if (step.expects === 'connect') world().selectEdge(undefined);
    saveSession({ step: next.id });
    useTutorialStore.setState(s => ({ error: undefined, target: undefined, ready: false, celebration: s.celebration + (step.expects ? 1 : 0) }));
    rebase();
    if (next.expects === 'select' && next.role) {
      world().selectCards(world().selectedCardIds.filter(id => id !== state().session?.refs[next.role!]), { syncCanvas: true });
    }
  } finally { transitioning = false; }
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
  if (stepComplete(step, state().session!.refs, baseline, observation(), event)) {
    if (step.review) { if (!state().ready) useTutorialStore.setState({ ready: true }); }
    else goNext();
  }
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
    case 'deck': await prepareDeck(['text', 'agent', 'conversation', 'sandbox']); break;
    case 'place': {
      const preferred = center(-190, 50);
      const card = await create('demo', visuals?.findSpace?.(preferred, { width: 380, height: 240 }) ?? preferred, true, signal);
      useTutorialStore.setState({ target: 'demo' });
      await visuals?.focus([card.id]);
      await visuals?.place(card.id, signal);
      break;
    }
    case 'connect': {
      const agent = requireCard('agent'), conversation = requireCard('conversation');
      await visuals?.focus([agent.id, conversation.id]);
      if (world().edges.some(edge => edge.source === agent.id && edge.target === conversation.id && edge.relationship === 'participate')) break;
      const option = getConnectionOptions(world().catalog, agent.type, conversation.type).find(item => item.value === 'participate');
      if (!option) throw new Error('This catalog does not provide Participate for these cards. Check their plugins in the Library.');
      await visuals?.connect(agent.id, conversation.id, signal);
      ensureActive(signal);
      if (world().pendingConnection) throw new Error('Finish or close your current capability chooser, then retry this demonstration.');
      world().requestConnection(agent.id, conversation.id);
      await world().createConnection(option.value);
      if (!world().edges.some(edge => edge.source === agent.id && edge.target === conversation.id && edge.relationship === option.value)) throw new Error('The relationship was not saved. Retry after checking the capability chooser.');
      world().selectEdge(undefined);
      break;
    }
    case 'glue': {
      const preferred = center(-25, 20);
      const space = visuals?.findSpace?.(preferred, { width: 880, height: 350 }) ?? preferred;
      const a = await create('glueA', { x: space.x - 195, y: space.y }, true, signal);
      const b = await create('glueB', { x: a.position.x + 390, y: a.position.y }, true, signal);
      useTutorialStore.setState({ target: 'glueA' });
      await visuals?.focus([a.id, b.id]);
      await visuals?.move(b.id, { x: a.position.x + 286, y: a.position.y }, signal);
      ensureActive(signal);
      const end = beginGlueEdit();
      try {
        const boxes = Object.fromEntries([requireCard('glueA'), requireCard('glueB')].map(card => [card.id, {
          ...positionSurfaceAtNodeCenter(card.position, 'preview'), width: 286, height: 156, level: 'preview',
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
    case 'minister': {
      await prepareDeck(['core.minister']);
      ensureActive(signal);
      const preferred = center(-130, -30);
      // Leave room for the canvas composer and settings button as well as the orb.
      const position = visuals?.findSpace?.(preferred, { width: 480, height: 360 }) ?? preferred;
      const card = await create('minister', position, false, signal);
      await visuals?.focus([card.id]);
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
      useNodeSurfaceStore.subscribe(() => observe()), observeInteractions(observe)];
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
  async minister() {
    if (state().busy) return;
    useTutorialStore.setState({ busy: true, error: undefined });
    try {
      await prepareDeck(['core.minister']);
      const card = await world().createCard('core.minister', center(-120, -35));
      if (!card) throw new Error('The Minister could not be placed. Please retry.');
      useNodeSurfaceStore.getState().openInspector(card.id);
      useTutorialStore.setState({ status: 'skipped', view: 'hidden' });
    } catch (error) { useTutorialStore.setState({ error: apiErrorMessage(error) }); }
    finally { useTutorialStore.setState({ busy: false }); }
  },
  async continue() {
    if (state().busy || stopping) return;
    const step = currentStep();
    if (step.id === 'finish') return tutorial.exit('completed');
    if (step.id === 'workflow') await visuals?.focus(state().session?.refs.demo ? [state().session!.refs.demo!] : []);
    if (step.action) return tutorial.perform(step.action);
    if (!step.expects || step.optional) goNext();
  },
  perform(action: Demonstration) {
    return runTask(async signal => { await demonstrate(action, signal); ensureActive(signal); goNext(); });
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
    } catch (error) { useTutorialStore.setState({ view: 'paused', error: apiErrorMessage(error) }); }
    finally { stopping = false; useTutorialStore.setState({ busy: false }); }
  },
  recover() {
    return runTask(async signal => {
      const step = currentStep(), role = step.role;
      // Refresh missing subjects from the authoritative world before deciding a
      // replacement is necessary. Panning/culling is not deletion.
      const needed: Role[] = step.action === 'connect' || step.expects === 'connect' ? ['agent', step.action === 'connect' ? 'conversation' : 'sandbox']
        : ['glue', 'unglue'].includes(step.action ?? '') || step.id.startsWith('glue') ? ['glueA', 'glueB'] : role ? [role] : [];
      if (needed.some(item => state().session?.refs[item] && !cardFor(item))) {
        const snapshot = await worldApi.getWorld();
        ensureActive(signal);
        const ids = new Set(needed.map(item => state().session?.refs[item]));
        useWorldStore.setState(s => ({ cards: mergeCards(s.cards, snapshot.nodes.filter(card => ids.has(card.id)), s.cardTombstones) }));
      }
      const absent = needed.find(item => !cardFor(item));
      if (absent && absent !== role) {
        const placement = STEPS.find(item => item.expects === 'place' && item.role === absent);
        saveSession({ step: placement?.id ?? (absent === 'minister' ? 'minister' : 'glue-demo') });
        useTutorialStore.setState({ error: undefined });
        rebase(); return;
      }
      if (step.action) { await demonstrate(step.action, signal); ensureActive(signal); goNext(); return; }
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
