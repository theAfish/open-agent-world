import { expect, test } from '@playwright/test';

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } });
});

test('Jobs card presents generic providers and controls in standalone and narrow composed workspaces', async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const boardResponse = await request.post('/api/nodes', { data: { type: 'core.jobs', name: 'Research jobs', position: { x: 300, y: 300 } } });
  expect(boardResponse.status()).toBe(201);
  const board = await boardResponse.json();
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en', 'oaw-theme': 'light',
  } } });
  const jobs = ['running', 'queued', 'succeeded', 'unknown', 'failed', 'running'].map((status, index) => ({
    id: `job-${index}`, provider: index % 2 ? 'core.sandbox-operation' : 'matcreator.bohrium-batch',
    request_key: ['Copper relaxation at 300 K', 'Validate candidate structures', 'Molecular dynamics trajectory', 'Reconcile remote submission', 'Training potential', 'Compare conductivity'][index],
    external_id: `external-${index}-abcdef0123456789abcdef0123456789`, status, detail: 'Latest status reported by the compute provider.',
    paused: index === 5, wake_requested: false, revision: 2, created_at: 1791961200, updated_at: 1791961320,
    last_observed_at: index === 3 ? null : 1791961320, next_check_at: 1791961380, observation_error: null, tracking_released: false,
    agent_id: 'agent', agent_name: 'Researcher', sandbox_id: 'sandbox', sandbox_name: 'Research files & compute', source_run_id: 'run-123',
    actions: index === 0 ? ['refresh', 'pause', 'cancel'] : [],
  }));
  const actions: string[] = [];
  await page.route(`**/api/nodes/${board.id}/jobs**`, async route => {
    if (route.request().method() === 'POST') {
      const action = route.request().postDataJSON().action;
      actions.push(action);
      jobs[0].paused = action !== 'resume';
      jobs[0].actions = ['refresh', jobs[0].paused ? 'resume' : 'pause', 'cancel'];
      jobs[0].revision++;
      await route.fulfill({ json: jobs[0] });
    } else await route.fulfill({ json: { sources: [{ id: 'agent', name: 'Researcher', type: 'agent', can_manage: true }], jobs } });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await expect(page.locator(`[data-card-id="${board.id}"]`)).toBeAttached();
  await page.evaluate(async id => {
    // @ts-expect-error Vite development module used to open the production workspace.
    const { useNodeSurfaceStore } = await import('/src/state/nodeSurfaces.ts' /* @vite-ignore */);
    useNodeSurfaceStore.getState().openWorkspace(id);
  }, board.id);
  let workspace = page.getByRole('dialog', { name: 'Research jobs workspace', exact: true });
  await expect(workspace.locator('.job-tile')).toHaveCount(6);
  await expect(page.locator(`.react-flow__node[data-id="${board.id}"]`)).toHaveCSS('width', '1020px');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await expect(workspace.getByRole('button', { name: 'Refresh jobs' })).toBeInViewport();
  await expect(workspace.getByRole('textbox', { name: 'Search jobs' })).toHaveCount(0);
  await expect(workspace.getByRole('article', { name: 'Training potential' }).getByText('Failed', { exact: true })).toBeVisible();
  const compactRatio = await workspace.locator('.job-tile').first().evaluate(el => el.getBoundingClientRect().width / el.parentElement!.getBoundingClientRect().width);
  expect(compactRatio).toBeLessThan(.5);
  expect(compactRatio).toBeGreaterThan(.45);
  await page.screenshot({ path: '../.outputs/jobs/standalone-light.png' });
  await workspace.getByRole('button', { name: 'Show job details' }).first().click();
  await expect(workspace.locator('.job-metadata')).toContainText('external-0-abcdef0123456789abcdef0123456789');
  expect(await workspace.locator('.job-tile').first().evaluate(el => el.getBoundingClientRect().width / el.parentElement!.getBoundingClientRect().width)).toBeCloseTo(1, 3);
  await workspace.getByRole('button', { name: 'Pause monitoring', exact: true }).click();
  await expect(workspace.getByRole('button', { name: 'Resume monitoring', exact: true })).toBeEnabled();
  expect(actions).toEqual(['pause']);
  await workspace.getByRole('button', { name: 'Cancel external job', exact: true }).click();
  expect(actions).toEqual(['pause']);
  await workspace.getByRole('group', { name: 'Cancel external job' }).getByRole('button', { name: 'Cancel external job' }).click();
  await expect.poll(() => actions).toEqual(['pause', 'cancel']);
  await expect(workspace.locator('.job-tile').first()).toHaveAttribute('data-status', 'running');
  await workspace.locator('.jobs-board').evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: '../.outputs/jobs/details-light.png' });
  await workspace.getByRole('button', { name: 'Close workspace', exact: true }).click();

  const notes = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Research notes', position: { x: 700, y: 300 } } })).json();
  const group = (await (await request.post('/api/legion-groups', { data: { name: 'Research studio', node_ids: [board.id, notes.id] } })).json())[0];
  await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2, hidden_sections: [], root: {
    kind: 'split', axis: 'horizontal', ratio: .3, first: { kind: 'pane', view: { card_id: board.id } }, second: { kind: 'pane', view: { card_id: notes.id } },
  } } } } });
  await page.reload();
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await page.locator(`[data-card-id="${group.id}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).evaluate((button: HTMLButtonElement) => button.click());
  workspace = page.getByRole('dialog', { name: 'Research studio workspace mode', exact: true });
  await expect(workspace.locator('.job-tile')).toHaveCount(6);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
    await page.setViewportSize({ width: 1000, height: 800 });
    expect(await workspace.locator('.jobs-board').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    const heights = await workspace.locator('.job-tile').evaluateAll(tiles => tiles.map(tile => tile.getBoundingClientRect().height));
    expect(Math.max(...heights) - Math.min(...heights)).toBeLessThan(1);
    const lines = await workspace.locator('.job-tile').first().evaluate(tile => ({
      title: tile.querySelector('.job-tile-title')!.getBoundingClientRect().bottom,
      status: tile.querySelector('.job-tile-state')!.getBoundingClientRect().top,
    }));
    expect(lines.status).toBeGreaterThan(lines.title);
    await page.screenshot({ path: `../.outputs/jobs/composed-narrow-${theme}.png` });
  }
  await workspace.getByRole('combobox', { name: 'Filter jobs' }).click();
  await page.screenshot({ path: '../.outputs/jobs/composed-filter-dark.png' });
  await page.getByRole('listbox', { name: 'Filter jobs' }).getByRole('option', { name: 'Needs attention · 4' }).click();
  await expect(workspace.locator('.job-tile')).toHaveCount(4);
  await workspace.getByRole('combobox', { name: 'Filter jobs' }).selectOption('all');
  await workspace.getByRole('button', { name: 'Search jobs' }).click();
  await expect(workspace.getByRole('textbox', { name: 'Search jobs' })).toBeFocused();
  expect(await workspace.locator('.jobs-board').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  const filterBox = (await workspace.getByRole('combobox', { name: 'Filter jobs' }).boundingBox())!;
  const searchBox = (await workspace.getByRole('textbox', { name: 'Search jobs' }).boundingBox())!;
  expect(Math.abs((filterBox.y + filterBox.height / 2) - (searchBox.y + searchBox.height / 2))).toBeLessThan(2);
  await page.screenshot({ path: '../.outputs/jobs/composed-search-dark.png' });
  await workspace.getByRole('textbox', { name: 'Search jobs' }).fill('no matching record');
  await expect(workspace.getByText('No matching jobs', { exact: true })).toBeVisible();
  await workspace.getByRole('button', { name: 'Close job search' }).click();
  await expect(workspace.locator('.job-tile')).toHaveCount(6);
  await expect(workspace.getByRole('textbox', { name: 'Search jobs' })).toHaveCount(0);
  const narrowTile = workspace.getByRole('article', { name: 'Training potential' });
  // The card padding is clickable too; there is no separate expand control.
  await narrowTile.click({ position: { x: 5, y: 5 } });
  expect(await narrowTile.evaluate(el => el.getBoundingClientRect().width / el.parentElement!.getBoundingClientRect().width)).toBeCloseTo(1, 3);
  await page.screenshot({ path: '../.outputs/jobs/composed-details-dark.png' });
  await narrowTile.getByRole('button', { name: 'Hide job details' }).click();
  expect(await narrowTile.evaluate(el => el.getBoundingClientRect().width / el.parentElement!.getBoundingClientRect().width)).toBeLessThan(.5);
  await narrowTile.getByRole('button', { name: 'Show job details' }).focus();
  await page.keyboard.press('Enter');
  await expect(narrowTile.getByRole('button', { name: 'Hide job details' })).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Space');
  await expect(narrowTile.getByRole('button', { name: 'Show job details' })).toHaveAttribute('aria-expanded', 'false');
  expect(errors).toEqual([]);
});

test('MatCreator preset composes the real session-scoped Jobs card beneath its graph', async ({ page, request }) => {
  const result = await request.post('/api/legions/presets/matcreator.native-research/instances', { data: {} });
  expect(result.status(), await result.text()).toBe(201);
  const instance = await result.json();
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en', 'oaw-theme': 'light',
    'oaw-active-workspace-v1': null, 'oaw-node-surfaces-v1': null,
  } } });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  // This checks composition, not the separate canvas fit/viewport gesture.
  await page.locator(`[data-card-id="${instance.node_ids.group}"]`).getByRole('button', { name: 'Workspace mode', exact: true })
    .evaluate((button: HTMLButtonElement) => button.click());
  const workspace = page.getByRole('dialog', { name: 'MatCreator research workspace mode', exact: true });
  const jobs = workspace.getByRole('region', { name: 'Jobs', exact: true });
  await expect(jobs.getByText('No jobs in this session', { exact: true })).toBeVisible();
  const graph = workspace.getByRole('region', { name: 'Execution graph', exact: true });
  await expect(graph).toBeVisible();
  const a = (await graph.boundingBox())!, b = (await jobs.boundingBox())!;
  expect(b.y).toBeGreaterThan(a.y + a.height - 1);
  expect(Math.abs(b.x - a.x)).toBeLessThan(2);
  for (const width of [1440, 1000]) {
    await page.setViewportSize({ width, height: 900 });
    const fits = await workspace.locator('.legion-layout-stage').evaluate(el => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1);
    expect(fits).toBe(true);
    await expect(jobs).toBeVisible();
    await page.screenshot({ path: `../.outputs/jobs/matcreator-${width}.png` });
  }
});
