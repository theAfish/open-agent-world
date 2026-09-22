import { expect, test } from '@playwright/test';
import { resetTutorialProfile } from './tutorial-profile';

test.beforeEach(async ({ page, request }) => {
  await resetTutorialProfile(request);
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((n: { id: string }) => n.id) } })).ok()).toBe(true);
  const templates = await (await request.get('/api/legions')).json();
  for (const item of templates) await request.delete(`/api/legions/${item.id}`);
});

for (const [name, count, links] of [['General assistant', 2, 1], ['Coding workspace', 3, 2], ['Multi-Agent collaboration', 4, 6]] as const) {
  test(`${name} starts an unwrapped canvas and supports undo, redo and reload`, async ({ page, request }) => {
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'What would you like to do here?' })).toBeVisible();
    await expect(page.locator('.blueprint-option')).toHaveCount(3);
    if (name === 'General assistant') {
      await page.screenshot({ path: 'test-results/blueprints-welcome-light.png' });
      await page.getByRole('button', { name: 'Use dark theme' }).click();
      await page.screenshot({ path: 'test-results/blueprints-welcome-dark.png' });
    }
    await page.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Close settings', exact: true }).click();
    await page.getByRole('button', { name: 'Set up later', exact: true }).click();
    await expect(page.locator('.world-card')).toHaveCount(count);
    await expect(page.locator('[data-card-type="legion"]')).toHaveCount(0);
    const world = await (await request.get('/api/world')).json();
    expect(world.edges).toHaveLength(links);
    expect(world.nodes.every((n: { parent_id: string | null }) => n.parent_id === null)).toBe(true);
    await page.screenshot({ path: `test-results/blueprint-${count}-deployed.png` });
    await page.keyboard.press('Control+z');
    await expect(page.locator('.world-card')).toHaveCount(0);
    await page.keyboard.press('Control+Shift+z');
    await expect(page.locator('.world-card')).toHaveCount(count);
    await expect(page.locator('[data-card-type="legion"]')).toHaveCount(0);
    await page.reload();
    await expect(page.locator('.world-card')).toHaveCount(count);
    await expect(page.locator('.onboarding-welcome')).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test('welcome blueprints remain usable on a small canvas', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 640 });
  await page.goto('/');
  await expect(page.locator('.blueprint-option')).toHaveCount(3);
  await page.screenshot({ path: 'test-results/blueprints-welcome-small.png' });
  await page.getByRole('button', { name: 'Start Empty', exact: true }).click();
  await expect(page.locator('.onboarding-welcome')).toHaveCount(0);
  await expect(page.locator('.world-card')).toHaveCount(0);
});

