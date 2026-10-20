import { expect, test } from '@playwright/test';

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } })).ok()).toBe(true);
});

test('native research workspace renders plan and subagents and switches session state', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const instanceResponse = await request.post('/api/legions/presets/matcreator.native-research/instances', { data: {} });
  expect(instanceResponse.status(), await instanceResponse.text()).toBe(201);
  const instance = await instanceResponse.json();
  const ids = instance.node_ids;
  const conversation = await (await request.get(`/api/conversations/${ids.conversation}`)).json();
  const session = conversation.sessions[0].id;
  const projection = {
    history_id: 'research-history', revision: 1, phase: 'running', title: 'Copper structure verification', nodes: [
      { id: 'root', kind: 'agent', label: 'Orchestrator', status: 'running' },
      { id: 'planner', kind: 'agent', label: 'Planning', status: 'success', parent_id: 'root' },
      { id: 'execution_0', kind: 'agent', role: 'execution', execution_id: 'execution_0', label: 'Execution', status: 'running', parent_id: 'planner' },
      { id: 'worker', kind: 'agent', execution_id: 'execution_0', label: 'Geometry subagent', status: 'running', parent_id: 'execution_0', detail: 'Verifying geometry through connected OAW tools.' },
      { id: 'review', kind: 'agent', execution_id: 'execution_0', label: 'Review subagent', status: 'needs_replanning', parent_id: 'execution_0' },
      { id: 'build', kind: 'plan', label: 'Build copper', status: 'success', result: 'Fixture: 32 atoms verified' },
    ],
  };
  const projected = await request.post(`/api/test/execution/${ids.agent}/${session}`, { data: projection });
  expect(projected.ok(), await projected.text()).toBe(true);
  const { run_id: runId } = await projected.json();
  const profile = await (await request.get('/api/application')).json();
  const preferences = await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
      'oaw-active-workspace-v1': JSON.stringify({ version: 0, state: { activeId: ids.group } }),
    },
  } });
  expect(preferences.ok()).toBe(true);
  const errors: string[] = [];
  let executionNotifications = 0;
  page.on('websocket', socket => socket.on('framereceived', event => {
    if (String(event.payload).includes('execution_updated')) executionNotifications++;
  }));
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install();
  await page.goto('/');
  const workspace = page.getByRole('dialog', { name: 'MatCreator research workspace mode' });
  await expect(workspace).toBeVisible({ timeout: 30_000 });
  await expect(workspace.getByRole('button', { name: 'Execution: running', exact: true })).toBeVisible();
  await expect(workspace.getByRole('textbox', { name: 'Conversation message', exact: true })).toBeVisible();
  const graph = workspace.getByRole('region', { name: 'Execution graph', exact: true });
  // Containment and chronology do not masquerade as dependency edges.
  await expect(graph.locator('[data-graph-edge]')).toHaveCount(0);
  await graph.getByRole('button', { name: 'Geometry subagent: running' }).click();
  await expect(graph.getByText('Verifying geometry through connected OAW tools.')).toBeVisible();
  await page.screenshot({ path: 'test-results/matcreator-native-workspace.png' });
  await graph.getByRole('button', { name: 'Build copper: success' }).click();
  await expect(graph.getByText('Fixture: 32 atoms verified')).toBeVisible();
  await graph.getByRole('button', { name: 'Geometry subagent: running' }).click();
  await page.waitForResponse(response => response.url().includes(`/execution-sources/${ids.agent}`));
  await page.clock.pauseAt(new Date(Date.now() + 100));
  const notificationsBefore = executionNotifications;
  const updated = await request.post(`/api/test/execution/${ids.agent}/${session}`, { data: {
    ...projection, run_id: runId, revision: 2, phase: 'succeeded',
    nodes: projection.nodes.map(node => ({ ...node, status: 'success', result: 'Live result verified' })),
  } });
  expect(updated.ok(), await updated.text()).toBe(true);
  await expect.poll(() => executionNotifications).toBeGreaterThan(notificationsBefore);
  await page.clock.runFor(100);
  await expect(graph.getByRole('button', { name: 'Geometry subagent: success' })).toHaveAttribute('aria-pressed', 'true');
  await expect(graph.getByText('Live result verified')).toBeVisible();
  await expect(graph.locator('[data-phase]')).toHaveAttribute('data-phase', 'succeeded');
  await page.clock.resume();
  const starting = await request.post(`/api/test/execution/${ids.agent}/${session}/start`);
  expect(starting.ok(), await starting.text()).toBe(true);
  const nextRun = (await starting.json()).run_id;
  await expect(graph.getByText('Waiting for this run’s graph…')).toBeVisible();
  await expect(graph.getByRole('button', { name: 'Geometry subagent: success' })).toHaveAttribute('aria-pressed', 'true');
  const resumed = await request.post(`/api/test/execution/${ids.agent}/${session}`, { data: {
    ...projection, revision: 3, run_id: nextRun,
  } });
  expect(resumed.ok(), await resumed.text()).toBe(true);
  await expect(graph.getByText('Waiting for this run’s graph…')).toHaveCount(0);
  await expect(graph.getByRole('button', { name: 'Geometry subagent: running' })).toHaveAttribute('aria-pressed', 'true');
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await graph.screenshot({ path: `test-results/execution-graph-${theme}.png` });
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  // Keep an unsent conversation draft while operations reveal other tabs.
  const composer = workspace.getByRole('textbox', { name: 'Conversation message', exact: true });
  await composer.fill('Retained research question');
  const sessionBase = `/api/conversations/${ids.conversation}/sessions/${session}`;
  const uploaded = await request.post(`${sessionBase}/attachments?filename=helium.xyz`, {
    data: Buffer.from('1\nHelium\nHe 0 0 0\n'), headers: { 'Content-Type': 'application/octet-stream' },
  });
  expect(uploaded.status()).toBe(201);
  const attachment = await uploaded.json();
  expect((await request.post(`${sessionBase}/messages`, { data: {
    attachments: [{ version_id: attachment.version_id, path: attachment.path }],
  } })).status()).toBe(202);
  const layoutBefore = (await (await request.get(`/api/nodes/${ids.group}`)).json()).config.workspace_layout;
  await workspace.getByRole('button', { name: 'Open helium.xyz', exact: true }).click();
  await expect(workspace.getByRole('tab', { name: 'Structure viewer', exact: true })).toHaveAttribute('aria-selected', 'true');
  const viewer = workspace.locator('.structure-viewer--workspace');
  await expect(viewer.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '1', { timeout: 30_000 });
  await expect(viewer.locator('canvas').first()).toBeVisible();
  await expect(viewer.locator('.structure-empty[role="alert"]')).toHaveCount(0);
  await expect(page.getByRole('dialog', { name: 'Preview helium.xyz', exact: true })).toHaveCount(0);
  expect((await (await request.get(`/api/nodes/${ids.group}`)).json()).config.workspace_layout).toEqual(layoutBefore);
  await page.screenshot({ path: 'test-results/matcreator-native-structure.png' });
  await workspace.getByRole('tab', { name: 'Skills & knowledge', exact: true }).click();
  await expect(workspace.getByRole('region', { name: 'Know-Do Graph workspace' })).toBeVisible();
  await workspace.getByRole('tab', { name: /Research files & compute · File preview/ }).click();
  await expect(workspace.getByText('Select a file to preview')).toBeVisible();
  await workspace.getByRole('tab', { name: /Research conversation · Conversation/ }).click();
  await expect(composer).toHaveValue('Retained research question');

  await workspace.getByRole('button', { name: 'New session', exact: true }).click();
  await expect(workspace.getByText('No execution yet')).toBeVisible();
  await graph.screenshot({ path: 'test-results/execution-graph-empty.png' });
  await expect(workspace.getByText('Copper structure verification')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('standalone execution graph gestures stay local inside a scaled canvas', async ({ page, request }) => {
  test.setTimeout(60_000);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1500, height: 950 });
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
      'oaw-active-workspace-v1': null, 'oaw-node-surfaces-v1': null, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
    },
  } })).ok()).toBe(true);
  const create = async (type: string, name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 250 } } });
    expect(response.status(), await response.text()).toBe(201);
    return response.json();
  };
  const source = await create('agent', 'Researcher', 1100);
  const viewer = await create('core.execution-graph', 'Agent graph', 300);
  expect((await request.post('/api/edges', { data: { source: viewer.id, target: source.id, relationship: 'core.execution-source' } })).status()).toBe(201);
  expect((await request.post(`/api/test/execution/${source.id}/default`, { data: {
    title: 'Inspect the geometry, then review the calculation and prepare the result.', phase: 'running', nodes: [
      { id: 'root', kind: 'agent', label: 'Coordinator', status: 'running' },
      { id: 'geometry', kind: 'agent', label: 'Geometry', status: 'success', parent_id: 'root' },
      { id: 'review', kind: 'agent', label: 'Review', status: 'running', parent_id: 'root' },
    ],
  } })).ok()).toBe(true);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const card = page.locator(`[data-card-id="${viewer.id}"]`);
  await expect(card).toBeVisible();
  if (await card.getAttribute('data-surface-level') === 'preview') await card.dblclick();
  const opener = card.getByRole('button', { name: 'Open workspace', exact: true });
  if (await opener.count()) await opener.evaluate((button: HTMLButtonElement) => button.click());
  const surface = page.locator(`[data-workspace-node-id="${viewer.id}"]`);
  const graph = surface.getByRole('region', { name: 'Execution graph', exact: true });
  await expect(graph.getByRole('button', { name: 'Coordinator: running' })).toBeVisible();
  const tutorial = page.locator('.card-tutorial-nudge');
  if (await tutorial.count()) await tutorial.getByRole('button', { name: /Close/ }).click();
  const viewport = graph.getByLabel('Execution history', { exact: true });
  const outer = page.locator('.react-flow__viewport');
  // Opening a workspace expands its window beyond the old card bounds. Place
  // that real window onscreen at a non-unit outer zoom before testing gestures.
  await expect.poll(async () => {
    const previous = await surface.boundingBox();
    await new Promise(resolve => setTimeout(resolve, 100));
    return JSON.stringify(previous) === JSON.stringify(await surface.boundingBox());
  }).toBe(true);
  const placeWindow = async (scale: number) => {
    const origin = await surface.evaluate((element, scale) => {
      const matrix = new DOMMatrix(getComputedStyle(element.closest('.react-flow__viewport')!).transform);
      const bounds = element.getBoundingClientRect();
      return { x: 30 - (bounds.x - matrix.m41) / matrix.a * scale, y: 30 - (bounds.y - matrix.m42) / matrix.d * scale };
    }, scale);
    return page.addStyleTag({ content: `.react-flow__viewport { transform: translate(${origin.x}px, ${origin.y}px) scale(${scale}) !important; }` });
  };
  const gestureCamera = await placeWindow(.75);
  await graph.getByRole('button', { name: 'Geometry: success', exact: true }).click();
  const outerTransform = await outer.getAttribute('style');
  const box = (await viewport.boundingBox())!;
  await page.mouse.move(box.x + 12, box.y + 15);
  await page.mouse.wheel(0, 250);
  await expect(outer).toHaveAttribute('style', outerTransform!);
  await page.mouse.down();
  await page.mouse.move(box.x + 60, box.y + 40, { steps: 6 });
  await page.mouse.up();
  await expect(outer).toHaveAttribute('style', outerTransform!);
  await expect(graph.getByRole('button', { name: 'Geometry: success', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const persisted = await (await request.get(`/api/nodes/${viewer.id}`)).json();
  expect(persisted.position).toEqual(viewer.position);
  await gestureCamera.evaluate(element => element.remove());
  await page.evaluate(() => window.getSelection()?.removeAllRanges());
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    for (const width of [300, 400, 500, 760]) {
      // Inspect the real card at 1:1, without changing saved window geometry.
      await surface.evaluate((element, width) => {
        for (const host of [element, element.closest('.world-card')]) {
          if (host instanceof HTMLElement) { host.style.width = `${width}px`; host.style.height = '700px'; }
        }
      }, width);
      const style = await placeWindow(1);
      await graph.screenshot({ path: `test-results/execution-graph-standalone-${theme}-${width}.png` });
      await style.evaluate(element => element.remove());
    }
  }
  expect(errors).toEqual([]);
});
