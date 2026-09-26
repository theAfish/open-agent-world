// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { tutorial, useTutorialStore } from './controller';
import { useWorldStore } from '../state/worldStore';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { surfaceDraftKey, useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useConversationView } from '../state/conversationView';
import { worldApi } from '../api/client';
import { buildCardDraft } from '../state/helpers';
import type { ConversationSummary, ConversationSession } from '../types/world';

beforeEach(() => {
  useWorldStore.setState({ cards: [{ ...buildCardDraft('legion', { x: 0, y: 0 }), id: 'workspace', created_at: '' }] });
  useTutorialStore.setState({ busy: false, quickStart: { agentId: 'agent', conversationId: 'conversation', legionId: 'workspace', goal: 'Help me analyze this file' } });
  useNodeSurfaceStore.setState({ drafts: {} });
  useLegionWorkspace.setState({ activeId: undefined });
});
afterEach(() => vi.restoreAllMocks());

it('opens the workspace with the goal as an unsent session draft', async () => {
  vi.spyOn(worldApi, 'getConversation').mockResolvedValue({ sessions: [], agents: [{ id: 'agent', connected: true }] } as unknown as ConversationSummary);
  const create = vi.spyOn(worldApi, 'createConversationSession').mockResolvedValue({ id: 'session' } as ConversationSession);
  const send = vi.spyOn(worldApi, 'postConversationMessage');
  await tutorial.openQuickStart();
  expect(create).toHaveBeenCalledWith('conversation', expect.objectContaining({ participant_ids: ['agent'] }));
  expect(useLegionWorkspace.getState().activeId).toBe('workspace');
  expect(useConversationView.getState().sessions.conversation).toBe('session');
  expect(JSON.parse(useNodeSurfaceStore.getState().drafts[surfaceDraftKey('conversation', 'composer', 'session')])).toBe('Help me analyze this file');
  expect(send).not.toHaveBeenCalled();
});

it('reuses an existing session on retry and preserves a draft the user already edited', async () => {
  vi.spyOn(worldApi, 'getConversation').mockResolvedValue({ sessions: [{ id: 'existing' }], agents: [] } as unknown as ConversationSummary);
  const create = vi.spyOn(worldApi, 'createConversationSession');
  const key = surfaceDraftKey('conversation', 'composer', 'existing');
  useNodeSurfaceStore.getState().setDraft(key, JSON.stringify('Edited goal'));
  await tutorial.openQuickStart();
  expect(create).not.toHaveBeenCalled();
  expect(JSON.parse(useNodeSurfaceStore.getState().drafts[key])).toBe('Edited goal');
});

it('keeps deployment identity and goal after a failed session request', async () => {
  vi.spyOn(worldApi, 'getConversation').mockRejectedValue(new Error('offline'));
  await expect(tutorial.openQuickStart()).rejects.toThrow('offline');
  expect(useTutorialStore.getState().quickStart).toMatchObject({ legionId: 'workspace', goal: 'Help me analyze this file' });
});
