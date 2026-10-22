import { expect, test } from '@playwright/test';

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } })).ok()).toBe(true);
});

for (const composed of [false, true]) test(`session and group creation keep the ${composed ? 'composed' : 'standalone'} conversation mounted without a reload`, async ({ page, request }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.stack ?? error.message));
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Creation regression', position: { x: 300, y: 200 } } })).json();
  const agent = await (await request.post('/api/nodes', { data: { type: 'agent', name: 'Creation participant', position: { x: 0, y: 0 } } })).json();
  await request.post('/api/edges', { data: { source: agent.id, target: room.id, relationship: 'participate' } });
  const group = composed ? (await (await request.post('/api/legion-groups', { data: { name: 'Creation studio', node_ids: [room.id] } })).json())[0] : undefined;
  if (group) {
    const pane = (section_id: string) => ({ kind: 'pane', view: { card_id: room.id, section_id } });
    await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2, hidden_sections: [], root: {
      kind: 'split', axis: 'horizontal', ratio: .28, first: pane('sessions'), second: pane('conversation'),
    } } } } });
  }
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    'oaw-progressive-tutorials-v1': JSON.stringify({ version: 1, state: { enabled: false, progress: {} } }),
    'oaw.locale': 'en', 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
    'oaw-node-surfaces-v1': JSON.stringify({ version: 4, state: { surfaceLevels: { [room.id]: 'workspace' } } }),
  } } });
  let holdRefresh = false;
  let releaseRefresh = () => {};
  const pendingRefresh = new Promise<void>(resolve => { releaseRefresh = resolve; });
  await page.route(`**/api/conversations/${room.id}`, async route => {
    if (holdRefresh) await pendingRefresh;
    await route.continue();
  });
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    if (group) await page.locator(`[data-card-id="${group.id}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).evaluate((button: HTMLButtonElement) => button.click());
    const surface = group ? page.getByRole('dialog', { name: 'Creation studio workspace mode' }) : page.locator(`[data-workspace-node-id="${room.id}"]`);
    await expect(surface).toBeVisible();
    if (composed) await expect(page.getByLabel('Conversation message', { exact: true })).toHaveCount(2);
    // Hold only the refresh, never the real create response. The background
    // canvas retains its old snapshot while the workspace owns the new session.
    holdRefresh = true;
    await surface.getByRole('button', { name: 'New session', exact: true }).click();
    await expect(surface.getByRole('button', { name: 'New session', exact: true })).toBeEnabled();
    await expect(surface.getByText('This session is ready', { exact: true })).toBeVisible();
    await surface.getByLabel('Conversation message', { exact: true }).fill('Draft in the new session');
    await expect(surface.locator('.conversation-composer-guidance')).toContainText('@Creation participant');
    await surface.getByRole('button', { name: /^Switch group:/ }).click();
    await surface.getByRole('button', { name: 'New group', exact: true }).click();
    await expect(surface.getByRole('textbox', { name: 'Group name', exact: true })).toBeVisible();
    await surface.getByRole('textbox', { name: 'Group name', exact: true }).fill('Created group');
    await surface.getByRole('button', { name: 'Create group', exact: true }).click();
    await expect(surface.getByRole('button', { name: 'Switch group: Created group', exact: true })).toBeVisible();
    await expect(surface.getByText('This session is ready', { exact: true })).toBeVisible();
    await expect(surface.getByLabel('Conversation message', { exact: true })).toHaveValue('');
    holdRefresh = false;
    releaseRefresh();
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      await surface.screenshot({ path: `../.outputs/conversation-fix/after-${composed ? 'composed' : 'standalone'}-${theme}.png` });
    }
    expect(errors).toEqual([]);
  } finally {
    releaseRefresh();
    await page.close();
  }
});

for (const [id, name] of [['matcreator.native-research', 'MatCreator research'], ['knowledge.base.research', 'Lab Manager']]) test(`${name} can create sessions and groups`, async ({ page, request }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.stack ?? error.message));
  const response = await request.post(`/api/legions/presets/${id}/instances`, { data: {} });
  expect(response.ok()).toBe(true);
  const { node_ids: ids } = await response.json();
  try {
    const profile = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en', 'oaw-theme': 'light',
      'oaw-node-surfaces-v1': null, 'oaw-canvas-viewport-v1': null,
    } } });
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.locator(`[data-card-id="${ids.group}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).evaluate((button: HTMLButtonElement) => button.click());
    const surface = page.getByRole('dialog', { name: `${name} workspace mode`, exact: true });
    await surface.getByRole('button', { name: 'New session', exact: true }).click();
    await expect(surface.getByRole('button', { name: 'New session', exact: true })).toBeEnabled();
    await expect(surface.getByLabel('Conversation message', { exact: true })).toBeVisible();
    await surface.screenshot({ path: `../.outputs/conversation-fix/${id}-created.png` });
    await surface.getByRole('button', { name: /^Switch group:/ }).click();
    await surface.getByRole('button', { name: 'New group', exact: true }).click();
    const groupName = surface.getByRole('textbox', { name: 'Group name', exact: true });
    await expect(groupName).toBeFocused();
    await groupName.fill('New research group');
    const participants = surface.getByRole('group', { name: 'Participants', exact: true });
    await participants.getByRole('checkbox').first().check();
    await surface.getByRole('button', { name: 'Create group', exact: true }).click();
    await expect(surface.getByRole('button', { name: 'Switch group: New research group', exact: true })).toBeVisible();
    await expect(surface.getByLabel('Conversation message', { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
});
