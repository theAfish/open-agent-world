import { expect, test } from '@playwright/test';
import { resetTutorialProfile } from './tutorial-profile';
import { buildTutorialLegion } from './tutorial-legion';
test.use({ actionTimeout: 10_000 });

test('Legion tutorial forms real membership, saves a workspace and survives reload', async ({ page, request }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const ids = {} as Record<'agent' | 'conversation' | 'sandbox', string>;
  for (const [index, type] of (['agent', 'conversation', 'sandbox'] as const).entries()) {
    const response = await request.post('/api/nodes', { data: { type, position: { x: 200 + index * 350, y: 300 } } });
    expect(response.ok()).toBe(true);
    ids[type] = (await response.json()).id;
  }
  await resetTutorialProfile(request, { id: 'legion-walk', step: 'legion-intro', refs: ids, initialIds: [], demos: [] });
  await page.goto('/');
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await buildTutorialLegion(page, ids);
  const cards = await (await request.get('/api/nodes')).json();
  const group = cards.find((card: { type: string }) => card.type === 'legion');
  expect(group.config.mode).toBe('group');
  for (const id of Object.values(ids)) expect(cards.find((card: { id: string }) => card.id === id).parent_id).toBe(group.id);
  expect(JSON.stringify(group.config.workspace_layout)).toContain(ids.conversation);
  expect(JSON.stringify(group.config.workspace_layout)).toContain(ids.sandbox);
  await page.getByRole('button', { name: 'Finish & keep my world', exact: true }).click();
  await page.reload();
  if (await page.getByRole('dialog', { name: 'Settings', exact: true }).isVisible()) await page.locator('[data-tutorial="settings-close"]').click();
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await page.locator('[data-tutorial="legion-open"]').click();
  await expect(page.locator(`[data-workspace-pane="${ids.conversation}"]`)).toBeVisible();
  await expect(page.locator(`[data-workspace-pane="${ids.sandbox}"]`)).toBeVisible();
  expect(errors).toEqual([]);
});

test('Chinese workspace guidance remains usable on a small screen, after pause and reload', async ({ page, request }) => {
  await page.setViewportSize({ width: 900, height: 700 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const refs: Record<string, string> = {};
  for (const [index, type] of ['agent', 'conversation', 'sandbox'].entries()) {
    const response = await request.post('/api/nodes', { data: { type, position: { x: 200 + index * 300, y: 300 } } });
    expect(response.ok()).toBe(true); refs[type] = (await response.json()).id;
  }
  const formed = await request.post('/api/legion-groups', { data: { name: 'Tutorial studio', node_ids: Object.values(refs) } });
  refs.legion = (await formed.json()).find((card: { type: string }) => card.type === 'legion').id;
  await resetTutorialProfile(request, { id: 'small-legion', step: 'legion-layout', refs, initialIds: [], demos: [] });
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: { 'oaw.locale': 'zh-CN', 'oaw-theme': 'dark' } } });
  await page.goto('/');
  await page.locator('.tutorial-next').click();
  // Recovery reopens the existing workspace; it never creates another Legion.
  await page.locator('.tutorial-bubble footer .onboarding-icon-button').click();
  const workspace = page.locator('dialog.legion-workspace');
  const guide = workspace.locator('.tutorial-bubble');
  await expect(guide).toHaveAttribute('data-step', 'legion-layout');
  await expect(guide).toContainText('完成编辑');
  await expect(page.locator('.tutorial-guide')).toHaveAttribute('data-moving', 'false');
  await expect(guide).toBeInViewport();
  const bounds = (await guide.boundingBox())!;
  const stage = (await workspace.locator('.legion-layout-stage').boundingBox())!;
  expect(bounds.y).toBeGreaterThanOrEqual(stage.y + stage.height);
  await guide.locator('header button').first().click();
  await workspace.locator('.tutorial-next').click();
  await page.screenshot({ path: 'test-results/onboarding-legion-small-zh.png' });
  await page.reload();
  await page.locator('.tutorial-next').click();
  await expect(page.locator('.tutorial-dialogue')).toContainText('重新打开');
  await page.locator('.tutorial-bubble footer .onboarding-icon-button').click();
  await workspace.locator(`[data-workspace-source="${refs.conversation}"]`).click();
  await workspace.locator('.legion-layout-empty .primary-button').click();
  const conversationPane = workspace.locator(`[data-workspace-pane="${refs.conversation}"]`);
  const paneBounds = (await conversationPane.boundingBox())!;
  await workspace.locator(`[data-workspace-source="${refs.sandbox}"]`).dragTo(conversationPane, { targetPosition: { x: paneBounds.width - 12, y: paneBounds.height / 2 } });
  await workspace.locator('[data-tutorial="legion-edit"]').click();
  await expect(guide).toHaveAttribute('data-step', 'legion-return');
  await guide.locator('header button').last().click();
  await expect(page.locator('.onboarding-layer')).toHaveCount(0);
  expect((await (await request.get('/api/nodes')).json()).filter((card: { type: string }) => card.type === 'legion')).toHaveLength(1);
});

test.afterEach(async ({ request }) => {
  const cards = await (await request.get('/api/nodes')).json();
  if (cards.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: cards.map((card: { id: string }) => card.id) } })).ok()).toBe(true);
});
