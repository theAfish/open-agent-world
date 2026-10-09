import { expect, test } from '@playwright/test';

test('knowledge documents, groups and private token survive real HTTP save and reopen', async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 1900, height: 1100 });
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw.locale': 'en', 'oaw-theme': 'light', 'oaw-node-surfaces-v1': null,
    },
  } })).ok()).toBe(true);
  const response = await request.post('/api/legions/presets/knowledge.base.research/instances', { data: {} });
  expect(response.status()).toBe(201);
  const { node_ids: ids } = await response.json();
  const openWorkspace = async () => {
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.locator(`[data-card-id="${ids.group}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).click();
  };
  try {
    await page.goto('/');
    await openWorkspace();
    const workspace = page.getByRole('dialog', { name: 'Knowledge research workspace mode' });
    const app = workspace.locator('.knowledge-app');
    await expect(app.getByRole('heading', { name: /Documents/ })).toBeVisible();
    await expect(app.getByLabel('Group', { exact: true })).toHaveCount(1);
    await expect(app.getByText('Add your first document')).toBeVisible();
    await app.getByLabel('Add documents', { exact: true }).setInputFiles({
      name: 'Lithium-ion transport in solid electrolytes.md', mimeType: 'text/markdown', buffer: Buffer.from(
        '# Lithium-ion transport in solid electrolytes\n\n## Results\n\nConductivity increased with temperature in the measured samples.\n\n| Sample | Temperature | Conductivity |\n| --- | --- | --- |\n| Li6PS5Cl | 300 K | 1.2 mS/cm |\n| Li6PS5Br | 300 K | 0.8 mS/cm |\n\n## Method\n\nImpedance spectroscopy was used to measure the pellets. Each result is linked to its source document.\n\n## Notes\n\nThese observations are awaiting review.'),
    });
    await app.getByRole('button', { name: 'Process all pending (1)', exact: true }).click();
    const document = app.getByRole('button', { name: /Lithium-ion transport.*Ready/ });
    await expect(document).toBeVisible({ timeout: 30_000 });
    await document.click();
    await expect(app.locator('.knowledge-prose table')).toBeVisible();
    await expect(app.getByText('0 projections')).toHaveCount(0);
    await expect(app.locator('.knowledge-jobs')).not.toHaveAttribute('open', '');
    await app.screenshot({ path: '../.outputs/mkb-refactor-documents-light.png' });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await app.screenshot({ path: '../.outputs/mkb-refactor-documents-dark.png' });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
    await app.getByRole('button', { name: 'Markdown', exact: true }).click();
    await expect(app.locator('pre.knowledge-markdown')).toContainText('# Lithium-ion');
    await app.getByRole('button', { name: 'Markdown', exact: true }).click();

    await app.getByRole('button', { name: 'Settings', exact: true }).click();
    const token = app.getByLabel('MinerU token', { exact: true });
    await token.fill('browser-test-private-token');
    await app.getByRole('button', { name: 'Save token', exact: true }).click();
    await expect(app.getByText('Saved securely', { exact: true })).toBeVisible();
    await expect(token).toHaveValue('');
    await app.screenshot({ path: '../.outputs/mkb-refactor-settings.png' });
    await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
    await expect(workspace).toBeHidden();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await openWorkspace();
    await app.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(app.getByText('Saved securely', { exact: true })).toBeVisible();
    await expect(app.getByLabel('MinerU token', { exact: true })).toHaveValue('');
    await expect(app.getByRole('button', { name: 'Save token', exact: true })).toBeDisabled();
    expect(await (await request.get(`/api/knowledge/${ids.knowledge}/mineru-token`)).json()).toEqual({ configured: true, source: 'card' });
    await app.getByRole('button', { name: 'Remove token', exact: true }).click();
    await expect(app.getByRole('button', { name: 'Remove token', exact: true })).toHaveCount(0);

    await app.getByRole('button', { name: 'Literature', exact: true }).click();
    await app.getByLabel('Group', { exact: true }).selectOption({ label: 'Knowledge base' });
    await app.getByLabel('Manage groups', { exact: true }).click();
    await app.getByRole('button', { name: 'New group', exact: true }).click();
    await app.getByLabel('Group name', { exact: true }).fill('Transport studies');
    await app.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(app.getByLabel('Group', { exact: true })).toHaveValue(/.+/);
    const groups = await (await request.post(`/api/nodes/${ids.knowledge}/resource/groups`, { data: { arguments: {} } })).json();
    expect(groups.groups.map((group: { name: string }) => group.name)).toEqual(expect.arrayContaining(['Knowledge base', 'Transport studies']));
    await app.getByLabel('Group', { exact: true }).selectOption('');
    await app.getByRole('button', { name: /Lithium-ion transport.*Ready/ }).click();
    await page.setViewportSize({ width: 1000, height: 850 });
    await expect(app.locator('.knowledge-prose table')).toBeVisible();
    const size = await app.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
    expect(size.scroll).toBeLessThanOrEqual(size.width + 1);
    await app.screenshot({ path: '../.outputs/mkb-refactor-documents-narrow.png' });
    expect(errors).toEqual([]);
  } catch (error) {
    await page.screenshot({ path: '../.outputs/mkb-refactor-failure.png' });
    throw error;
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: Object.values(ids) } });
  }
});
