import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

test('touch Deck opens explicitly, swipes without placement, then supports tap and upward-drag placement', async ({ page, request }) => {
  test.setTimeout(60_000);
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-active-workspace-v1': null, 'oaw-node-surfaces-v1': null,
    },
  } })).ok()).toBe(true);
  let library = await (await request.get('/api/card-library')).json();
  for (const id of library.available_pack_ids) library = await (await request.post('/api/card-library/actions', {
    data: { action: 'open_pack', id, expected_revision: library.revision },
  })).json();
  const entries = ['text', ...library.available_card_ids.filter((id: string) => id !== 'text').slice(0, 5)].map(id => ({ kind: 'node', id }));
  expect(entries.length).toBeGreaterThanOrEqual(4);
  expect((await request.post('/api/card-library/actions', { data: {
    action: 'update_deck', id: library.active_deck_id, entries, expected_revision: library.revision,
  } })).ok()).toBe(true);
  const created: string[] = [];
  page.on('request', value => { if (value.method() === 'POST' && value.url().endsWith('/api/card-library/nodes')) created.push(value.url()); });
  await page.goto('/');
  const tray = page.locator('.component-palette:not(.palette-drag-preview)');
  const stage = tray.locator('.deck-stage');
  await expect(stage).toBeHidden();
  await tray.getByRole('button', { name: 'Expand card deck', exact: true }).tap();
  await expect(stage).toBeVisible();
  // Raw CDP gestures bypass actionability; wait out the tray animation first.
  await tray.locator('[data-palette-card="text"]').tap({ trial: true });
  const row = tray.locator('.palette-items');
  const box = (await row.boundingBox())!;
  const session = await page.context().newCDPSession(page);
  const x = box.x + box.width - 16, y = box.y + box.height / 2;
  await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  for (let step = 1; step <= 8; step++) await session.send('Input.dispatchTouchEvent', {
    type: 'touchMove', touchPoints: [{ x: x - step * (box.width - 32) / 8, y }],
  });
  await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect.poll(() => row.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
  expect(created).toHaveLength(0);
  await expect(page.locator('.palette-drag-preview')).toHaveCount(0);
  await tray.getByRole('button', { name: 'Collapse card deck', exact: true }).tap();
  await expect(stage).toBeHidden();
  await tray.getByRole('tab').first().tap();
  const text = tray.locator('[data-palette-card="text"]');
  await expect(text).toBeEnabled();
  await text.scrollIntoViewIfNeeded();
  await text.tap();
  await expect.poll(() => created.length).toBe(1);
  await expect(stage).toBeHidden();
  await tray.getByRole('button', { name: 'Expand card deck', exact: true }).tap();
  await text.tap({ trial: true });
  const source = (await text.boundingBox())!;
  const sx = source.x + source.width / 2, sy = source.y + source.height / 2;
  await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: sx, y: sy }] });
  await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: sx, y: sy - 30 }] });
  await expect(page.locator('.palette-drag-preview')).toBeVisible();
  await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 195, y: 300 }] });
  await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect.poll(() => created.length).toBe(2);
  await expect(page.locator('.palette-drag-preview')).toHaveCount(0);
  const tip = page.locator('.progressive-tutorial.is-hint');
  await expect(tip).toBeVisible();
  const tipBox = (await tip.boundingBox())!, trayBox = (await tray.boundingBox())!;
  expect(tipBox.y + tipBox.height).toBeLessThanOrEqual(trayBox.y);
  await tray.getByRole('button', { name: 'Create a new card deck', exact: true }).tap();
  const name = tray.getByRole('textbox', { name: 'Deck name', exact: true });
  await name.fill('Touch draft');
  await expect(tray.getByRole('button', { name: 'Create deck', exact: true })).toBeVisible();
  const editorBox = (await stage.boundingBox())!, nameBox = (await name.boundingBox())!;
  expect(nameBox.x).toBeGreaterThanOrEqual(editorBox.x);
  expect(nameBox.x + nameBox.width).toBeLessThanOrEqual(editorBox.x + editorBox.width);
  await tray.getByRole('button', { name: 'Cancel creating deck', exact: true }).tap();
});
