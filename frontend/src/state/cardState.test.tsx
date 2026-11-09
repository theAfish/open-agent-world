// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useWorldStore } from './worldStore';
import { useConversationView } from './conversationView';
import { TEST_CATALOG } from './catalog.fixture';
import { resolveCardStateSession, resolveViewingSession, useCardStateSession } from './cardState';
import { TaskBoardBody } from '../cards/TaskBoard';
import { clearTaskBoardCache } from '../cards/taskBoardCache';
import { useNodeSurfaceStore } from './nodeSurfaces';
import type { NodeTypeCatalogItem, WorldCard, WorldEdge } from '../types/world';

const board: WorldCard = { id: 'board', type: 'oaw.tasks', name: 'Tasks', status: 'available', config: { description: 'Same config' },
  state_scope: 'session', position: { x: 0, y: 0 }, size: { width: 360, height: 240 }, expanded: false };
const definition: NodeTypeCatalogItem = { ...TEST_CATALOG.node_types[0], id: board.type,
  state: { mode: 'scoped', supportedScopes: ['shared', 'session'], defaultScope: 'session', userConfigurable: false } };
const document = (title: string) => ({ value: { tasks: title ? [{ id: title, title, description: '', status: 'todo', depends_on: [], note: '' }] : [],
  execution: { default_executor_id: null, max_parallel: 1, pause_on_failure: true } }, revision: title ? 4 : 0,
  summary: { total: title ? 1 : 0, done: 0, ready_ids: title ? [title] : [] } });

afterEach(() => { cleanup(); vi.restoreAllMocks(); useConversationView.setState({ sessions: {}, activeConversationId: undefined }); });
beforeEach(() => { clearTaskBoardCache(); useNodeSurfaceStore.setState({ drafts: {} }); });

it('switches the same card A → B → A with isolated drafts and no scope UI', async () => {
  useWorldStore.setState({ cards: [board, { ...board, id: "chat", type: "conversation" }], catalog: { ...TEST_CATALOG, node_types: [definition] }, events: [] });
  useConversationView.setState({ activeConversationId: 'chat', sessions: { chat: 'A' } });
  vi.spyOn(worldApi, 'getNodeDocument').mockImplementation(async (_id, session) => document(session === 'A' ? 'Original task' : ''));
  vi.spyOn(worldApi, 'getNodeExecution').mockResolvedValue({ status: 'idle', active: false, error: null, executors: [], items: [], attempts: [] });
  const view = render(<TaskBoardBody card={board} />);
  await screen.findByRole('button', { name: 'Edit task Original task' });
  fireEvent.change(screen.getByLabelText('New task title'), { target: { value: 'Unsubmitted draft' } });
  const controls = view.container.querySelectorAll('button, select').length;
  act(() => useConversationView.getState().selectSession('chat', 'B'));
  await screen.findByText('A clear place to start.');
  expect(screen.queryByRole('button', { name: 'Edit task Original task' })).toBeNull();
  expect((screen.getByLabelText('New task title') as HTMLInputElement).value).toBe('');
  expect(view.container.textContent).not.toMatch(/Session|Shared across|Separate for|scope/);
  act(() => useConversationView.getState().selectSession('chat', 'A'));
  await screen.findByRole('button', { name: 'Edit task Original task' });
  expect(view.container.querySelectorAll('button, select').length).toBe(controls);
  expect((screen.getByLabelText('New task title') as HTMLInputElement).value).toBe('Unsubmitted draft');
  expect(board.config).toEqual({ description: 'Same config' });
});

it('ignores a late response from the previous session, even with a greater revision', async () => {
  useWorldStore.setState({ cards: [board, { ...board, id: "chat", type: "conversation" }], catalog: { ...TEST_CATALOG, node_types: [definition] }, events: [] });
  useConversationView.setState({ activeConversationId: 'chat', sessions: { chat: 'A' } });
  let resolveOld!: (value: ReturnType<typeof document>) => void;
  vi.spyOn(worldApi, 'getNodeDocument').mockImplementation((_id, session) => session === 'A'
    ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(document('')));
  vi.spyOn(worldApi, 'getNodeExecution').mockResolvedValue({ status: 'idle', active: false, error: null, executors: [], items: [], attempts: [] });
  render(<TaskBoardBody card={board} />);
  await waitFor(() => expect(resolveOld).toBeDefined());
  act(() => useConversationView.getState().selectSession('chat', 'B'));
  await screen.findByText('A clear place to start.');
  await act(async () => resolveOld(document('Late task')));
  expect(screen.queryByRole('button', { name: 'Edit task Late task' })).toBeNull();
});

