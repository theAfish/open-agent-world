import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

test('touch details fill the screen and preserve one draft, desktop presentation and canvas position', async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const response = await request.post('/api/nodes', { data: {
    type: 'text', name: 'Phone draft', position: { x: 0, y: 0 },
  } });
  expect(response.ok()).toBe(true);
  const card = await response.json();
  const profile = await (await request.get('/api/application')).json();
  const changes = {
    'oaw.locale': 'en',
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    'oaw-active-workspace-v1': null,
    'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 80, y: 160, zoom: .55, width: 390, height: 844 },
      mapPins: [{ id: 'far-pin', name: 'Far pin', x: 5000, y: 5000, zoom: 1 }] } }),
    'oaw-node-surfaces-v1': JSON.stringify({ version: 4, state: {
      surfaceLevels: { [card.id]: 'inspector' }, baseLevels: { [card.id]: 'preview' },
      surfaceSizes: { [card.id]: { inspector: { width: 1000, height: 800 } } },
    } }),
  };
  try {
    expect((await request.patch('/api/application/preferences', { data: {
      profile_id: profile.profile_id, generation: profile.generation, changes,
    } })).ok()).toBe(true);
    await page.goto('/');
    const preview = page.locator(`.world-card[data-card-id="${card.id}"]`);
    await expect(preview).toHaveAttribute('data-surface-level', 'preview');
    const viewport = page.locator('#oaw-world-map .react-flow__viewport').first();
    const camera = await viewport.evaluate(element => getComputedStyle(element).transform);
    await preview.tap();
    const dialog = page.locator(`dialog.mobile-card-surface[data-mobile-card-id="${card.id}"]`);
    await expect(dialog).toBeVisible();
    await page.keyboard.press('1');
    expect(await viewport.evaluate(element => getComputedStyle(element).transform)).toBe(camera);
    const bounds = (await dialog.boundingBox())!;
    expect(bounds).toMatchObject({ x: 0, y: 0, width: 390, height: 844 });
    const editor = dialog.getByRole('textbox', { name: 'Contents', exact: true });
    await expect(editor).toBeEnabled();
    expect(await editor.evaluate(element => element.closest('.react-flow__viewport') === null)).toBe(true);
    await editor.fill('Unsaved phone draft');
    await page.setViewportSize({ width: 844, height: 390 });
    await expect(editor).toHaveValue('Unsaved phone draft');
    await page.setViewportSize({ width: 390, height: 844 });
    await dialog.getByRole('button', { name: 'Back to canvas', exact: true }).tap();
    await expect(dialog).toHaveCount(0);
    await expect(preview).toHaveAttribute('data-surface-level', 'preview');
    expect(await viewport.evaluate(element => getComputedStyle(element).transform)).toBe(camera);
    await preview.tap();
    await expect(dialog.getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Unsaved phone draft');
    await page.screenshot({ path: info.outputPath('mobile-card-draft.png') });
    const savedProfile = await (await request.get('/api/application')).json();
    const surfaces = JSON.parse(savedProfile.values['oaw-node-surfaces-v1']).state;
    expect(surfaces.surfaceLevels[card.id]).toBe('inspector');
    expect(surfaces.surfaceSizes[card.id].inspector).toEqual({ width: 1000, height: 800 });
    const savedCard = await (await request.get(`/api/nodes/${card.id}`)).json();
    expect(savedCard.position).toEqual({ x: 0, y: 0 });
  } finally {
    await request.delete(`/api/nodes/${card.id}`);
    await request.patch('/api/application/preferences', { data: {
      profile_id: profile.profile_id, generation: profile.generation,
      changes: Object.fromEntries(Object.keys(changes).map(key => [key, profile.values[key] ?? null])),
    } });
  }
});
