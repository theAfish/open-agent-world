import { expect, test } from '@playwright/test';

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } })).ok()).toBe(true);
});

test('MatCreator preset opens sessions, files, conversation and a persistent research board', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null, 'oaw-theme': 'light',
  } } })).ok()).toBe(true);
  await page.goto('/');
  await page.getByRole('tab', { name: /Legions/ }).click();
  const deployed = page.waitForResponse(response => response.url().includes('/presets/matcreator.research/instances'));
  await page.getByRole('button', { name: 'Place MatCreator research', exact: true }).click();
  const response = await deployed;
  expect(response.status()).toBe(201);
  const instance = await response.json();
  expect(instance.nodes.find((node: { id: string }) => node.id === instance.node_ids.summoning).equipment.owner_id).toBe(instance.node_ids.agent);
  expect(instance.nodes.find((node: { id: string }) => node.id === instance.node_ids.executor).parent_id).toBe(instance.node_ids.barracks);
  const group = instance.node_ids.group;
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const toolsets = ['knowledge', 'barracks'].map(key => page.locator(`[data-card-id="${instance.node_ids[key]}"]`));
  for (const toolset of toolsets) await expect(toolset).toBeVisible();
  const bounds = await Promise.all(toolsets.map(toolset => toolset.boundingBox()));
  for (let i = 0; i < bounds.length; i++) for (let j = i + 1; j < bounds.length; j++) {
    const a = bounds[i]!, b = bounds[j]!;
    expect(a.x + a.width <= b.x + 1 || b.x + b.width <= a.x + 1 || a.y + a.height <= b.y + 1 || b.y + b.height <= a.y + 1).toBe(true);
  }
  await page.screenshot({ path: 'test-results/matcreator-formation.png' });
  const open = async () => {
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.locator(`[data-card-id="${group}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).click();
    return page.getByRole('dialog', { name: 'MatCreator research workspace mode' });
  };
  const workspace = await open();
  await expect(workspace.getByRole('region', { name: 'Research task board', exact: true })).toBeVisible();
  await expect(workspace.getByRole('button', { name: 'New session', exact: true })).toBeVisible();
  await expect(workspace.getByText('Start once to prepare the managed workspace', { exact: true })).toBeVisible();
  await expect(workspace.getByRole('textbox', { name: 'Conversation message', exact: true })).toBeVisible();
  const board = workspace.getByRole('region', { name: 'Research task board', exact: true });
  await board.getByRole('button', { name: 'New plan', exact: true }).first().click();
  await board.getByLabel('Plan title', { exact: true }).fill('Copper structure study');
  await board.getByLabel('Research goal').fill('Build a 2 × 2 × 2 copper supercell and verify 32 atoms.');
  await board.getByRole('button', { name: 'Create plan', exact: true }).click();
  await board.getByRole('button', { name: 'Add task', exact: true }).click();
  await board.getByLabel('Task title', { exact: true }).fill('Generate the copper structure');
  await board.getByLabel('Task details').fill('Read Materials Core and inspect the Sandbox environment.');
  await board.getByLabel('Acceptance criteria').fill('Verify exactly 32 copper atoms and publish the structure.');
  await board.getByRole('button', { name: 'Save task', exact: true }).click();
  await board.getByRole('button', { name: '‹ Tasks', exact: true }).click();
  await expect(board.getByRole('button', { name: /Generate the copper structure/ })).toBeVisible();
  let doc = await (await request.get(`/api/nodes/${instance.node_ids.tasks}/document`)).json();
  const plan = doc.value.plans[0];
  expect((await request.post(`/api/nodes/${instance.node_ids.tasks}/actions/update_task`, { data: {
    expected_revision: doc.revision,
    arguments: { plan_id: plan.id, task_id: plan.tasks[0].id, status: 'review', result: 'Executor reports 32 atoms; verify the file.' },
  } })).ok()).toBe(true);
  await expect(board.getByRole('region', { name: 'Awaiting review', exact: true })).toBeVisible();
  await board.getByRole('button', { name: 'Generate the copper structure', exact: true }).click();
  await expect(board.getByText('Executor finished. Verify the outputs and record evidence before marking this task done.')).toBeVisible();
  await expect(board.getByText('Verify exactly 32 copper atoms and publish the structure.')).toBeVisible();
  await page.screenshot({ path: 'test-results/matcreator-task-review.png' });
  await board.getByRole('button', { name: '‹ Tasks', exact: true }).click();
  doc = await (await request.get(`/api/nodes/${instance.node_ids.tasks}/document`)).json();
  const updated = await request.post(`/api/nodes/${instance.node_ids.tasks}/actions/update_task`, { data: {
    expected_revision: doc.revision,
    arguments: { plan_id: plan.id, task_id: plan.tasks[0].id, status: 'done', result: 'Fixture result: validated 32 atoms', outputs: ['copper/structure.xyz'] },
  } });
  expect(updated.ok()).toBe(true);
  await expect(board.getByRole('region', { name: 'Done', exact: true }).getByRole('button', { name: /Generate the copper structure/ })).toBeVisible();
  await board.getByRole('region', { name: 'Done', exact: true }).getByRole('button', { name: /Generate the copper structure/ }).click();
  await expect(board.getByText('copper/structure.xyz', { exact: true })).toBeVisible();
  await board.getByRole('button', { name: '‹ Tasks', exact: true }).click();
  await page.screenshot({ path: 'test-results/matcreator-workspace-light.png' });
  const stage = workspace.locator('.legion-layout-stage');
  const fit = await stage.evaluate(element => ({ width: element.clientWidth, scrollWidth: element.scrollWidth, height: element.clientHeight, scrollHeight: element.scrollHeight }));
  expect(fit.scrollWidth).toBeLessThanOrEqual(fit.width + 1);
  expect(fit.scrollHeight).toBeLessThanOrEqual(fit.height + 1);
  await expect(workspace.getByRole('region', { name: 'Sandbox terminal', exact: true })).toBeVisible();
  await expect(workspace.getByRole('button', { name: 'Start', exact: true })).toBeVisible();
  // Exercise the preset's real Conversation grant and renderer in its lower-right tab.
  const conversation = await (await request.get(`/api/conversations/${instance.node_ids.conversation}`)).json();
  const sessionBase = `/api/conversations/${instance.node_ids.conversation}/sessions/${conversation.sessions[0].id}`;
  const uploaded = await request.post(`${sessionBase}/attachments?filename=helium.xyz`, {
    data: Buffer.from('1\nHelium\nHe 0 0 0\n'), headers: { 'Content-Type': 'application/octet-stream' },
  });
  expect(uploaded.status()).toBe(201);
  const attachment = await uploaded.json();
  expect((await request.post(`${sessionBase}/messages`, { data: {
    attachments: [{ version_id: attachment.version_id, path: attachment.path }],
  } })).status()).toBe(202);
  await workspace.getByRole('button', { name: 'Open helium.xyz', exact: true }).click();
  await workspace.getByRole('tab', { name: 'Structure viewer', exact: true }).click();
  const viewer = workspace.locator('.structure-viewer--workspace');
  await expect(viewer).toBeVisible();
  await expect(viewer.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '1', { timeout: 30_000 });
  await expect(viewer.locator('canvas').first()).toBeVisible();
  // Wait for an actual interactive atom, not just a parsed file or blank canvas.
  await expect(async () => {
    const box = (await viewer.locator('canvas').first().boundingBox())!;
    await page.mouse.move(box.x + 5, box.y + 5);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await expect(viewer.locator('.elem-name')).toHaveText('Helium', { timeout: 500 });
  }).toPass({ timeout: 15_000 });
  await page.screenshot({ path: 'test-results/matcreator-structure-viewer.png' });
  // Switching tabs retains access to the Sandbox controls.
  await workspace.getByRole('tab', { name: 'Research files & compute', exact: true }).click();
  await expect(workspace.getByRole('button', { name: 'Start', exact: true })).toBeVisible();
  await workspace.getByRole('tab', { name: /Research files & compute · File preview/ }).click();
  await workspace.getByRole('tab', { name: 'Research knowledge', exact: true }).click();
  await expect(workspace.getByRole('region', { name: 'Know-Do Graph workspace', exact: true })).toBeVisible();
  await workspace.getByRole('tab', { name: 'Research tasks', exact: true }).click();
  await page.reload();
  await open();
  await expect(board.getByRole('heading', { name: 'Copper structure study', exact: true })).toBeVisible();
  await board.getByRole('button', { name: 'Generate the copper structure', exact: true }).click();
  await expect(board.getByText('Fixture result: validated 32 atoms', { exact: true })).toBeVisible();
  await board.getByRole('button', { name: '‹ Tasks', exact: true }).click();
  await workspace.getByRole('button', { name: 'Use dark theme' }).click();
  await page.screenshot({ path: 'test-results/matcreator-workspace-dark.png' });
  await page.setViewportSize({ width: 960, height: 720 });
  await expect(board.getByRole('button', { name: 'Add task', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/matcreator-workspace-small.png' });
  expect(errors).toEqual([]);
});
