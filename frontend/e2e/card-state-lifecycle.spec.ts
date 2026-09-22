import { expect, test } from '@playwright/test';

test('research plans follow workspace sessions without session inputs or copied cards', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null, 'oaw-conversation-view-v1': null,
  } } });
  const create = async (type: string, name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 300 } } });
    expect(response.status()).toBe(201); return response.json();
  };
  const conversation = await create('conversation', 'Conversation', 300);
  const board = await create('matcreator.tasks', 'Research tasks', 700);
  const grouped = await request.post('/api/legion-groups', { data: { name: 'Research state studio', node_ids: [conversation.id, board.id] } });
  const group = (await grouped.json())[0];
  const pane = (id: string) => ({ kind: 'pane', view: { card_id: id } });
  await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2, hidden_sections: [], root: {
    kind: 'split', axis: 'horizontal', ratio: .5, first: pane(conversation.id), second: pane(board.id),
  } } } } });
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.locator(`[data-card-id="${group.id}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).click();
    const workspace = page.getByRole('dialog', { name: 'Research state studio workspace mode' });
    const tasks = workspace.getByRole('region', { name: 'Research task board', exact: true });
    const addPlan = async (title: string) => {
      await tasks.getByRole('button', { name: 'New plan', exact: true }).click();
      await expect(tasks.getByLabel('Session reference (optional)')).toHaveCount(0);
      await tasks.getByLabel('Plan title', { exact: true }).fill(title);
      await tasks.getByRole('button', { name: 'Create plan', exact: true }).click();
      await expect(tasks.getByRole('heading', { name: title, exact: true })).toBeVisible();
    };
    await addPlan('A copper study');
    await tasks.getByRole('button', { name: 'Add task', exact: true }).click();
    await tasks.getByLabel('Task title', { exact: true }).fill('A structure calculation');
    await tasks.getByRole('button', { name: 'Save task', exact: true }).click();
    await tasks.getByRole('button', { name: '‹ Tasks', exact: true }).click();
    await expect(tasks.getByRole('button', { name: 'A structure calculation', exact: true })).toBeVisible();
    const a = (await (await request.get(`/api/conversations/${conversation.id}`)).json()).sessions.find((s: { is_default: boolean }) => s.is_default);
    // A local draft must not follow the card into B.
    await tasks.getByRole('button', { name: 'A structure calculation', exact: true }).click();
    await tasks.getByRole('button', { name: 'Edit', exact: true }).click();
    await tasks.getByLabel('Task title', { exact: true }).fill('Unsaved A draft');
    await workspace.getByRole('button', { name: 'New session', exact: true }).click();
    await expect(tasks.getByText('Plan, execute, verify and learn.', { exact: true })).toBeVisible();
    await expect(tasks.getByLabel('Task title', { exact: true })).toHaveCount(0);
    await expect(tasks).not.toContainText(/Shared across sessions|Separate for each session|scope|Session/);
    await addPlan('B silicon study');
    await workspace.locator('button.workspace-session[title]').filter({ hasText: a.title }).click();
    await expect(tasks.getByRole('heading', { name: 'A copper study', exact: true })).toBeVisible();
    await expect(tasks.getByRole('button', { name: 'A structure calculation', exact: true })).toBeVisible();
    await expect(tasks).not.toContainText('B silicon study');
    const nodes = await (await request.get('/api/nodes')).json();
    expect(nodes.filter((node: { type: string }) => node.type === 'matcreator.tasks')).toHaveLength(1);
    expect(nodes.find((node: { id: string }) => node.id === board.id).config).toEqual(board.config);
    await page.screenshot({ path: '../.outputs/card-state/research-session-a-restored.png' });
    await page.reload();
    await page.locator(`[data-card-id="${group.id}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).click();
    await expect(tasks.getByRole('heading', { name: 'A copper study', exact: true })).toBeVisible();
    await expect(tasks.getByRole('button', { name: 'A structure calculation', exact: true })).toBeVisible();
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: [board.id, conversation.id, group.id] } });
  }
});

// Host integration: Conversation selection, a persistent graph, and scoped plugin state.
test('a workspace changes Task Board data with its conversation and restores A without scope UI', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null, 'oaw-conversation-view-v1': null,
  } } });
  const create = async (type: string, name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 300 } } });
    expect(response.status()).toBe(201); return response.json();
  };
  const conversation = await create('conversation', 'Conversation', 300);
  const board = await create('oaw.tasks', 'Tasks', 700);
  const viewer = await create('science.structure-viewer', 'Viewer', 1100);
  const grouped = await request.post('/api/legion-groups', { data: { name: 'State studio', node_ids: [conversation.id, board.id, viewer.id] } });
  const group = (await grouped.json())[0];
  const pane = (id: string) => ({ kind: 'pane', view: { card_id: id } });
  await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2, hidden_sections: [], root: {
    kind: 'split', axis: 'horizontal', ratio: .5, first: pane(conversation.id), second: pane(board.id),
  } } } } });
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.locator(`[data-card-id="${group.id}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).click();
    const workspace = page.getByRole('dialog', { name: 'State studio workspace mode' });
    const tasks = workspace.locator('.task-board');
    await tasks.getByLabel('New task title').fill('A retained task');
    await tasks.getByRole('button', { name: 'Add task', exact: true }).click();
    await expect(tasks.getByRole('button', { name: 'Edit task A retained task', exact: true })).toBeVisible();
    const a = (await (await request.get(`/api/conversations/${conversation.id}`)).json()).sessions.find((s: { is_default: boolean }) => s.is_default);
    await workspace.getByRole('button', { name: 'New session', exact: true }).click();
    await expect(tasks.getByText('A clear place to start.', { exact: true })).toBeVisible();
    await expect(tasks.getByRole('button', { name: 'Edit task A retained task', exact: true })).toHaveCount(0);
    await expect(tasks).not.toContainText(/Shared across sessions|Separate for each session|scope|Session/);
    await tasks.getByLabel('New task title').fill('B independent task');
    await tasks.getByRole('button', { name: 'Add task', exact: true }).click();
    await expect(tasks.getByRole('button', { name: 'Edit task B independent task', exact: true })).toBeVisible();
    await workspace.locator('button.workspace-session[title]').filter({ hasText: a.title }).click();
    await expect(tasks.getByRole('button', { name: 'Edit task A retained task', exact: true })).toBeVisible();
    await expect(tasks.getByRole('button', { name: 'Edit task B independent task', exact: true })).toHaveCount(0);
    const nodes = await (await request.get('/api/nodes')).json();
    expect(nodes.filter((node: { type: string }) => node.type === 'oaw.tasks')).toHaveLength(1);
    expect(nodes.find((node: { id: string }) => node.id === board.id).config).toEqual(board.config);
    await page.screenshot({ path: '../.outputs/card-state/session-a-restored.png' });
    await page.reload();
    await page.locator(`[data-card-id="${group.id}"]`).getByRole("button", { name: "Workspace mode", exact: true }).click();
    await expect(page.locator('.task-board').getByRole('button', { name: 'Edit task A retained task', exact: true })).toBeVisible();
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: [board.id, viewer.id, conversation.id, group.id] } });
  }
});
