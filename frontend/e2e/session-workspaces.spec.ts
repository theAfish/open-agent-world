import { expect, test } from '@playwright/test';

test('session and chat switches restore only their workspace, members and generated edges', async ({ page, request }) => {
  await page.setViewportSize({ width: 2200, height: 1500 });
  const create = async (type: string, name: string, x: number, y: number, extra = {}) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y }, ...extra } });
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const room = await create('conversation', 'Research chat', 600, 450);
  const otherRoom = await create('conversation', 'Other chat', 600, 1250);
  const box = await create('oaw.barracks', 'Source', -1200, 300);
  const template = await create('agent', 'Worker', -1000, 450, { parent_id: box.id });
  const session = async (roomId: string, title: string, groupId?: string) => {
    const response = await request.post(`/api/conversations/${roomId}/sessions`, { data: { title, group_title: 'Research', group_id: groupId } });
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const first = await session(room.id, 'Topic A');
  const second = await session(room.id, 'Topic B', first.group_id);
  const third = await session(otherRoom.id, 'Topic C');
  const instances: { id: string; workspace_id: string; entry_agent_id: string }[] = [];
  try {
    // The backend suite covers real session admission and reuse. Here real saved
    // summons are scoped as historical fixtures to exercise the browser projection.
    for (const [owner, topic] of [[room, first], [room, second], [otherRoom, third]]) {
      const response = await request.post(`/api/nodes/${box.id}/summoning/actions`, { data: {
        action: 'summon', agent_id: template.id, prompt: topic.title,
      } });
      expect(response.ok()).toBeTruthy();
      const instance = await response.json();
      instances.push(instance);
      expect((await request.patch(`/api/nodes/${instance.workspace_id}`, { data: {
        name: `${topic.title} team`, position: { x: 1200, y: 100 },
        config: { conversation_id: owner.id, session_id: topic.id },
      } })).ok()).toBeTruthy();
    }
    const profile = await (await request.get('/api/application')).json();
    expect((await request.patch('/api/application/preferences', { data: {
      profile_id: profile.profile_id, generation: profile.generation, changes: {
        'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
        'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null,
        'oaw-conversation-view-v1': JSON.stringify({ version: 0, state: {
          activeConversationId: room.id, sessions: { [room.id]: first.id, [otherRoom.id]: third.id },
        } }),
      },
    } })).ok()).toBeTruthy();
    await page.goto('/');
    const chat = page.locator(`[data-workspace-node-id="${room.id}"]`);
    const otherChat = page.locator(`[data-workspace-node-id="${otherRoom.id}"]`);
    const workspace = (index: number) => page.locator(`[data-card-id="${instances[index].workspace_id}"]`);
    const worker = (index: number) => page.locator(`[data-card-id="${instances[index].entry_agent_id}"]`);
    const assertScope = async (index: number) => {
      for (let i = 0; i < instances.length; i++) {
        await expect(workspace(i)).toHaveCount(i === index ? 1 : 0);
        await expect(worker(i)).toHaveCount(i === index ? 1 : 0);
        await expect(page.locator(`.semantic-edge-path.is-generated[data-target-id="${instances[i].workspace_id}"]`)).toHaveCount(i === index ? 1 : 0);
      }
    };
    await assertScope(0);
    const saved = (await (await request.get(`/api/nodes/${instances[0].entry_agent_id}`)).json()).position;
    await chat.getByRole('button', { name: /^Topic B/ }).click();
    await assertScope(1);
    // A background update for an inactive session must not reveal that workspace.
    await request.patch(`/api/nodes/${instances[0].entry_agent_id}`, { data: { name: 'Updated in background' } });
    await assertScope(1);
    await chat.getByRole('button', { name: /^Topic A/ }).click();
    await assertScope(0);
    expect((await (await request.get(`/api/nodes/${instances[0].entry_agent_id}`)).json()).position).toEqual(saved);
    await otherChat.getByRole('button', { name: /^Topic C/ }).click();
    await assertScope(2);
    await chat.getByRole('button', { name: /^Topic A/ }).click();
    await assertScope(0);
    await expect.poll(async () => {
      const preferences = await (await request.get('/api/application')).json();
      return JSON.parse(preferences.values['oaw-conversation-view-v1']).state.activeConversationId;
    }).toBe(room.id);
    await page.reload();
    await assertScope(0);
    await expect(chat.getByRole('button', { name: /^Topic A/ })).toHaveAttribute('aria-current', 'true');
    await expect.poll(async () => {
      const frame = await workspace(0).boundingBox(), member = await worker(0).boundingBox();
      return !!frame && !!member && member.x >= frame.x && member.y >= frame.y
        && member.x + member.width <= frame.x + frame.width && member.y + member.height <= frame.y + frame.height;
    }).toBeTruthy();
    await page.screenshot({ path: '../.outputs/session-workspaces.png' });
  } finally {
    for (const instance of instances) await request.post(`/api/nodes/${box.id}/summoning/actions`, {
      data: { action: 'reclaim', instance_id: instance.id },
    });
    expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: [room.id, otherRoom.id, template.id, box.id] } })).ok()).toBeTruthy();
  }
});