test('header settings are optional; saving restores four display states and layout', async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const levels = ['node', 'preview', 'inspector', 'workspace'];
  const members = [];
  for (let i = 0; i < 4; i++) {
    const response = await request.post('/api/nodes', { data: { type: 'agent', name: `State ${i + 1}`, position: { x: 250 + (i % 2) * 1000, y: 280 + Math.floor(i / 2) * 850 } } });
    expect(response.status()).toBe(201);
    members.push(await response.json());
  }
  const grouped = await request.post('/api/legion-groups', { data: { name: 'Reusable layout', node_ids: members.map(n => n.id) } });
  expect(grouped.ok()).toBe(true);
  const groupId = (await grouped.json())[0].id;
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    'oaw-node-surfaces-v1': JSON.stringify({ version: 3, state: {
      surfaceLevels: Object.fromEntries(members.map((n, i) => [n.id, levels[i]])),
      baseLevels: Object.fromEntries(members.map(n => [n.id, 'node'])),
      workspaceSizes: { [members[3].id]: { width: 1100, height: 740 } },
    } }),
  } } })).ok()).toBe(true);
  await page.goto('/');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const group = page.locator(`[data-card-id="${groupId}"]`);
  await expect(group.locator('.legion-controls')).toHaveCount(0);
  await expect(group.locator('.container-header').getByRole('button', { name: 'Save to library', exact: true })).toBeVisible();
  const memberPositions = await Promise.all(members.map(async n => (await (await request.get(`/api/nodes/${n.id}`)).json()).position));
  await group.getByRole('button', { name: 'Legion settings', exact: true }).click();
  const settings = page.getByRole('complementary', { name: 'Legion settings', exact: true });
  await expect(settings).toBeVisible();
  // Opening settings can pan the viewport on the next animation frame.
  await expect(async () => {
    const sidebarBounds = await settings.boundingBox();
    const groupBounds = await group.boundingBox();
    expect(sidebarBounds!.x - (groupBounds!.x + groupBounds!.width)).toBeCloseTo(12, 0);
    expect(sidebarBounds!.x + sidebarBounds!.width).toBeLessThanOrEqual(1800 - 15);
    expect(sidebarBounds!.y).toBeGreaterThanOrEqual(15);
  }).toPass();
  // The member workspace remains interactive while settings stay open.
  const workspace = page.locator(`.world-card[data-card-id="${members[3].id}"]`);
  await workspace.getByRole('tab', { name: 'Settings', exact: true }).click();
  await workspace.getByRole('tab', { name: 'Activity', exact: true }).click();
  await expect(settings).toBeVisible();
  expect(await Promise.all(members.map(async n => (await (await request.get(`/api/nodes/${n.id}`)).json()).position))).toEqual(memberPositions);
  await expect(settings.getByLabel('Enable shared team settings')).not.toBeChecked();
  await expect(settings.getByLabel('Team instruction', { exact: true })).toHaveCount(0);
  await settings.getByLabel('Enable shared team settings').check();
  await settings.getByLabel('Team instruction', { exact: true }).fill('Plan then review.');
  await settings.getByRole('button', { name: 'Add variable', exact: true }).click();
  await settings.getByLabel('Variable 1 name').fill('goal');
  await settings.getByLabel('Variable 1 value').fill('Reusable goal');
  // Scrolling settings does not zoom or pan the canvas.
  const viewportBefore = await page.locator('.react-flow__viewport').getAttribute('style');
  await settings.getByLabel('Variable 1 value').hover();
  await page.mouse.wheel(0, 400);
  await expect(page.locator('.react-flow__viewport')).toHaveAttribute('style', viewportBefore!);
  await expect.poll(() => settings.locator('.legion-controls').evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  await page.screenshot({ path: 'test-results/legion-settings-light.png' });
  await settings.getByRole('button', { name: 'Close Legion settings' }).click();
  // Closing and reopening must retain an unsaved shared-variable draft.
  await group.getByRole('button', { name: 'Legion settings', exact: true }).click();
  await expect(settings.getByLabel('Variable 1 value')).toHaveValue('Reusable goal');
  await settings.getByLabel('Variable 1 value').press('Escape');
  await expect(settings).toHaveCount(0);
  await expect(group.getByRole('button', { name: 'Legion settings', exact: true })).toBeFocused();
  const savedResponse = page.waitForResponse(r => r.url().endsWith('/api/legions') && r.request().method() === 'POST');
  await group.getByRole('button', { name: 'Save to library', exact: true }).click();
  const saved = await savedResponse;
  expect(saved.status()).toBe(201);
  const presentation = saved.request().postDataJSON().presentation;
  expect(members.map(n => presentation[n.id].level)).toEqual(levels);
  expect(presentation[members[3].id].surface_sizes.workspace).toEqual({ width: 1100, height: 740 });
  expect((await (await request.get(`/api/legion-groups/${groupId}/state`)).json()).value).toEqual({ goal: 'Reusable goal' });
  const positions = await Promise.all(members.map(async n => (await (await request.get(`/api/nodes/${n.id}`)).json()).position));
  const instanceResponse = page.waitForResponse(r => r.url().includes('/instances') && r.request().method() === 'POST');
  await page.getByRole('tab', { name: /Legions/ }).click();
  await page.getByRole('button', { name: 'Place Reusable layout', exact: true }).click();
  const instance = await (await instanceResponse).json();
  const copies = instance.nodes.filter((n: { type: string }) => n.type === 'agent').sort((a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name));
  for (let i = 0; i < 4; i++) {
    await expect(page.locator(`.world-card[data-card-id="${copies[i].id}"]`)).toHaveAttribute('data-surface-level', levels[i]);
    expect(copies[i].position.x - copies[0].position.x).toBeCloseTo(positions[i].x - positions[0].x, 6);
    expect(copies[i].position.y - copies[0].position.y).toBeCloseTo(positions[i].y - positions[0].y, 6);
  }
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await page.screenshot({ path: 'test-results/legion-restored-layout.png' });
  await page.getByRole('button', { name: 'Use dark theme' }).click();
  await group.getByRole('button', { name: 'Legion settings', exact: true }).click();
  while (await page.locator('.toast button').count()) await page.locator('.toast button').first().click();
  await page.screenshot({ path: 'test-results/legion-settings-dark.png' });
  await settings.getByRole('button', { name: 'Close Legion settings' }).click();
  await page.setViewportSize({ width: 1000, height: 760 });
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await group.getByRole('button', { name: 'Legion settings', exact: true }).click();
  await expect(settings).toBeVisible();
  await settings.getByRole('button', { name: 'Close Legion settings' }).focus();
  await page.waitForTimeout(350); // React Flow focus panning must not displace the attached sidebar.
  const compactBounds = await settings.boundingBox();
  expect(compactBounds!.x + compactBounds!.width).toBeLessThanOrEqual(985);
  expect(compactBounds!.y).toBeGreaterThanOrEqual(15);
  expect(compactBounds!.y + compactBounds!.height).toBeLessThanOrEqual(661);
  await page.screenshot({ path: 'test-results/legion-settings-small.png' });
});
