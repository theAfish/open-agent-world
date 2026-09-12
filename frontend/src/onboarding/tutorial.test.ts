// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
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
  useTutorialStore.setState({ status: 'new', view: 'hidden', session: undefined, error: undefined, busy: false, ready: false });
  useWorldStore.setState({ cards: [], edges: [], catalog: TEST_CATALOG, syncState: 'online', stressCards: [], viewport,
    historyBusy: false, positionCommitBusy: false, undoStack: [], redoStack: [], cardTombstones: {}, toasts: [], selectedCardIds: [] });
  useNodeSurfaceStore.setState({ surfaceLevels: {}, dragging: false });
  useGlueStore.setState({ boxes: {}, bonds: [], activeEdits: 0 });
  vi.spyOn(worldApi, 'getWorld').mockResolvedValue(snapshot());
  vi.spyOn(worldApi, 'getGlue').mockResolvedValue({ revision: 1, boxes: {}, bonds: [] });
});
afterEach(() => { detach?.(); detach = undefined; });

describe('tutorial progression', () => {
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
    expect(useNodeSurfaceStore.getState().surfaceLevels.agent).toBeUndefined();
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
  it('waits for in-flight creation before cleaning up a skipped demonstration', async () => {
    let resolve!: (card: WorldCard) => void;
    const created = card('late-prop');
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
