import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const screenshotDirectory = process.env.OAW_KB_SCREENSHOT_DIR ?? '../.outputs/knowledge-ui/projects';

test('knowledge organizes project materials and direct Space results, preserving experiment and private settings flows', async ({ page, request }) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(15_000);
  mkdirSync(screenshotDirectory, { recursive: true });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    const url = message.location().url;
    const fixture404 = ['/auth/me', '/favicon.ico'].some(path => url.endsWith(path)) && message.text().includes('status of 404');
    if (message.type() === 'error' && !fixture404) errors.push(message.text() + ' at ' + url);
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
  const resource = async (action: string, args: Record<string, unknown> = {}) => {
    const result = await request.post('/api/nodes/' + ids.knowledge + '/resource/' + action, { data: { arguments: args } });
    expect(result.ok(), await result.text()).toBe(true);
    return result.json();
  };
  const openWorkspace = async () => {
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.locator('[data-card-id="' + ids.group + '"]').getByRole('button', { name: 'Workspace mode', exact: true }).click();
  };
  try {
    await page.goto('/');
    await openWorkspace();
    const workspace = page.getByRole('dialog', { name: 'Lab Manager workspace mode' });
    const app = workspace.locator('.knowledge-app');
    const capture = async (name: string) => {
      const size = await app.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
      expect(size.scroll, name + ' overflows its ' + size.width + 'px pane').toBeLessThanOrEqual(size.width + 1);
      await app.screenshot({ path: path.join(screenshotDirectory, name + '.png') });
    };
    await expect(app.getByRole('tab', { name: 'Projects', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(app.getByText('Bring your materials together')).toBeVisible();
    await capture('projects-empty-light-wide');
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await capture('projects-empty-dark-wide');
    await page.setViewportSize({ width: 1000, height: 850 });
    await capture('projects-empty-dark-narrow');
    await page.setViewportSize({ width: 1900, height: 1100 });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });

    await app.getByLabel('Project files or archives', { exact: true }).setInputFiles([
      { name: 'diffusion-in-solid-state-electrolytes-molecular-dynamics-report.md', mimeType: 'text/markdown', buffer: Buffer.from('# Transport report\n\n## Results\n\n| Sample | Conductivity |\n| --- | --- |\n| Li6PS5Cl | 1.2 mS/cm |\n\nImpedance spectroscopy was used.') },
      { name: 'supplement.md', mimeType: 'text/markdown', buffer: Buffer.from('# Supporting data\n\nMeasurements were taken at 300 K.') },
    ]);
    await expect(app.getByText('2 files → 2 projects')).toBeVisible();
    await app.getByLabel('Split projects by', { exact: true }).selectOption('single');
    await expect(app.getByText('2 files → 1 projects')).toBeVisible();
    await app.getByRole('textbox', { name: /Project name for/ }).fill('Transport study');
    await capture('project-import-preview-light-wide');
    await app.getByRole('button', { name: 'Import and convert', exact: true }).click();
    const project = app.getByRole('button', { name: /Transport study.*files ready/ });
    await expect(project).toContainText('2 / 2 files ready', { timeout: 30_000 });
    await expect(app.getByLabel('Select Transport study', { exact: true })).toBeChecked();
    await project.click();
    await expect(app.getByRole('button', { name: 'Create a Space', exact: true })).toBeVisible();
    await expect(app.getByRole('button', { name: 'Run Space', exact: true })).toHaveCount(0);
    await capture('project-selected-before-space-light-wide');
    await app.getByRole('button', { name: 'diffusion-in-solid-state-electrolytes-molecular-dynamics-report.md', exact: true }).click();
    await expect(app.getByRole('heading', { name: 'Transport report', exact: true })).toBeVisible();
    await expect(app.locator('.project-reader table')).toBeVisible();
    await capture('project-reader-light-wide');

    await app.getByRole('tab', { name: 'Spaces', exact: true }).click();
    await expect(app.getByText('Define what you want to extract')).toBeVisible();
    await app.getByRole('button', { name: 'Structured data', exact: true }).click();
    await app.getByLabel('Space name', { exact: true }).fill('Reported transport');
    await app.getByLabel('Extraction instructions', { exact: true }).fill('Read conductivity values and preserve units from all files in each project.');
    await app.getByLabel('Output schema', { exact: true }).fill(JSON.stringify({
      type: 'object', required: ['conductivity', 'unit'], properties: { conductivity: { type: 'number' }, unit: { type: 'string' } },
    }, null, 2));
    await app.getByRole('tab', { name: 'Projects', exact: true }).click();
    await app.getByRole('tab', { name: 'Spaces', exact: true }).click();
    await expect(app.getByLabel('Space name', { exact: true })).toHaveValue('Reported transport');
    await capture('space-editor-light-wide');
    await app.getByRole('button', { name: 'Save Space', exact: true }).click();
    await expect(app.getByRole('heading', { name: 'Reported transport', exact: true })).toBeVisible();
    await app.getByRole('button', { name: 'Use Space', exact: true }).click();
    await expect(app.getByLabel('Select Transport study', { exact: true })).toBeChecked();
    await expect(app.getByRole('button', { name: 'Run Space', exact: true })).toBeEnabled();
    await app.getByRole('button', { name: 'Close document', exact: true }).click();
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await capture('project-selected-dark-wide');
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });

    const { projects } = await resource('projects');
    const savedProject = projects.find((item: { name: string }) => item.name === 'Transport study');
    const { spaces } = await resource('spaces', { operation: 'list' });
    const savedSpace = spaces.find((item: { name: string }) => item.name === 'Reported transport');
    // Seed the agent's schema-valid answer through the real resource boundary; no external model is used.
    const prepared = await resource('projection_prepare', { project_ids: [savedProject.id], space_id: savedSpace.id });
    const savedResult = prepared.projections[0];
    const mechanismNotes = 'Low-barrier ion transport follows connected oxygen coordination environments. Uncertainty and source-specific conditions must be retained. '.repeat(18);
    const records = Array.from({ length: 6 }, (_, index) => ({
      composition: `${index + 1}.0Li2O–TaCl5`, ionic_conductivity_300K_mS_cm1: 2.5 + index,
      key_mechanisms: mechanismNotes, evidence_source_ids: ['cdd67b47-8bce-503a-bd98-2d6355ed8e53', 'supporting-data-source'],
      activation_energy_eV: null, bridging_O_fraction: null, dopant: index ? 'F' : null,
      electrochemical_window_V: null, computational_method: 'Molecular dynamics', temperature_K: 300,
      uncertainty_notes: 'No numerical value was reported for this field.', metadata: { source_section: 'Results', conditions: ['300 K', 'ambient pressure'] },
    }));
    await resource('projection_save', { projection_id: savedResult.id, expected_revision: 1, data: { conductivity: 1.2, unit: 'mS/cm', records } });
    await app.getByRole('button', { name: 'Refresh projects', exact: true }).click();
    await app.getByRole('tab', { name: 'Results', exact: true }).click();
    await app.getByRole('button', { name: /Transport study.*Reported transport/ }).click();
    await expect(app.getByText('1.2', { exact: true })).toBeVisible();
    await expect(app.getByRole('button', { name: 'Review result', exact: true })).toBeEnabled();
    await expect(app.getByRole('button', { name: 'Publication stage', exact: true })).toHaveCount(0);
    await capture('project-result-light-wide');
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    const resultTable = app.getByRole('region', { name: 'records table', exact: true });
    const tableSize = await resultTable.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
    expect(tableSize.scroll).toBeGreaterThan(tableSize.width);
    expect((await resultTable.locator('tbody tr').first().boundingBox())!.height).toBeLessThan(80);
    await expect(app.getByLabel('Show history', { exact: true })).toHaveCSS('color-scheme', 'dark');
    await capture('project-result-table-dark-wide');
    const notes = resultTable.getByRole('button', { name: 'View records · 1 · key_mechanisms', exact: true });
    await notes.hover();
    await expect(notes).toHaveAttribute('title', mechanismNotes);
    await notes.click();
    const cellDialog = workspace.getByRole('dialog', { name: 'records · 1 · key_mechanisms', exact: true });
    await expect(cellDialog.locator('.knowledge-value-fulltext')).toHaveText(mechanismNotes);
    await cellDialog.screenshot({ path: path.join(screenshotDirectory, 'result-cell-fulltext-dark.png') });
    await page.keyboard.press('Escape');
    await expect(cellDialog).toHaveCount(0);
    await expect(notes).toBeFocused();
    await resultTable.evaluate(element => { element.scrollLeft = element.scrollWidth; });
    const resultsLeft = (await app.locator('.project-result-list').boundingBox())!.x;
    expect(resultsLeft).toBeGreaterThanOrEqual((await app.boundingBox())!.x);
    expect(await app.locator('.knowledge-grid').evaluate(element => element.scrollLeft)).toBe(0);
    await capture('project-result-table-scrolled-dark-wide');
    await resultTable.evaluate(element => { element.scrollLeft = 0; });
    await page.setViewportSize({ width: 1000, height: 900 });
    await capture('project-result-table-dark-narrow');
    await page.setViewportSize({ width: 1900, height: 1100 });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
    for (const selected of [false, true]) {
      await app.getByRole('tab', { name: 'Projects', exact: true }).click();
      await app.getByLabel('Select Transport study', { exact: true }).setChecked(selected);
      await app.getByRole('tab', { name: 'Results', exact: true }).click();
      expect((await resource('projections', { operation: 'list' })).projections.map((item: { id: string }) => item.id)).toEqual([savedResult.id]);
    }
    await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
    await openWorkspace();
    await app.getByRole('tab', { name: 'Results', exact: true }).click();
    await expect(app.getByText('1.2', { exact: true })).toBeVisible();

    await app.getByRole('button', { name: 'Settings', exact: true }).click();
    const token = app.getByLabel('MinerU token', { exact: true });
    await token.fill('browser-test-private-token');
    await app.getByRole('button', { name: 'Save token', exact: true }).click();
    await expect(app.getByText('Saved securely', { exact: true })).toBeVisible();
    await expect(token).toHaveValue('');
    await capture('settings-light-wide');
    await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await openWorkspace();
    await app.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(app.getByText('Saved securely', { exact: true })).toBeVisible();
    await expect(app.getByLabel('MinerU token', { exact: true })).toHaveValue('');
    await expect(app.getByRole('button', { name: 'Save token', exact: true })).toBeDisabled();
    expect(await (await request.get('/api/knowledge/' + ids.knowledge + '/mineru-token')).json()).toEqual({ configured: true, source: 'card' });
    await app.getByRole('button', { name: 'Remove token', exact: true }).click();
    await expect(app.getByRole('button', { name: 'Remove token', exact: true })).toHaveCount(0);

    await app.getByRole('button', { name: 'Materials', exact: true }).click();
    await app.getByRole('tab', { name: 'Projects', exact: true }).click();
    await app.getByRole('button', { name: /Transport study.*files ready/ }).click();
    await app.getByRole('button', { name: 'Rename project', exact: true }).click();
    const longTitle = 'Analysis of Diffusion in Solid-State Electrolytes through MD';
    await app.getByLabel('Project name', { exact: true }).fill(longTitle);
    await app.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(app.getByRole('heading', { name: longTitle, exact: true })).toBeVisible();
    await app.getByLabel('Select ' + longTitle, { exact: true }).setChecked(true);
    await app.getByRole('combobox', { name: 'Extraction Space', exact: true }).selectOption(savedSpace.id);
    // Real CSS must preserve compact horizontal controls and leave room for long project names.
    const selectAll = await app.locator('.project-select-all').boundingBox();
    const batch = await app.locator('.project-batchbar').boundingBox();
    const picker = app.locator('.project-batchbar .project-space-picker');
    const icon = await picker.locator('svg').boundingBox();
    const select = await picker.locator('select').boundingBox();
    expect(selectAll!.height).toBeLessThan(40);
    expect(batch!.height).toBeLessThan(90);
    expect(Math.abs(icon!.y + icon!.height / 2 - select!.y - select!.height / 2)).toBeLessThan(2);
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await capture('project-long-title-dark-wide');
    await page.setViewportSize({ width: 1200, height: 1000 });
    await capture('project-long-title-dark-medium');
    await page.setViewportSize({ width: 1900, height: 1100 });
    await app.getByRole('button', { name: 'Rename project', exact: true }).click();
    await app.getByLabel('Project name', { exact: true }).fill('Transport studies');
    await app.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(app.getByRole('button', { name: /Transport studies.*files ready/ })).toBeVisible();
    const divider = workspace.getByRole('separator').first();
    await divider.focus();
    await divider.press('Home');
    await expect.poll(() => app.evaluate(element => element.clientWidth)).toBeLessThanOrEqual(350);
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await capture('projects-dark-minimum');
    await app.getByRole('tab', { name: 'Spaces', exact: true }).click();
    await capture('spaces-dark-minimum');
    await app.getByRole('tab', { name: 'Results', exact: true }).click();
    await capture('results-dark-minimum');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(app.locator('.project-workspace:visible')).toBeVisible();
    await page.emulateMedia({ reducedMotion: 'no-preference' });

    await app.getByRole('button', { name: 'Experiment', exact: true }).click();
    await expect(app.getByText('What will you measure?')).toBeVisible();
    await capture('experiment-empty-dark-minimum');
    await app.getByRole('tab', { name: 'Insights', exact: true }).click();
    await expect(app.getByText('Insights come from published records')).toBeVisible();
    await capture('insights-empty-dark-minimum');
    await app.getByRole('button', { name: 'Open experiments', exact: true }).click();
    await app.getByRole('button', { name: 'Define record fields', exact: true }).click();
    await app.getByRole('button', { name: 'New schema', exact: true }).click();
    await app.getByLabel('Name', { exact: true }).fill('Conductivity measurements');
    await app.getByLabel('System prompt', { exact: true }).fill('Read each specimen name, measured value and unit.');
    await app.getByRole('button', { name: 'Create schema', exact: true }).click();
    await app.getByRole('tab', { name: 'Experiments', exact: true }).click();
    await expect(app.getByLabel('Choose files for a new experiment upload', { exact: true })).toBeEnabled();
    await capture('experiment-upload-dark-minimum');
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
    await capture('experiment-upload-light-minimum');
    await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
    const currentProfile = await (await request.get('/api/application')).json();
    expect((await request.patch('/api/application/preferences', { data: {
      profile_id: currentProfile.profile_id, generation: currentProfile.generation, changes: { 'oaw.locale': 'zh-CN', 'oaw-theme': 'dark' },
    } })).ok()).toBe(true);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '适配视图', exact: true }).click();
    await page.locator('[data-card-id="' + ids.group + '"]').getByRole('button', { name: '工作区模式', exact: true }).click();
    const chineseApp = page.locator('.legion-workspace .knowledge-app');
    await expect(chineseApp.getByRole('tab', { name: '项目', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(chineseApp.getByRole('button', { name: /Transport studies/ })).toBeVisible();
    const chineseSize = await chineseApp.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
    expect(chineseSize.scroll).toBeLessThanOrEqual(chineseSize.width + 1);
    await chineseApp.screenshot({ path: path.join(screenshotDirectory, 'projects-chinese-dark-minimum.png') });
    expect(errors).toEqual([]);
  } catch (error) {
    if (errors.length) console.error('Knowledge browser errors:', errors);
    await page.screenshot({ path: path.join(screenshotDirectory, 'failure.png') });
    throw error;
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: Object.values(ids) } });
  }
});
