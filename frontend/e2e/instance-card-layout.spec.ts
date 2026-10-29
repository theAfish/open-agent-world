import { expect, test } from '@playwright/test';
import { graphFixture } from './fixtures/executionGraph';
import type { BackgroundJob } from '../src/cards/jobTypes';

test('instance previews give content the space and use type-specific shapes', async ({ page, request }, testInfo) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 960 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    'oaw.locale': 'en', 'oaw-node-surfaces-v1': null, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
  } } });
  const cards = [];
  for (const [type, name, x] of [
    ['core.execution-graph', 'Agent graph', 80],
    ['core.artifact-collection', 'Research artifacts', 520],
    ['core.jobs', 'Research jobs', 804],
  ] as const) {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 180 } } });
    expect(response.status(), await response.text()).toBe(201);
    cards.push(await response.json());
  }
  const [graphCard, artifactCard, jobsCard] = cards;
  let populated = false;
  const source = { id: 'research-agent', name: 'Research agent', type: 'agent', can_manage: false };
  // Real canvas/card rendering, with deterministic read-only runtime observations.
  await page.route(`**/api/nodes/${graphCard.id}/execution-sources`, route => route.fulfill({ json: { sources: [source] } }));
  await page.route(`**/api/nodes/${graphCard.id}/execution-sources/*`, route => route.fulfill({
    json: graphFixture(populated ? 'details' : 'empty'),
  }));
  const jobs: BackgroundJob[] = ['Relax Li6PS5Cl', 'Phonon spectrum', 'Compare structures', 'Verify convergence'].map((name, index) => ({
    id: `job-${index}`, request_key: name, provider: 'Research cluster', external_id: `run-${index}`,
    status: ['running', 'queued', 'succeeded', 'failed'][index], detail: 'Structure search and validation',
    paused: false, wake_requested: false, revision: 1, created_at: 1793145600, updated_at: 1793145660,
    last_observed_at: 1793145660, next_check_at: null, observation_error: null, tracking_released: false,
    agent_id: source.id, agent_name: source.name, sandbox_id: 'research-sandbox', sandbox_name: 'Research sandbox',
    source_run_id: 'research-run', actions: [],
  }));
  await page.route(`**/api/nodes/${jobsCard.id}/jobs`, route => route.fulfill({ json: { sources: [source], jobs: populated ? jobs : [] } }));
  const card = (id: string) => page.locator(`.world-card[data-card-id="${id}"]`);

  for (const state of ['empty', 'populated'] as const) {
    populated = state === 'populated';
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    for (const [id, width, height] of [[graphCard.id, 384, 440], [artifactCard.id, 224, 300], [jobsCard.id, 320, 336]] as const) {
      const surface = card(id);
      await expect(surface).toHaveAttribute('data-surface-level', 'preview');
      await expect(surface).toHaveCSS('width', `${width}px`);
      await expect(surface).toHaveCSS('height', `${height}px`);
      await expect(surface.locator('.node-surface-header')).toHaveCSS('height', '50px');
      await expect(surface.locator('.card-kind-icon, .card-eyebrow')).toHaveCount(0);
      await expect(surface.getByRole('img', { name: /Status: available$/ })).toHaveCSS('border-radius', '50%');
      const contentRatio = await surface.evaluate(element => element.querySelector('.node-preview-body')!.clientHeight / element.clientHeight);
      expect(contentRatio).toBeGreaterThan(.65);
    }
    if (populated) {
      await expect(card(graphCard.id).getByText('4 events')).toBeVisible();
      await expect.poll(() => card(graphCard.id).getByRole('button', { name: 'Verify retained artifacts before deletion: waiting', exact: true }).evaluate(element => {
        const row = element.getBoundingClientRect();
        const viewport = element.closest('.execution-graph-viewport')!.getBoundingClientRect();
        return row.top >= viewport.top && row.bottom <= viewport.bottom;
      })).toBe(true);
      await expect(card(jobsCard.id).locator('.job-tile')).toHaveCount(4);
      await card(jobsCard.id).getByRole('combobox', { name: 'Filter jobs', exact: true }).selectOption('active');
      await expect(card(jobsCard.id).locator('.job-tile')).toHaveCount(2);
      await expect(card(jobsCard.id)).toHaveAttribute('data-surface-level', 'preview');
      await card(jobsCard.id).getByRole('combobox', { name: 'Filter jobs', exact: true }).selectOption('all');
    } else {
      await expect(card(graphCard.id).getByText('No execution yet')).toBeVisible();
      await expect(card(jobsCard.id).getByText('No jobs in this session')).toBeVisible();
      expect(await card(jobsCard.id).locator('.jobs-board').evaluate(element => element.scrollHeight <= element.clientHeight)).toBe(true);
    }
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      await page.mouse.move(10, 10);
      await page.screenshot({ path: testInfo.outputPath(`${state}-${theme}.png`), animations: 'disabled' });
      if (populated) {
        const position = await card(artifactCard.id).evaluate(element => {
          const matrix = new DOMMatrix(getComputedStyle(element.closest('.react-flow__node')!).transform);
          return { x: 32 - matrix.m41 * 3, y: 32 - matrix.m42 * 3 };
        });
        const camera = await page.addStyleTag({ content: `.react-flow__viewport { transform: translate(${position.x}px, ${position.y}px) scale(3) !important; }` });
        await card(artifactCard.id).locator('.node-surface-header').screenshot({ path: testInfo.outputPath(`header-${theme}.png`), animations: 'disabled' });
        await camera.evaluate(element => element.remove());
      }
    }
  }
  const title = card(artifactCard.id).locator('.card-name');
  await title.hover();
  await title.getByRole('button', { name: 'Rename', exact: true }).click();
  const input = title.getByRole('textbox');
  await expect(input).toBeFocused();
  await input.fill('Retained research artifacts and released versions');
  await input.press('Enter');
  await expect(title.getByRole('heading')).toHaveText('Retained research artifacts and released versions');
  const before = (await card(artifactCard.id).boundingBox())!;
  const heading = (await title.getByRole('heading').boundingBox())!;
  const emblem = (await card(artifactCard.id).locator('.card-status-emblem').boundingBox())!;
  expect(emblem.x + emblem.width).toBeLessThan(heading.x);
  await page.mouse.move(heading.x + 10, heading.y + heading.height / 2);
  await page.mouse.down();
  await page.mouse.move(heading.x + 45, heading.y + heading.height / 2 + 50, { steps: 10 });
  await page.mouse.up();
  await expect.poll(async () => (await card(artifactCard.id).boundingBox())!.y - before.y).toBeGreaterThan(30);
  await card(jobsCard.id).getByRole('button', { name: 'Collapse Research jobs card', exact: true }).click();
  await expect(card(jobsCard.id)).toHaveAttribute('data-surface-level', 'node');
  await card(jobsCard.id).getByRole('button', { name: 'Expand Research jobs card', exact: true }).click();
  await expect(card(jobsCard.id)).toHaveCSS('width', '320px');
  await expect(card(jobsCard.id)).toHaveCSS('height', '336px');
  await card(jobsCard.id).getByRole('heading', { name: 'Research jobs' }).click();
  await expect(card(jobsCard.id)).toHaveAttribute('data-surface-level', 'workspace');
  expect(errors).toEqual([]);
  expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: cards.map(item => item.id) } })).ok()).toBe(true);
});
