import { expect, test } from '@playwright/test';
import path from 'node:path';

// Host integration scene: one Legion composes built-in cards and the Task Board plugin.
// Set OAW_CAPTURE_README=1 to refresh the checked-in README images.
test('README Legion scene retains connections and opens live workspace panels', async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const profile = await (await request.get('/api/application')).json();
  const preferences = async (changes: Record<string, string | null>) => {
    expect((await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes } })).ok()).toBe(true);
  };
  const create = async (type: string, name: string, x: number, y: number, content?: string) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y }, ...(content ? { content } : {}),
      ...(type === 'agent' ? { config: { runtime_provider_id: 'core.mock', system_instruction: name === 'Researcher' ? 'Compare evidence and record useful sources.' : 'Turn the evidence into a clear recommendation.' } } : {}) } });
    expect(response.status()).toBe(201);
    return response.json();
  };
  const researcher = await create('agent', 'Researcher', 200, 220);
  const writer = await create('agent', 'Writer', 610, 220);
  const brief = await create('text', 'Project brief', 200, 570, '# A greener neighbourhood\n\nCompare three ideas for a small community garden.\n\n## What matters\n- Low water use\n- Space for pollinators\n- Easy care for volunteers\n\n## Deliverable\nA short recommendation with sources, trade-offs and next steps.\n\nKeep the discussion and task progress together in this studio.');
  const conversation = await create('conversation', 'Team conversation', 610, 570);
  const board = await create('oaw.tasks', 'Research plan', 1020, 400);
  for (const [source, target, relationship] of [[researcher.id, brief.id, 'read'], [researcher.id, conversation.id, 'participate'], [writer.id, conversation.id, 'participate'], [writer.id, board.id, 'oaw.tasks.progress'], [researcher.id, writer.id, 'communicate']]) {
    expect((await request.post('/api/edges', { data: { source, target, relationship } })).ok()).toBe(true);
  }
  expect((await request.post(`/api/nodes/${board.id}/actions/upsert`, { data: { expected_revision: 0, arguments: { tasks: [
    { id: 'sources', title: 'Collect sources', status: 'done', note: 'Three approaches ready to compare.' },
    { id: 'compare', title: 'Compare the options', status: 'doing', depends_on: ['sources'], note: 'Check water use, wildlife value and upkeep.' },
    { id: 'draft', title: 'Draft the recommendation', depends_on: ['compare'] },
    { id: 'review', title: 'Review and share', depends_on: ['draft'] },
  ] } } })).ok()).toBe(true);
  const summary = await (await request.get(`/api/conversations/${conversation.id}`)).json();
  expect((await request.post(`/api/conversations/${conversation.id}/sessions/${summary.sessions[0].id}/messages`, { data: {
    content: 'Let’s compare a native flower border, a rain garden and raised herb beds.\n\nUse the project brief as our checklist. Keep sources and trade-offs in this conversation, then update the Research plan as each step is ready.\n\nThis is a prepared example project; no model run is needed to explore the workspace.',
  } })).ok()).toBe(true);
  const response = await request.post('/api/legion-groups', { data: { name: 'Research studio', node_ids: [researcher.id, writer.id, brief.id, conversation.id, board.id] } });
  expect(response.ok()).toBe(true);
  const group = (await response.json()).find((card: { type: string }) => card.type === 'legion');
  const pane = (id: string) => ({ kind: 'pane', view: { card_id: id } });
  const layout = { version: 2, hidden_sections: [], root: { kind: 'split', axis: 'horizontal', ratio: .24, first: pane(brief.id),
    second: { kind: 'split', axis: 'horizontal', ratio: .62, first: pane(conversation.id), second: pane(board.id) } } };
  expect((await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: layout } } })).ok()).toBe(true);
  await preferences({ 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en', 'oaw-theme': 'light',
    'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': JSON.stringify({ version: 4, state: { surfaceLevels: { [conversation.id]: 'preview', [board.id]: 'preview' }, baseLevels: {}, surfaceSizes: {}, workspaceSizes: {}, maximizedWorkspaces: {} } }) });
  const capture = async (name: string) => {
    await page.mouse.move(1580, 980);
    await page.screenshot({ path: process.env.OAW_CAPTURE_README === '1' ? path.resolve('../docs/assets/demos', `${name}.png`) : testInfo.outputPath(`${name}.png`), animations: 'disabled' });
  };
  await page.goto('/');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await expect(page.locator(`[data-card-id="${group.id}"]`)).toBeVisible();
  // Wait for fit-view animation and resources before comparing both themes.
  await expect(page.getByRole('button', { name: 'Workspace mode', exact: true })).toBeInViewport();
  await page.waitForTimeout(800);
  await capture('legion-canvas');
  await page.getByRole('button', { name: 'Use dark theme', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await capture('legion-canvas-dark');
  await page.getByRole('button', { name: 'Workspace mode', exact: true }).click();
  const workspace = page.getByRole('dialog', { name: 'Research studio workspace mode' });
  await expect(workspace.getByRole('button', { name: 'Edit task Compare the options', exact: true })).toBeVisible();
  await expect(workspace.getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue(/A greener neighbourhood/);
  await expect(workspace.getByText(/Let’s compare a native flower border/)).toBeVisible();
  await capture('legion-workspace-dark');
  await workspace.getByRole('button', { name: 'Use light theme', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await capture('legion-workspace');
  expect((await (await request.get('/api/edges')).json()).length).toBe(5);
  expect((await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout).toEqual(layout);
});

test.afterEach(async ({ request }) => {
  const cards = await (await request.get('/api/nodes')).json();
  if (cards.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: cards.map((card: { id: string }) => card.id) } })).ok()).toBe(true);
});
