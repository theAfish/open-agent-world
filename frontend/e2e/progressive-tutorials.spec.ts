import { expect, test } from '@playwright/test';

test('card encounters, dismissal, offline replay and preferences survive reload', async ({ page, request }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-theme': 'light',
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-progressive-tutorials-v1': null, 'oaw-active-workspace-v1': null,
      'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null },
  } })).ok()).toBe(true);
  const created = await request.post('/api/nodes', { data: { type: 'text', name: 'Tutorial note', position: { x: 330, y: 250 } } });
  expect(created.status()).toBe(201);
  const id = (await created.json()).id;
  try {
    await page.goto('/');
    const card = page.locator(`[data-card-id="${id}"]`);
    await expect(card).toBeVisible();
    await expect(page.locator('.progressive-tutorial')).toHaveCount(0);
    await card.click();
    const reader = page.locator('.progressive-tutorial');
    await expect(reader).toContainText('Give your Agent reference material');
    await reader.getByRole('button', { name: 'View tutorial', exact: true }).click();
    await expect(reader).toContainText('Write your note');
    await reader.getByRole('button', { name: 'Continue', exact: true }).click();
    await expect(reader).toContainText('Choose access');
    await page.screenshot({ path: info.outputPath('tutorial-light.png') });
    await reader.getByRole('button', { name: 'Dismiss this tutorial' }).click();
    await expect.poll(async () => JSON.stringify((await (await request.get('/api/application')).json()).values['oaw-progressive-tutorials-v1'])).toContain('dismissed');
    await page.reload();
    await card.click();
    await expect(reader).toHaveCount(0);
    await page.getByRole('button', { name: 'Help', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Card tutorials & docs' }).click();
    const library = page.getByRole('dialog', { name: 'Card tutorials & docs' });
    await library.getByRole('checkbox').uncheck();
    await library.getByRole('searchbox').fill('reference material');
    await library.getByRole('button', { name: 'View tutorial', exact: true }).click();
    await expect(reader).toContainText('Choose access');
    await page.setViewportSize({ width: 390, height: 700 });
    await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
    await expect(reader.getByRole('button', { name: 'Finish tutorial' })).toBeInViewport();
    await page.screenshot({ path: info.outputPath('tutorial-dark-small.png') });
    await reader.getByRole('button', { name: 'Finish tutorial' }).click();
    await page.getByRole('button', { name: 'Help', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Card tutorials & docs' }).click();
    await library.getByRole('searchbox').fill('Meet your Agent');
    await library.getByRole('button', { name: 'Read documentation' }).click();
    await expect(reader.getByRole('heading', { name: 'Agent cards' })).toBeVisible();
    await reader.getByRole('button', { name: 'Dismiss this tutorial' }).click();
    await expect.poll(async () => JSON.parse((await (await request.get('/api/application')).json()).values['oaw-progressive-tutorials-v1']).state.enabled).toBe(false);
    await page.reload();
    await page.getByRole('button', { name: 'Help', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Card tutorials & docs' }).click();
    await expect(library.getByRole('checkbox')).not.toBeChecked();
    expect(errors).toEqual([]);
  } finally { await request.post('/api/nodes/batch-delete', { data: { node_ids: [id] } }); }
});
