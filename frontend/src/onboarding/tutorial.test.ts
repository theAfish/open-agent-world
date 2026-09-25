// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { useCardLibrary, type LibrarySnapshot } from '../state/cardLibrary';
import { useGlueStore } from '../state/glue';
import { reportInteraction } from '../state/interactions';
import { buildCardDraft } from '../state/helpers';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { disposableDemos, tutorial, useTutorialStore, type GuideVisuals } from './controller';
import { STEPS, stepComplete, type Observation } from './steps';
import type { WorldCard, WorldSnapshot } from '../types/world';

const card = (id: string, type = 'text'): WorldCard => ({ id, ...buildCardDraft(type, { x: 40, y: 80 }), created_at: '2026-09-12T00:00:00Z' });
const snapshot = (nodes: WorldCard[] = []): WorldSnapshot => ({ nodes, edges: [], chunks: [] });
const viewport = { x: 0, y: 0, zoom: 1, width: 1280, height: 800 };
const base = { viewport, position: { x: 40, y: 80 } };
const observation = (patch: Partial<Observation> = {}): Observation => ({ cards: [], edges: [], selected: [], surfaces: {}, bonds: [], viewport, settled: true, deleted: [], ...patch });
const bridge: GuideVisuals = { focus: async () => {}, place: async () => {}, move: async () => {}, connect: async () => {} };
let detach: (() => void) | undefined;

beforeEach(() => {
  vi.restoreAllMocks();
  useCardLibrary.setState({ snapshot: null, open: false, busy: false, tab: "packs" });
  useTutorialStore.setState({ status: 'new', view: 'hidden', session: undefined, error: undefined, busy: false, ready: false });
  useWorldStore.setState({ cards: [], edges: [], catalog: TEST_CATALOG, syncState: 'online', stressCards: [], viewport,
    modelCatalog: { revision: 0, connections: [], default_model: null }, settingsOpen: false,
    historyBusy: false, positionCommitBusy: false, undoStack: [], redoStack: [], cardTombstones: {}, toasts: [], selectedCardIds: [] });
  useNodeSurfaceStore.setState({ surfaceLevels: {}, dragging: false });
  useLegionWorkspace.setState({ activeId: undefined });
  useGlueStore.setState({ boxes: {}, bonds: [], activeEdits: 0 });
  vi.spyOn(worldApi, 'getWorld').mockResolvedValue(snapshot());
  vi.spyOn(worldApi, 'getGlue').mockResolvedValue({ revision: 1, boxes: {}, bonds: [] });
});
afterEach(() => { detach?.(); detach = undefined; });

