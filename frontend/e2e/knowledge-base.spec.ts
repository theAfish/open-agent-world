import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const screenshotDirectory = process.env.OAW_KB_SCREENSHOT_DIR ?? '../.outputs/knowledge-ui/final';

test('knowledge guides documents into drafts and records, with saved groups and private settings', async ({ page, request }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(15_000);
  mkdirSync(screenshotDirectory, { recursive: true });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    const url = message.location().url;
    // The local fixture has neither hosted auth nor a favicon endpoint.
    const fixture404 = ['/auth/me', '/favicon.ico'].some(path => url.endsWith(path)) && message.text().includes('status of 404');
    if (message.type() === 'error' && !fixture404) errors.push(`${message.text()} at ${url}`);
  });
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
    const workspace = page.getByRole('dialog', { name: 'Lab Manager workspace mode' });
    const app = workspace.locator('.knowledge-app');
    const capture = async (name: string) => {
      const size = await app.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
      expect(size.scroll, `${name} overflows its ${size.width}px pane`).toBeLessThanOrEqual(size.width + 1);
      await app.screenshot({ path: path.join(screenshotDirectory, `${name}.png`) });
    };
    await expect(app.getByRole('tab', { name: 'Sources', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(app.getByRole('combobox', { name: 'Group', exact: true })).toHaveCount(1);
    await expect(app.getByText('Add your first document')).toBeVisible();
    await expect(app.getByLabel('Add documents', { exact: true })).toBeEnabled();
    await app.screenshot({ path: path.join(screenshotDirectory, 'empty-light-wide.png') });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await app.screenshot({ path: path.join(screenshotDirectory, 'empty-dark-wide.png') });
    await page.setViewportSize({ width: 1000, height: 850 });
    await app.screenshot({ path: path.join(screenshotDirectory, 'empty-dark-narrow.png') });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
    await app.screenshot({ path: path.join(screenshotDirectory, 'empty-light-narrow.png') });
    await page.setViewportSize({ width: 1900, height: 1100 });

    await app.getByRole('tab', { name: 'Knowledge', exact: true }).click();
    await expect(app.getByText('Your knowledge graph starts with a source')).toBeVisible();
    await capture('knowledge-empty-light-wide');
    await app.getByRole('button', { name: 'Open Sources', exact: true }).click();
    await expect(app.getByRole('tab', { name: 'Sources', exact: true })).toHaveAttribute('aria-selected', 'true');
    await app.getByRole('tab', { name: 'Records', exact: true }).click();
    await expect(app.getByText('Compare what your papers report')).toBeVisible();
    await expect(app.getByLabel('Record workflow').getByRole('button', { name: /Review and publish/ })).toBeDisabled();
    await capture('records-empty-light-wide');
    await app.getByRole('button', { name: 'Add sources', exact: true }).click();

    await app.getByLabel('Add documents', { exact: true }).setInputFiles({
      name: 'Lithium-ion transport in solid electrolytes.md', mimeType: 'text/markdown', buffer: Buffer.from(
        '# Lithium-ion transport in solid electrolytes\n\n## Results\n\nConductivity increased with temperature in the measured samples.\n\n| Sample | Temperature | Conductivity |\n| --- | --- | --- |\n| Li6PS5Cl | 300 K | 1.2 mS/cm |\n| Li6PS5Br | 300 K | 0.8 mS/cm |\n\n## Method\n\nImpedance spectroscopy was used to measure the pellets. Each result is linked to its source document.\n\n## Notes\n\nThese observations are awaiting review.'),
    });
    const document = app.getByRole('button', { name: /Lithium-ion transport/ });
    await expect(document).toBeVisible();
    await expect(document.locator('small')).toContainText('Ready', { timeout: 30_000 });
    const goals = app.getByRole('group', { name: 'Use your documents', exact: true });
    await expect(goals).toBeVisible();
    await capture('sources-ready-light-wide');
    await app.getByRole('checkbox', { name: 'Select Lithium-ion transport in solid electrolytes.md', exact: true }).check();
    await goals.getByRole('button', { name: /Build knowledge/ }).click();
    await expect(app.getByRole('tab', { name: 'Knowledge', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(app.getByRole('checkbox', { name: 'Select Lithium-ion transport in solid electrolytes.md', exact: true })).toBeChecked();
    await capture('knowledge-create-light-wide');
    await app.getByLabel('Title', { exact: true }).fill('Solid electrolyte transport');
    await app.getByRole('tab', { name: 'Sources', exact: true }).click();
    await app.getByRole('tab', { name: 'Knowledge', exact: true }).click();
    await expect(app.getByLabel('Title', { exact: true })).toHaveValue('Solid electrolyte transport');
    await expect(app.getByRole('checkbox', { name: 'Select Lithium-ion transport in solid electrolytes.md', exact: true })).toBeChecked();
    await app.getByRole('button', { name: 'Create draft from 1 source(s)', exact: true }).click();
    await expect(app.getByRole('button', { name: 'Extraction stage', exact: true })).toHaveAttribute('aria-current', 'step');
    await expect(app.getByRole('button', { name: 'Review stage', exact: true })).toBeDisabled();
    await expect(app.getByRole('button', { name: 'Publication stage', exact: true })).toBeDisabled();
    const listDrafts = async () => (await (await request.post(`/api/nodes/${ids.knowledge}/resource/knowledge_drafts`,
      { data: { arguments: { all_groups: true } } })).json()).drafts;
    const [savedDraft] = await listDrafts();
    for (const selected of [false, true]) {
      await app.getByRole('tab', { name: 'Sources', exact: true }).click();
      if (selected) await app.getByRole('checkbox', { name: 'Select Lithium-ion transport in solid electrolytes.md', exact: true }).check();
      await goals.getByRole('button', { name: /Build knowledge/ }).click();
      await expect(app.getByRole('heading', { name: 'Solid electrolyte transport', exact: true })).toBeVisible();
      await expect(app.getByLabel('Title', { exact: true })).toHaveCount(0);
      expect((await listDrafts()).map((item: { id: string }) => item.id)).toEqual([savedDraft.id]);
    }
    await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
    await openWorkspace();
    await app.getByRole('tab', { name: 'Knowledge', exact: true }).click();
    await expect(app.getByRole('heading', { name: 'Solid electrolyte transport', exact: true })).toBeVisible();
    await capture('knowledge-draft-light-wide');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(app.locator('.knowledge-flow-panel:visible')).toHaveCSS('animation-name', 'none');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await app.getByRole('tab', { name: 'Sources', exact: true }).click();
    await goals.getByRole('button', { name: /Extract records/ }).click();
    await expect(app.getByRole('tab', { name: 'Records', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(app.getByRole('button', { name: 'New schema', exact: true })).toBeVisible();
    await capture('records-setup-light-wide');
    await app.getByRole('tab', { name: 'Sources', exact: true }).click();
    await goals.getByRole('button', { name: /Read documents/ }).click();
    await expect(app.locator('.knowledge-prose table')).toBeVisible();
    await expect(app.getByText('0 projections')).toHaveCount(0);
    await expect(app.locator('.knowledge-jobs')).not.toHaveAttribute('open', '');
    await app.screenshot({ path: path.join(screenshotDirectory, 'documents-light-wide.png') });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await app.screenshot({ path: path.join(screenshotDirectory, 'documents-dark-wide.png') });
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
    await app.screenshot({ path: path.join(screenshotDirectory, 'settings-light-wide.png') });
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
    await app.getByRole('combobox', { name: 'Group', exact: true }).selectOption({ label: 'Knowledge base' });
    await app.getByLabel('Manage groups', { exact: true }).click();
    await app.getByRole('button', { name: 'New group', exact: true }).click();
    await app.getByLabel('Group name', { exact: true }).fill('Transport studies');
    await app.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(app.getByRole('combobox', { name: 'Group', exact: true })).toHaveValue(/.+/);
    const groups = await (await request.post(`/api/nodes/${ids.knowledge}/resource/groups`, { data: { arguments: {} } })).json();
    expect(groups.groups.map((group: { name: string }) => group.name)).toEqual(expect.arrayContaining(['Knowledge base', 'Transport studies']));
    await app.getByRole('combobox', { name: 'Group', exact: true }).selectOption('');
    await expect(document.locator('small')).toContainText('Ready');
    await document.click();
    await page.setViewportSize({ width: 1000, height: 850 });
    await expect(app.locator('.knowledge-prose table')).toBeVisible();
    const size = await app.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
    expect(size.scroll).toBeLessThanOrEqual(size.width + 1);
    await app.screenshot({ path: path.join(screenshotDirectory, 'documents-light-narrow.png') });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await app.screenshot({ path: path.join(screenshotDirectory, 'documents-dark-narrow.png') });

    // Resize the actual composed pane to its minimum, independent of viewport width.
    const divider = workspace.getByRole('separator', { name: 'Resize workspace regions', exact: true }).first();
    await divider.press('Home');
    await expect.poll(() => app.evaluate(element => element.clientWidth)).toBeLessThanOrEqual(350);
    await capture('documents-dark-minimum');
    await app.getByRole('tab', { name: 'Knowledge', exact: true }).click();
    await expect(app.getByRole('button', { name: /Solid electrolyte transport/ })).toBeVisible();
    await app.getByRole('button', { name: /Solid electrolyte transport/ }).click();
    await capture('knowledge-draft-dark-minimum');
    await app.getByRole('tab', { name: 'Records', exact: true }).click();
    await capture('records-setup-dark-minimum');
    await app.getByRole('button', { name: 'Experiment', exact: true }).click();
    await expect(app.getByText('What will you measure?')).toBeVisible();
    await capture('experiment-empty-dark-minimum');
    await app.getByRole('tab', { name: 'Insights', exact: true }).click();
    await expect(app.getByText('Insights come from published records')).toBeVisible();
    await capture('insights-empty-dark-minimum');
    await app.getByRole('button', { name: 'Open experiments', exact: true }).click();
    await expect(app.getByRole('tab', { name: 'Experiments', exact: true })).toHaveAttribute('aria-selected', 'true');
    await app.getByRole('button', { name: 'Define record fields', exact: true }).click();
    await expect(app.getByRole('tab', { name: 'Schemas', exact: true })).toHaveAttribute('aria-selected', 'true');
    await app.getByRole('button', { name: 'New schema', exact: true }).click();
    await app.getByLabel('Name', { exact: true }).fill('Conductivity measurements');
    await app.getByLabel('System prompt', { exact: true }).fill('Read each specimen name, measured value and unit.');
    await app.getByRole('button', { name: 'Create schema', exact: true }).click();
    await app.getByRole('tab', { name: 'Experiments', exact: true }).click();
    await expect(app.getByLabel('Choose files for a new experiment upload', { exact: true })).toBeEnabled();
    await capture('experiment-upload-dark-minimum');
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
    await capture('experiment-upload-light-minimum');
    await app.getByRole('button', { name: 'Literature', exact: true }).click();
    await app.getByRole('tab', { name: 'Sources', exact: true }).click();
    await app.getByRole('combobox', { name: 'Group', exact: true }).selectOption({ label: 'Transport studies' });
    await expect(app.getByText('Add your first document')).toBeVisible();
    await capture('empty-light-minimum');
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await capture('empty-dark-minimum');
    await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
    const currentProfile = await (await request.get('/api/application')).json();
    expect((await request.patch('/api/application/preferences', { data: {
      profile_id: currentProfile.profile_id, generation: currentProfile.generation,
      changes: { 'oaw.locale': 'zh-CN', 'oaw-theme': 'dark' },
    } })).ok()).toBe(true);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '适配视图', exact: true }).click();
    await page.locator(`[data-card-id="${ids.group}"]`).getByRole('button', { name: '工作区模式', exact: true }).click();
    const chineseApp = page.locator('.legion-workspace .knowledge-app');
    await expect(chineseApp.getByRole('tab', { name: '来源', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(chineseApp.getByRole('button', { name: /Lithium-ion transport/ })).toBeVisible();
    const chineseSize = await chineseApp.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
    expect(chineseSize.scroll).toBeLessThanOrEqual(chineseSize.width + 1);
    await chineseApp.screenshot({ path: path.join(screenshotDirectory, 'sources-ready-chinese-dark-minimum.png') });
    expect(errors).toEqual([]);
  } catch (error) {
    if (errors.length) console.error('Knowledge browser errors:', errors);
    await page.screenshot({ path: path.join(screenshotDirectory, 'failure.png') });
    throw error;
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: Object.values(ids) } });
  }
});