it('resolves the owning workspace conversation before an unrelated active conversation', () => {
  const card = { ...board, parent_id: 'group' };
  const group = { ...board, id: 'group', type: 'legion' };
  const chat = { ...board, id: 'chat', type: 'conversation', parent_id: 'group' };
  const types = [definition, { ...definition, id: 'conversation', traits: ['core.conversation'] }];
  const view = { activeConversationId: 'other', sessions: { other: 'B', chat: 'A' } };
  expect(resolveCardStateSession(card.id, [card, group, chat], { ...TEST_CATALOG, node_types: types }, view)).toBe('A');
  expect(resolveCardStateSession(card.id, [card], { ...TEST_CATALOG, node_types: [{ ...definition, state: { mode: 'none' } }] }, view)).toBeUndefined();
  expect(resolveCardStateSession(card.id, [{ ...card, state_scope: 'shared' }], { ...TEST_CATALOG, node_types: types }, view)).toBeUndefined();
});

it('does not rerender a stateless card when unrelated cards or conversation selection change', () => {
  useWorldStore.setState({ cards: [board], catalog: { ...TEST_CATALOG, node_types: [{ ...definition, state: { mode: 'none' } }] } });
  const rendered = vi.fn();
  function Surface() { const session = useCardStateSession(board.id); rendered(); return <span>{session ?? 'shared'}</span>; }
  render(<Surface />);
  const initial = rendered.mock.calls.length;
  act(() => useWorldStore.setState({ cards: [board, { ...board, id: 'unrelated' }] }));
  act(() => useConversationView.getState().selectSession('other-chat', 'B'));
  expect(rendered).toHaveBeenCalledTimes(initial);
});

it('resolves only connected Conversation sources independently for each Sandbox', () => {
  const card = (id: string, type: string): WorldCard => ({ ...board, id, type });
  const cards = [card('a', 'agent'), card('b', 'agent'), card('s1', 'sandbox'), card('s2', 'sandbox'),
    card('c1', 'conversation'), card('c2', 'conversation'), card('unconnected', 'sandbox'), card('viewer', 'text')];
  const edge = (source: string, target: string, relationship: string): WorldEdge => ({ id: source + target, source, target, relationship, direction: 'forward' });
  const edges = [edge('a', 's1', 'execute'), edge('a', 'c1', 'participate'), edge('b', 's2', 'execute'), edge('b', 'c2', 'participate'),
    edge('a', 'viewer', 'read_edit'), edge('viewer', 's2', 'core.file-preview')];
  const view = { activeConversationId: 'c2', sessions: { c1: 'one', c2: 'two' } };
  const resolve = (id: string, links = edges) => resolveViewingSession(id, cards, TEST_CATALOG, view, links);
  expect(resolve('unconnected')).toBeUndefined();
  expect(resolve('s1')).toBe('one');
  expect(resolve('s2')).toBe('two');
  expect(resolve('viewer')).toBe('two');
  const shared = [...edges, edge('a', 'c2', 'participate')];
  expect(resolve('s1', shared)).toBe('two');
  expect(resolveViewingSession('s1', cards, TEST_CATALOG, { ...view, activeConversationId: 'other' }, shared)).toBeUndefined();
});

it('uses native default equipment relationships to retain the selected workspace session', () => {
  const card = (id: string, type: string): WorldCard => ({ ...board, id, type });
  const cards = [card('agent', 'agent'), card('viewer', 'text'),
    { ...card('sandbox', 'sandbox'), equipment: { owner_id: 'agent', relationship: null } },
    { ...card('room', 'conversation'), equipment: { owner_id: 'agent', relationship: null } }];
  const edges: WorldEdge[] = [{ id: 'preview', source: 'viewer', target: 'sandbox', relationship: 'core.file-preview', direction: 'forward' }];
  const view = { activeConversationId: 'room', sessions: { room: 'selected-session' } };
  for (const id of ['agent', 'sandbox', 'viewer']) {
    expect(resolveViewingSession(id, cards, TEST_CATALOG, view, edges)).toBe('selected-session');
  }
  const revoked = cards.map(node => node.id === 'room'
    ? { ...node, equipment: { owner_id: 'agent', relationship: 'communicate' } } : node);
  expect(resolveViewingSession('sandbox', revoked, TEST_CATALOG, view, edges)).toBeUndefined();
});