describe('tutorial progression', () => {
  it('recovers a dissolved Legion by returning to the surviving workflow cards', async () => {
    const members = [card('a', 'agent'), card('c', 'conversation'), card('s', 'sandbox')];
    useWorldStore.setState({ cards: members });
    vi.mocked(worldApi.getWorld).mockResolvedValue(snapshot(members));
    useTutorialStore.setState({ status: 'started', view: 'active', session: {
      id: 'recover-legion', step: 'legion-layout', initialIds: [], demos: [], refs: { agent: 'a', conversation: 'c', sandbox: 's', legion: 'gone' },
    } });
    const focus = vi.fn(async () => {});
    tutorial.resume(); detach = tutorial.attach({ ...bridge, focus });
    await tutorial.recover();
    expect(useTutorialStore.getState().session?.step).toBe('legion-form');
    expect(focus).toHaveBeenCalledWith(['a', 'c', 's']);
    expect(useWorldStore.getState().cards).toEqual(members);
    expect(useTutorialStore.getState().session?.demos).toEqual([]);
  });

  it('requires the workflow members, a persisted layout and an explicit workspace close', async () => {
    const members = [card('a', 'agent'), card('c', 'conversation'), card('s', 'sandbox')];
    const group = card('g', 'legion');
    useWorldStore.setState({ cards: [...members, group] });
    useTutorialStore.setState({ status: 'started', view: 'active', session: {
      id: 'legion', step: 'legion-form', initialIds: [], demos: [], refs: { agent: 'a', conversation: 'c', sandbox: 's' },
    } });
    tutorial.resume(); detach = tutorial.attach(bridge);
    useWorldStore.setState({ cards: [...members.map((member, index) => index < 2 ? { ...member, parent_id: 'g' } : member), group] });
    expect(useTutorialStore.getState().session?.step).toBe('legion-form');
    useWorldStore.setState({ cards: [...members.map(member => ({ ...member, parent_id: 'g' })), group] });
    expect(useTutorialStore.getState().session?.step).toBe('legion-open');
    useLegionWorkspace.getState().open('unrelated');
    expect(useTutorialStore.getState().session?.step).toBe('legion-open');
    useLegionWorkspace.getState().open('g');
    expect(useTutorialStore.getState().session?.step).toBe('legion-layout');
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('legion-layout');
    const layout = { version: 2, hidden_sections: [], root: { kind: 'split', axis: 'horizontal', ratio: .5,
      first: { kind: 'pane', view: { card_id: 'c' } }, second: { kind: 'pane', view: { card_id: 's' } } } };
    useWorldStore.setState(state => ({ cards: state.cards.map(item => item.id === 'g' ? { ...item, config: { workspace_layout: layout } } : item) }));
    expect(useTutorialStore.getState().session?.step).toBe('legion-return');
    expect(stepComplete(STEPS.find(step => step.id === 'legion-return')!, { legion: 'g' }, base, observation({ cards: [group] }))).toBe(false);
    useLegionWorkspace.getState().close();
    expect(useTutorialStore.getState().session?.step).toBe('finish');
  });

  it('requires a visible workspace and explicit Continue for window introductions', async () => {
    useWorldStore.setState({ cards: [card('room', 'conversation')] });
    useTutorialStore.setState({ status: 'started', view: 'active', session: {
      id: 'window-intro', step: 'conversation-open', initialIds: [], demos: [], refs: { conversation: 'room' },
    } });
    tutorial.resume(); detach = tutorial.attach(bridge);
    expect(useTutorialStore.getState().ready).toBe(true);
    expect(useTutorialStore.getState().session?.step).toBe('conversation-open');
    useNodeSurfaceStore.getState().closeWorkspace('room');
    expect(useTutorialStore.getState().ready).toBe(false);
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('conversation-open');
    useNodeSurfaceStore.getState().openPrimary('room');
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('message');
  });

  it('waits for all four cards in the chosen deck and activates only that deck', async () => {
    const entries = ['text', 'agent', 'conversation', 'sandbox'].map(id => ({ kind: 'node' as const, id }));
    const library: LibrarySnapshot = { schema_version: 1, revision: 1, migration_pending: false, plugins: {}, packs: {}, card_definitions: {}, collection: {},
      decks: [{ id: 'starter', name: 'My deck', icon: 'folder', entries: [] }, { id: 'chosen', name: 'Research', icon: 'layers', entries: entries.slice(0, 3) }],
      active_deck_id: 'chosen', available_card_ids: entries.map(entry => entry.id), available_pack_ids: [] };
    useTutorialStore.setState({ status: 'started', view: 'active', session: { id: 'deck', step: 'deck-build', initialIds: [], refs: {}, demos: [] } });
    useCardLibrary.setState({ snapshot: library, open: true, tab: 'cards' });
    tutorial.resume(); detach = tutorial.attach(bridge);
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('deck-build');
    const complete = { ...library, decks: library.decks.map(deck => deck.id === 'chosen' ? { ...deck, entries } : deck) };
    useCardLibrary.setState({ snapshot: complete });
    expect(useTutorialStore.getState().ready).toBe(true);
    useCardLibrary.setState({ snapshot: { ...complete, active_deck_id: 'starter' } });
    expect(useTutorialStore.getState().ready).toBe(false);
    useCardLibrary.setState({ snapshot: complete });
    const edit = vi.spyOn(worldApi, 'editCardLibrary').mockResolvedValue({ ...complete, revision: 2, active_deck_id: 'chosen' });
    await tutorial.continue();
    expect(edit).toHaveBeenCalledWith(expect.objectContaining({ action: 'activate_deck', id: 'chosen' }));
    expect(useTutorialStore.getState().session?.step).toBe('place-demo');
    expect(useCardLibrary.getState().open).toBe(false);
    expect(useCardLibrary.getState().snapshot?.decks[0].entries).toEqual([]);
  });

  it('waits for settings and a successful model save before returning to the Agent', async () => {
    useWorldStore.setState({ settingsOpen: false });
    useTutorialStore.setState({ status: 'started', view: 'active', session: {
      id: 'models', step: 'model-settings', initialIds: [], demos: [], refs: {},
    } });
    tutorial.resume();
    detach = tutorial.attach(bridge);
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('model-settings');
    useWorldStore.getState().toggleSettings();
    expect(useTutorialStore.getState().session?.step).toBe('model-connection');
    reportInteraction({ type: 'model-connection-selected' });
    expect(useTutorialStore.getState().session?.step).toBe('model-credentials');
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('model-list');
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('model-save');
    useWorldStore.setState({ settingsOpen: false });
    expect(useTutorialStore.getState().session?.step).toBe('model-save');
    reportInteraction({ type: 'models-saved' });
    expect(useTutorialStore.getState().session?.step).toBe('configure');
  });

  it('closes settings when model setup is explicitly deferred', async () => {
    useWorldStore.setState({ settingsOpen: true });
    useTutorialStore.setState({ status: 'started', view: 'active', session: {
      id: 'models', step: 'model-save', initialIds: [], demos: [], refs: {},
    } });
    tutorial.resume();
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('configure');
    expect(useWorldStore.getState().settingsOpen).toBe(false);
  });

  it('does not re-enter a completed step when closing its surfaces publishes synchronously', async () => {
    const agent = card('agent', 'agent');
    useWorldStore.setState({ cards: [card('demo'), agent] });
    useTutorialStore.setState({ status: 'started', view: 'active', session: {
      id: 'run', step: 'configure', initialIds: [], demos: [], refs: { demo: 'demo', agent: 'agent' },
    } });
    tutorial.resume();
    detach = tutorial.attach(bridge);
    useNodeSurfaceStore.getState().openInspector('agent');
    useWorldStore.setState({ cards: [card('demo'), { ...agent, config: { ...agent.config, system_instruction: 'My garden' } }] });
    expect(useTutorialStore.getState().session?.step).toBe('configure');
    expect(useTutorialStore.getState().ready).toBe(true);
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('conversation');
    expect(useNodeSurfaceStore.getState().surfaceLevels.agent).toBe('preview');
  });
  it('requires a user viewport gesture, a successful send, and the right card identities', () => {
    const pan = STEPS.find(step => step.id === 'pan')!;
    expect(stepComplete(pan, {}, base, observation({ viewport: { ...viewport, x: 100 } }))).toBe(false);
    expect(stepComplete(pan, {}, base, observation(), { type: 'viewport', x: 100, y: 0, zoom: 1 })).toBe(true);
    const message = STEPS.find(step => step.id === 'message')!;
    expect(stepComplete(message, { conversation: 'room' }, base, observation(), { type: 'message-sent', cardId: 'elsewhere', conversationId: 'elsewhere' })).toBe(false);
    expect(stepComplete(message, { conversation: 'room' }, base, observation(), { type: 'message-sent', cardId: 'room', conversationId: 'room' })).toBe(true);
  });
  it('does not advance during an optimistic move or treat unloaded cards as deleted', () => {
    const moved = { ...card('practice'), position: { x: 150, y: 80 } };
    const step = STEPS.find(item => item.id === 'move')!;
    expect(stepComplete(step, { practice: 'practice' }, base, observation({ cards: [moved], settled: false }))).toBe(false);
    expect(stepComplete(step, { practice: 'practice' }, base, observation({ cards: [moved] }))).toBe(true);
    const remove = STEPS.find(item => item.id === 'delete')!;
    expect(stepComplete(remove, { practice: 'practice' }, base, observation())).toBe(false);
    expect(stepComplete(remove, { practice: 'practice' }, base, observation({ deleted: ['practice'] }))).toBe(true);
  });
  it('requires a saved glue seam between both props and a real execution capability', () => {
    const step = STEPS.find(item => item.id === 'glue')!;
    const refs = { glueA: 'a', glueB: 'b' };
    const glued = observation({ bonds: [{ a: 'a', b: 'b', side: 'right' }] });
    expect(stepComplete(step, refs, base, glued)).toBe(false);
    expect(stepComplete(step, refs, base, glued, { type: 'glue-saved', bonds: glued.bonds })).toBe(true);
    // A previous queued save must not confirm a newer optimistic seam.
    expect(stepComplete(step, refs, base, glued, { type: 'glue-saved', bonds: [{ a: 'a', b: 'foreign' }] })).toBe(false);
    const connect = STEPS.find(item => item.id === 'sandbox-connect')!;
    const edges = [{ id: 'edge', source: 'agent', target: 'sandbox', relationship: 'read', direction: 'forward' as const }];
    expect(stepComplete(connect, { agent: 'agent', sandbox: 'sandbox' }, base, observation({ edges }))).toBe(false);
    expect(stepComplete(connect, { agent: 'agent', sandbox: 'sandbox' }, base, observation({ edges: [{ ...edges[0], relationship: 'execute' }] }))).toBe(true);
    expect(stepComplete(connect, { agent: 'agent', sandbox: 'sandbox' }, base, observation({ settled: false, edges: [{ ...edges[0], relationship: 'execute' }] }))).toBe(false);
  });
  it('subscribes to real stores, adopts new cards, and persists the next step', async () => {
    await tutorial.start();
    detach = tutorial.attach(bridge);
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('pan');
    reportInteraction({ type: 'viewport', x: 100, y: 0, zoom: 1 });
    expect(useTutorialStore.getState().session?.step).toBe('zoom');
    reportInteraction({ type: 'viewport', x: 100, y: 0, zoom: 1.2 });
    expect(useTutorialStore.getState().session?.step).toBe('deck');
    useTutorialStore.setState(s => ({ session: { ...s.session!, step: 'place' } }));
    tutorial.resume();
    useWorldStore.setState({ cards: [card('mine')] });
    expect(useTutorialStore.getState().session?.refs.practice).toBe('mine');
    expect(useTutorialStore.getState().session?.step).toBe('move');
    const saved = JSON.parse(localStorage.getItem('oaw-onboarding-v1')!);
    expect(saved.state.session.step).toBe('move');
    expect(saved.state).not.toHaveProperty('busy');
    expect(saved.state).not.toHaveProperty('config');
  });
});

