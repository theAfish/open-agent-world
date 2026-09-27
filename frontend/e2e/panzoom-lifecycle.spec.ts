import { expect, test } from '@playwright/test';

test('node shells defer documents and inspector drafts survive viewport culling', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const create = async (type: string, name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 400 } } });
    expect(response.status()).toBe(201);
    return response.json();
  };
  const text = await create('text', 'Culling draft', 400);
  const board = await create('oaw.tasks', 'Summary board', 1000);
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: {
        viewport: { x: 0, y: 0, zoom: 1, width: 1600, height: 1000 }, mapPins: [],
      } }),
      'oaw-node-surfaces-v1': JSON.stringify({ version: 3, state: {
        surfaceLevels: { [text.id]: 'node', [board.id]: 'node' }, baseLevels: {}, maximizedWorkspaces: {},
      } }),
    },
  } })).ok()).toBe(true);
  const reads: string[] = [];
  page.on('request', request => {
    if (request.method() === 'GET' && request.url().includes(`/nodes/${board.id}/document`)) reads.push(request.url());
  });
  const surface = (id: string) => page.locator(`#oaw-world-map .world-card[data-card-id="${id}"]`).first();
  try {
    await page.goto('/');
    await expect(surface(board.id)).toHaveAttribute('data-surface-level', 'node', { timeout: 30_000 });
    await expect(surface(board.id).locator('.node-preview-body')).toHaveCount(0);
    expect(reads).toEqual([]);
    await surface(board.id).hover();
    await surface(board.id).getByRole('button', { name: 'Expand Summary board card', exact: true }).click();
    await expect.poll(() => reads.length).toBe(1);
    expect(new URL(reads[0]).searchParams.get('summary_only')).toBe('true');
    await surface(board.id).locator('.card-kind-icon').click();
    await expect(surface(board.id).getByLabel('New task title')).toBeVisible();
    await expect.poll(() => reads.filter(url => !new URL(url).searchParams.has('summary_only')).length).toBe(1);
    await surface(board.id).getByLabel('New task title').fill('Unsaved task title');
    await surface(board.id).getByRole('button', { name: 'Close Summary board inspector', exact: true }).click();
    await expect(surface(board.id).getByLabel('New task title')).toHaveCount(0);

    await surface(text.id).locator('.card-kind-icon').click();
    const editor = surface(text.id).getByRole('textbox', { name: 'Contents', exact: true });
    await expect(editor).toBeEnabled();
    await editor.fill('Keep this unfinished draft across chunk boundaries.');
    const pane = page.locator('#oaw-world-map > .react-flow__renderer .react-flow__pane').first();
    const pan = async (from: number, to: number) => {
      await page.mouse.move(from, 160);
      expect(await pane.evaluate((element, x) => document.elementFromPoint(x, 160) === element, from)).toBe(true);
      await page.mouse.down();
      await page.mouse.move(to, 160, { steps: 12 });
      await page.mouse.up();
    };
    for (let i = 0; i < 4; i++) await pan(1450, 250);
    await expect(surface(text.id)).toHaveCount(0);
    await expect(surface(board.id)).toHaveCount(0);
    for (let i = 0; i < 4; i++) await pan(250, 1450);
    // Return crosses asynchronous world chunk loads before React Flow remounts.
    await expect(editor).toHaveValue('Keep this unfinished draft across chunk boundaries.', { timeout: 20_000 });
    await surface(board.id).locator('.card-kind-icon').click();
    await expect(surface(board.id).getByLabel('New task title')).toHaveValue('Unsaved task title');
    await surface(text.id).getByRole('button', { name: 'Save text', exact: true }).click();
    await expect.poll(async () => (await (await request.get(`/api/resources/${text.id}/text`)).json()).content)
      .toBe('Keep this unfinished draft across chunk boundaries.');
  } finally {
    // A timed-out browser context may already be closed; preserve the original
    // assertion in the report. The runner owns and resets this isolated profile.
    await Promise.allSettled([request.delete(`/api/nodes/${text.id}`), request.delete(`/api/nodes/${board.id}`)]);
  }
});
