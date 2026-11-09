// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useWorldStore } from './worldStore';
import { useConversationView } from './conversationView';
import { useWorkspaceContext, type WorkspaceContext } from './workspaceContext';
import { buildCardDraft } from './helpers';
import { TEST_CATALOG } from './catalog.fixture';

const sandbox = { id: 'sandbox', ...buildCardDraft('sandbox', { x: 0, y: 0 }) };
const conversation = { id: 'room', ...buildCardDraft('conversation', { x: 0, y: 0 }) };
const agent = { id: 'agent', ...buildCardDraft('agent', { x: 0, y: 0 }) };
const value = (session: string, group = false): WorkspaceContext => ({
  key: group ? 'group-one' : session, directory: group ? 'groups/one' : `sessions/${session}`,
  label: session, scope_type: group ? 'group' : 'session', scope_id: group ? 'one' : session,
  session_id: session, group_id: 'one', conversation_id: 'room', sandbox_id: sandbox.id,
});
beforeEach(() => {
  useWorldStore.setState({ cards: [sandbox, conversation, agent], catalog: TEST_CATALOG, events: [], edges: [
    { id: 'execute', source: agent.id, target: sandbox.id, relationship: 'execute', direction: 'forward' },
    { id: 'join', source: agent.id, target: conversation.id, relationship: 'participate', direction: 'forward' },
  ] });
  useConversationView.setState({ activeConversationId: 'room', sessions: { room: 'a' } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('drops stale context responses on session switches and resolves group identity through the host', async () => {
  let finishA!: (value: WorkspaceContext) => void;
  const request = vi.spyOn(worldApi, 'workspaceContext').mockImplementationOnce(() => new Promise(resolve => { finishA = resolve; }))
    .mockResolvedValueOnce(value('b')).mockResolvedValue(value('b', true));
  const { result } = renderHook(() => useWorkspaceContext(sandbox.id));
  expect(result.current.value).toBeUndefined();
  act(() => useConversationView.setState({ sessions: { room: 'b' } }));
  await waitFor(() => expect(result.current.value?.key).toBe('b'));
  await act(async () => finishA(value('a')));
  expect(result.current.value?.key).toBe('b');
  act(() => useWorldStore.setState({ cards: [{ ...sandbox, config: { ...sandbox.config, workspace_scope: 'group' } }, conversation, agent] }));
  expect(result.current.value).toBeUndefined();
  await waitFor(() => expect(result.current.value?.key).toBe('group-one'));
  expect(request).toHaveBeenLastCalledWith(sandbox.id, 'b');
});

it('reports failed resolution without displaying the previous scope', async () => {
  const request = vi.spyOn(worldApi, 'workspaceContext').mockResolvedValueOnce(value('a')).mockRejectedValue(new Error('Missing session'));
  const { result } = renderHook(() => useWorkspaceContext(sandbox.id));
  await waitFor(() => expect(result.current.value?.key).toBe('a'));
  act(() => useConversationView.setState({ sessions: { room: 'deleted' } }));
  await waitFor(() => expect(result.current.error).toContain('Missing session'));
  expect(result.current.value).toBeUndefined();
  request.mockResolvedValue(value('restored'));
  act(() => result.current.reload());
  await waitFor(() => expect(result.current.value?.key).toBe('restored'));
});

it('refreshes renamed sessions and claimed command paths without discarding the current view while loading', async () => {
  let finish!: (context: WorkspaceContext) => void;
  const request = vi.spyOn(worldApi, 'workspaceContext').mockResolvedValueOnce({ ...value('a'), claimed: false })
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue({ ...value('a'), directory: 'Named (a)', claimed: true });
  const { result } = renderHook(() => useWorkspaceContext(sandbox.id));
  await waitFor(() => expect(result.current.value?.key).toBe('a'));
  act(() => useWorldStore.setState({ events: [{ id: 'rename', type: 'conversation_session_updated', session_id: 'a',
    conversation_id: 'room', timestamp: '2026-11-04T00:00:00Z', payload: {} }] }));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(result.current.value?.key).toBe('a');
  await act(async () => finish({ ...value('a'), directory: 'Named', claimed: false }));
  expect(result.current.value?.directory).toBe('Named');
  act(() => useWorldStore.setState({ events: [{ id: 'start', type: 'sandbox_command_started', sandbox_id: sandbox.id,
    timestamp: '2026-11-04T00:00:01Z', payload: {} }] }));
  await waitFor(() => expect(result.current.value?.directory).toBe('Named (a)'));
  expect(result.current.value?.claimed).toBe(true);
});