describe('first-run persistence and ownership', () => {
  it('checks the whole world before showing a welcome over an empty viewport', async () => {
    vi.mocked(worldApi.getWorld).mockResolvedValue(snapshot([{ ...card('far'), position: { x: 100000, y: 100000 } }]));
    await tutorial.checkWelcome();
    expect(worldApi.getWorld).toHaveBeenCalledWith();
    expect(useTutorialStore.getState()).toMatchObject({ view: 'hidden', status: 'skipped' });
  });
  it('never presents onboarding offline or after a previous skip', async () => {
    useWorldStore.setState({ syncState: 'offline' });
    await tutorial.checkWelcome();
    expect(worldApi.getWorld).not.toHaveBeenCalled();
    useWorldStore.setState({ syncState: 'online' });
    await tutorial.directly();
    await tutorial.checkWelcome();
    expect(useTutorialStore.getState().view).toBe('hidden');
  });
  it('retains modified, connected, nested, replaced, and externally glued props', () => {
    const props = ['plain', 'edited', 'connected', 'nested', 'replaced', 'glued', 'renamed'].map(id => card(id));
    const demos = props.map(({ id, name, created_at }) => ({ id, name, created_at }));
    props[1].config = { ...props[1].config, revision: 1 };
    props[3].parent_id = 'user-container';
    props[4].created_at = '2026-09-13T00:00:00Z';
    props[6].name = 'My own note';
    const world = { ...snapshot(props), edges: [{ id: 'edge', source: 'connected', target: 'user', relationship: 'read', direction: 'forward' as const }] };
    expect(disposableDemos(demos, world, [{ a: 'glued', b: 'user' }]).map(item => item.id)).toEqual(['plain']);
  });
  it('stages placement before focusing and plays one flight before revealing the card', async () => {
    const demo = card('demo');
    await tutorial.start();
    useWorldStore.setState({ cards: [demo] });
    useTutorialStore.setState(s => ({ session: { ...s.session!, step: 'place-demo', refs: { demo: demo.id } } }));
    const order: string[] = [];
    let finish!: () => void;
    detach = tutorial.attach({ ...bridge,
      preparePlace: () => { order.push('hide'); return () => { order.push('reveal'); }; },
      focus: async () => { order.push('focus'); },
      place: async () => { order.push('flight'); await new Promise<void>(resolve => { finish = resolve; }); },
    });
    const action = tutorial.perform('place');
    await vi.waitFor(() => expect(order).toEqual(['hide', 'focus', 'flight']));
    const repeated = tutorial.perform('place');
    finish();
    await action; await repeated;
    expect(order).toEqual(['hide', 'focus', 'flight', 'reveal']);
    expect(useTutorialStore.getState().session).toMatchObject({ step: 'place-demo', completedDemo: 'place-demo' });
    expect(useTutorialStore.getState().busy).toBe(false);
    const saved = JSON.parse(localStorage.getItem('oaw-onboarding-v1')!).state;
    useTutorialStore.setState({ ...saved, view: 'paused', target: undefined });
    tutorial.resume();
    await tutorial.perform('place');
    expect(order).toEqual(['hide', 'focus', 'flight', 'reveal']);
    await tutorial.continue();
    expect(useTutorialStore.getState().session?.step).toBe('place');
    expect(useTutorialStore.getState().session?.completedDemo).toBeUndefined();
  });

  it('waits for in-flight creation before cleaning up a skipped demonstration', async () => {
    let resolve!: (card: WorldCard) => void;
    const created = card('late-prop');
    const reveal = vi.fn();
    const place = vi.fn(async () => {});
    detach = tutorial.attach({ ...bridge, preparePlace: () => reveal, place });
    vi.spyOn(worldApi, 'createNode').mockImplementation(() => new Promise(done => { resolve = done; }));
    vi.spyOn(worldApi, 'getTextContent').mockResolvedValue('');
    vi.spyOn(worldApi, 'deleteNode').mockResolvedValue(undefined);
    await tutorial.start();
    const action = tutorial.perform('place');
    await vi.waitFor(() => expect(resolve).toBeDefined());
    const skipped = tutorial.exit('skipped');
    vi.mocked(worldApi.getWorld).mockResolvedValueOnce(snapshot([created])).mockResolvedValue(snapshot());
    resolve(created);
    await action; await skipped;
    expect(worldApi.deleteNode).toHaveBeenCalledWith('late-prop');
    expect(reveal).toHaveBeenCalledOnce();
    expect(place).not.toHaveBeenCalled();
    expect(useTutorialStore.getState()).toMatchObject({ view: 'hidden', status: 'skipped', session: undefined });
  });
  it('preserves a cleanup ledger on deletion failure for a retry', async () => {
    const prop = card('prop');
    useTutorialStore.setState({ status: 'started', view: 'active', session: { id: 'run', step: 'finish', initialIds: [], refs: {}, demos: [{ id: prop.id, name: prop.name, created_at: prop.created_at }] } });
    vi.mocked(worldApi.getWorld).mockResolvedValue(snapshot([prop]));
    vi.spyOn(worldApi, 'getTextContent').mockResolvedValue('');
    vi.spyOn(worldApi, 'deleteNode').mockRejectedValue(new Error('offline'));
    await tutorial.exit('completed');
    expect(useTutorialStore.getState().view).toBe('paused');
    expect(useTutorialStore.getState().session?.demos).toHaveLength(1);
    expect(useTutorialStore.getState().status).toBe('started');
  });
});

it('opens settings after finishing without a configured default model', async () => {
  vi.spyOn(worldApi, 'getWorld').mockResolvedValue(snapshot());
  await tutorial.exit('completed');
  expect(useTutorialStore.getState().status).toBe('completed');
  expect(useWorldStore.getState().settingsOpen).toBe(true);
  expect(useWorldStore.getState().toasts.at(-1)?.title).toBe('Set up your default model');
});

it('keeps settings closed after finishing with a configured default model', async () => {
  vi.spyOn(worldApi, 'getWorld').mockResolvedValue(snapshot());
  useWorldStore.setState({ modelCatalog: { revision: 1, default_model: 'oaw:model:user', connections: [{
    id: 'user', name: 'User', adapter: 'openai', enabled: true, base_url: 'https://example.test/v1',
    auth_mode: 'api_key', api_key_configured: true, models: [{ id: 'user', name: 'User', model_id: 'custom', enabled: true }],
  }] } });
  await tutorial.exit('completed');
  expect(useWorldStore.getState().settingsOpen).toBe(false);
});
