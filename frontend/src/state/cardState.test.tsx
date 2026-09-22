// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useWorldStore } from './worldStore';
import { useConversationView } from './conversationView';
import { TEST_CATALOG } from './catalog.fixture';
import { resolveCardStateSession } from './cardState';
import { TaskBoardBody } from '../cards/TaskBoard';
import type { NodeTypeCatalogItem, WorldCard } from '../types/world';

const board: WorldCard = { id: 'board', type: 'oaw.tasks', name: 'Tasks', status: 'available', config: { description: 'Same config' },
  state_scope: 'session', position: { x: 0, y: 0 }, size: { width: 360, height: 240 }, expanded: false };
const definition: NodeTypeCatalogItem = { ...TEST_CATALOG.node_types[0], id: board.type,
  state: { mode: 'scoped', supportedScopes: ['shared', 'session'], defaultScope: 'session', userConfigurable: false } };
const document = (title: string) => ({ value: { tasks: title ? [{ id: title, title, description: '', status: 'todo', depends_on: [], note: '' }] : [],
  execution: { default_executor_id: null, max_parallel: 1, pause_on_failure: true } }, revision: title ? 4 : 0,
  summary: { total: title ? 1 : 0, done: 0, ready_ids: title ? [title] : [] } });

afterEach(() => { cleanup(); vi.restoreAllMocks(); useConversationView.setState({ sessions: {}, activeConversationId: undefined }); });

it('switches the same card A → B → A without retaining drafts or adding scope UI', async () => {
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
  expect(resolveCardStateSession(card.id, [card, group, chat], types, view)).toBe('A');
  expect(resolveCardStateSession(card.id, [card], [{ ...definition, state: { mode: 'none' } }], view)).toBeUndefined();
  expect(resolveCardStateSession(card.id, [{ ...card, state_scope: 'shared' }], types, view)).toBeUndefined();
});
