import { expect, test, type Page } from '@playwright/test';

// Host integration: the shell and React Flow panels share viewport space.
async function expectSeparate(page: Page, selectors: string[]) {
  const boxes = await Promise.all(selectors.map(async selector => {
    const element = page.locator(selector);
    await expect(element).toBeVisible();
    return (await element.boundingBox())!;
  }));
  const viewport = page.viewportSize()!;
  for (const [i, box] of boxes.entries()) {
    expect(box.x, selectors[i]).toBeGreaterThanOrEqual(0);
    expect(box.y, selectors[i]).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, selectors[i]).toBeLessThanOrEqual(viewport.width);
    expect(box.y + box.height, selectors[i]).toBeLessThanOrEqual(viewport.height);
    for (let j = i + 1; j < boxes.length; j++) {
      const other = boxes[j];
      const overlap = Math.min(box.x + box.width, other.x + other.width) - Math.max(box.x, other.x) > 0
        && Math.min(box.y + box.height, other.y + other.height) - Math.max(box.y, other.y) > 0;
      expect(overlap, `${viewport.width}x${viewport.height}: ${selectors[i]} ${JSON.stringify(box)} overlaps ${selectors[j]} ${JSON.stringify(other)}`).toBe(false);
    }
  }
}

for (const touch of [false, true]) test.describe(touch ? 'touch HUD' : 'mouse HUD', () => {
test.use({ hasTouch: touch, isMobile: touch });
test('corner controls stay separate from the Deck across responsive breakpoints', async ({ page, request }) => {
  test.setTimeout(90_000);
  const profile = await (await request.get('/api/application')).json();
  const card = await (await request.post('/api/nodes', { data: {
    type: 'text', name: 'Field notes', position: { x: 140, y: 330 },
  } })).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 390, height: 844 } } }),
      'oaw-node-surfaces-v1': JSON.stringify({ version: 4, state: { surfaceLevels: { [card.id]: 'preview' } } }),
    },
  } })).ok()).toBe(true);
  let library = await (await request.get('/api/card-library')).json();
  for (const id of library.available_pack_ids) library = await (await request.post('/api/card-library/actions', {
    data: { action: 'open_pack', id, expected_revision: library.revision },
  })).json();
  expect((await request.post('/api/card-library/actions', { data: {
    action: 'update_deck', id: library.active_deck_id, entries: [{ kind: 'node', id: 'text' }], expected_revision: library.revision,
  } })).ok()).toBe(true);
  // Render the development badge too: production profiles omit it, which hid
  // the user's actual World-menu overlap from the original layout regression.
  await page.route('**/api/application', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), mode: 'development' } });
  });
  try {
  await page.goto('/');
  await expect(page.locator('.development-badge')).toBeVisible();
  for (const [width, height] of [[1600, 900], [1280, 800], [1251, 800], [1250, 800], [1024, 768], [820, 650], [620, 650], [440, 956], [390, 844], [320, 568], [844, 390]]) {
    await page.setViewportSize({ width, height });
    await page.mouse.move(width / 2, height / 2);
    const compact = width <= 700;
    const panels = compact ? ['.top-bar', '.mobile-canvas-toolbar', '.component-palette']
      : ['.top-bar', '.world-controls', '.world-minimap', '.map-toolbar', '.component-palette'];
    await expect(async () => { await expectSeparate(page, panels); }).toPass({ timeout: 3000 });
    const bar = (await page.locator('.top-bar').boundingBox())!;
    expect(width <= 1250 ? bar.y < 20 : bar.y > height / 2).toBe(true);
    if (compact) {
      await expect(page.locator('.world-minimap')).toHaveCount(0);
      await expect(page.locator('.map-toolbar')).toHaveCount(0);
      await expect.poll(async () => (await page.locator('.component-palette').boundingBox())!.height).toBeLessThanOrEqual(60);
      await expectSeparate(page, [...panels, '.development-badge']);
      if (width === 390 || width === 440) await page.screenshot({ path: `test-results/hud-${touch ? 'touch' : 'mouse'}-${width}-resting.png` });
      await page.getByRole('button', { name: 'Expand card deck', exact: true }).click();
    } else await page.locator('.deck-tabs').hover();
    await expect(page.locator('.deck-stage')).toHaveCSS('opacity', '1');
    await expect(page.locator('[data-palette-card="text"]')).toBeVisible();
    await expect(async () => { await expectSeparate(page, panels); }).toPass({ timeout: 3000 });
    await page.mouse.move(width / 2, height / 2);
    if (compact) {
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await expectSeparate(page, [...panels, '.mobile-canvas-options']);
      if (width === 390) {
        await expect.poll(async () => (await page.locator('.component-palette').boundingBox())!.height).toBeLessThanOrEqual(60);
        await page.screenshot({ path: `test-results/hud-${touch ? 'touch' : 'mouse'}-${width}-tools.png` });
      }
      await page.getByRole('button', { name: 'Map overview', exact: true }).click();
      await expectSeparate(page, [...panels, '.world-minimap']);
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await page.getByRole('button', { name: 'Map overview', exact: true }).click();
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Move cards', exact: true })).toHaveCount(0);
      await page.getByRole('button', { name: 'Pins', exact: true }).click();
      await expectSeparate(page, [...panels, '.map-atlas']);
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await expect(page.locator('.map-atlas')).toHaveCount(0);
      await expectSeparate(page, [...panels, '.mobile-canvas-options']);
      await page.getByRole('button', { name: 'Map overview', exact: true }).click();
      await expect(page.locator('.world-minimap')).toBeVisible();
      await expect(page.locator('.map-atlas')).toHaveCount(0);
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await page.getByRole('button', { name: 'Pins', exact: true }).click();
      await page.getByRole('button', { name: 'Close pin tools', exact: true }).click();
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await page.getByRole('button', { name: 'Glue', exact: true }).click();
      await expectSeparate(page, [...panels, '.glue-tool-hint']);
      await page.locator('.mobile-canvas-mode').getByRole('button', { name: 'Done', exact: true }).click();
      await expect(page.locator('.glue-tool-hint')).toHaveCount(0);
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await page.getByRole('button', { name: 'Glue', exact: true }).click();
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await expect(page.locator('.glue-tool-hint')).toHaveCount(0);
      await page.getByRole('button', { name: 'Map overview', exact: true }).click();
      await expect(page.locator('.world-minimap')).toBeVisible();
      await expect(page.locator('.glue-tool-hint')).toHaveCount(0);
      await page.getByRole('button', { name: 'Canvas tools', exact: true }).click();
      await page.getByRole('button', { name: 'Map overview', exact: true }).click();
    } else if (width <= 1250 && height >= 568) {
      await page.locator('.map-toolbar button').first().click();
      await expectSeparate(page, [...panels, '.map-atlas']);
      await page.locator('.map-toolbar button').first().click();
      await page.locator('.map-toolbar button').nth(1).click();
      await expectSeparate(page, [...panels, '.glue-tool-hint']);
      await page.locator('.map-toolbar button').nth(1).click();
    }
    if (width === 390 || width === 1280) {
      if (compact) await page.getByRole('button', { name: 'Expand card deck', exact: true }).click();
      else await page.locator('.deck-tabs').hover();
      await expect(page.locator('.deck-stage')).toHaveCSS('opacity', '1');
      await page.screenshot({ path: `test-results/hud-${touch ? 'touch' : 'mouse'}-${width}.png` });
      if (compact) await page.getByRole('button', { name: 'Collapse card deck', exact: true }).click();
    }
  }
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});
});
