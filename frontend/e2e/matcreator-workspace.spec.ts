import { expect, test } from '@playwright/test';

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } })).ok()).toBe(true);
});

test('MatCreator native preset opens from the deck with graph, central tabs and right-side sections', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null, 'oaw-theme': 'light',
    'oaw-active-workspace-v1': null,
  } } })).ok()).toBe(true);
  // Presets are available in the Library but are not auto-equipped in new decks.
  let library = await (await request.get('/api/card-library')).json();
  const sourcePack = Object.values(library.packs).find((pack: any) => pack.definition.cards.includes('matcreator.agent')) as any;
  expect(sourcePack.definition.cards).toContain('core.jobs');
  const opened = await request.post('/api/card-library/actions', { data: {
    expected_revision: library.revision, action: 'open_pack', id: sourcePack.definition.id,
  } });
  expect(opened.ok(), await opened.text()).toBe(true);
  library = await opened.json();
  expect(library.collection['core.jobs'].plugin_id).toBe('open-agent-world.core');
  const equipped = await request.post('/api/card-library/actions', { data: {
    expected_revision: library.revision, action: 'update_deck', id: 'saved-legions',
    entries: [{ kind: 'legion', id: 'matcreator.native-research' }],
  } });
  expect(equipped.ok(), await equipped.text()).toBe(true);
  await page.goto('/');
  await page.getByRole('tab', { name: /Legions/ }).click();
  const deployed = page.waitForResponse(response => response.url().includes('/presets/matcreator.native-research/instances'));
  // Open the remaining owned dependencies, including Published artifacts.
  page.once('dialog', async dialog => {
    expect(dialog.type()).toBe('confirm');
    expect(dialog.message()).toContain('Open these owned packs and continue?');
    await dialog.accept();
  });
  await page.getByRole('button', { name: 'Place MatCreator research', exact: true }).click();
  const response = await deployed;
  expect(response.status(), await response.text()).toBe(201);
  const instance = await response.json();
  const group = instance.node_ids.group;
  const groupCard = page.locator(`.react-flow__node[data-id="${group}"]`);
  await expect(groupCard).toBeAttached();
  expect(instance.nodes.find((node: { id: string }) => node.id === instance.node_ids.artifacts).type).toBe('core.artifact-collection');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const checkFormation = async () => {
    const keys = ['conversation', 'structure', 'agent', 'sandbox', 'execution', 'jobs', 'artifacts', 'knowledge'];
    const cards = keys.map(key => page.locator(`[data-card-id="${instance.node_ids[key]}"]`));
    for (const card of cards) await expect(card).toBeInViewport();
    const bounds = await Promise.all(cards.map(card => card.boundingBox()));
    for (let i = 0; i < bounds.length; i++) for (let j = i + 1; j < bounds.length; j++) {
      const a = bounds[i]!, b = bounds[j]!;
      expect(a.x + a.width <= b.x + 1 || b.x + b.width <= a.x + 1 || a.y + a.height <= b.y + 1 || b.y + b.height <= a.y + 1,
        `${keys[i]} overlaps ${keys[j]}`).toBe(true);
    }
    const [conversation, structure, agent, sandbox, graph, jobs, artifacts, knowledge] = bounds.map(box => box!);
    expect(conversation.x + conversation.width).toBeLessThan(agent.x);
    expect(agent.x + agent.width).toBeLessThan(sandbox.x);
    expect(structure.y + structure.height).toBeLessThan(agent.y);
    expect(jobs.y).toBeGreaterThan(agent.y + agent.height);
    expect(graph.y).toBeGreaterThan(conversation.y + conversation.height);
    expect(artifacts.x + artifacts.width).toBeLessThan(jobs.x);
    expect(knowledge.y).toBeGreaterThan(sandbox.y + sandbox.height);
    expect(Math.abs(conversation.y - sandbox.y)).toBeLessThan(2);
    expect(Math.abs(structure.x - agent.x)).toBeLessThan(2);
    expect(Math.abs(jobs.x - agent.x)).toBeLessThan(2);
    await expect(cards[0].getByRole('textbox', { name: 'Conversation message', exact: true })).toBeVisible();
    await expect(cards[3].getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
    await expect(groupCard.getByRole('button', { name: 'Workspace mode', exact: true })).toBeInViewport();
  };
  await checkFormation();
  await page.reload();
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await checkFormation();
  await page.screenshot({ path: '../.outputs/matcreator/default-formation.png' });
  const open = async () => {
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await groupCard.getByRole('button', { name: 'Workspace mode', exact: true }).click();
    return page.getByRole('dialog', { name: 'MatCreator research workspace mode' });
  };
  const workspace = await open();
  await expect(workspace.getByRole('region', { name: 'Execution graph', exact: true })).toBeVisible();
  await expect(workspace.getByRole('button', { name: 'New session', exact: true })).toBeVisible();
  await expect(workspace.getByRole('textbox', { name: 'Conversation message', exact: true })).toBeVisible();
  const stage = workspace.locator('.legion-layout-stage');
  const fit = await stage.evaluate(element => ({ width: element.clientWidth, scrollWidth: element.scrollWidth, height: element.clientHeight, scrollHeight: element.scrollHeight }));
  expect(fit.scrollWidth).toBeLessThanOrEqual(fit.width + 1);
  expect(fit.scrollHeight).toBeLessThanOrEqual(fit.height + 1);
  await page.screenshot({ path: 'test-results/matcreator-workspace-light.png' });
  // Exercise the preset's real Conversation grant and automatic central-tab handoff.
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
  await expect(workspace.getByRole('tab', { name: 'Structure viewer', exact: true })).toHaveAttribute('aria-selected', 'true');
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
  await expect(workspace.getByText('Ready on demand', { exact: true })).toBeVisible();
  await workspace.getByRole('tab', { name: /Research files & compute · File preview/ }).click();
  await workspace.getByRole('tab', { name: 'Skills & knowledge', exact: true }).click();
  await expect(workspace.getByRole('region', { name: 'Know-Do Graph workspace', exact: true })).toBeVisible();
  await workspace.getByRole('tab', { name: /Research conversation · Conversation/ }).click();
  await workspace.getByRole('button', { name: 'Use dark theme' }).click();
  await page.screenshot({ path: 'test-results/matcreator-workspace-dark.png' });
  await page.setViewportSize({ width: 1100, height: 760 });
  await expect(workspace.getByRole('textbox', { name: 'Conversation message', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/matcreator-workspace-small.png' });
  expect(errors).toEqual([]);
});
