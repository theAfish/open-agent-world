import { expect, test } from '@playwright/test';

test('procedural grid stays world-anchored through pan, resize and theme changes', async ({ page, request }) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-theme': 'light',
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: { viewport: { x: 0, y: 0, zoom: 1, width: 1280, height: 800 } } }),
    },
  } });
  await page.goto('/');
  const background = page.locator('.terrain-webgl-background');
  await expect(background).toHaveAttribute('data-terrain-status', 'ready');
  // Isolate the procedural grid pixels without changing camera or renderer code.
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--contour', 'rgba(0,0,0,0)');
    document.documentElement.style.setProperty('--contour-fill', 'rgba(0,0,0,0)');
  });
  const clip = { x: 500, y: 300, width: 144, height: 144 };
  const original = await page.screenshot({ clip });
  const pan = async (dx: number) => {
    await page.mouse.move(800, 500); await page.mouse.down({ button: 'middle' });
    await page.mouse.move(800 + dx, 500, { steps: 6 }); await page.mouse.up({ button: 'middle' });
  };
  // At zoom 1 the full major/minor lattice repeats every 48 CSS pixels.
  await pan(48);
  expect((await page.screenshot({ clip })).equals(original)).toBe(true);
  await pan(7);
  expect((await page.screenshot({ clip })).equals(original)).toBe(false);
  await pan(-7);
  expect((await page.screenshot({ clip })).equals(original)).toBe(true);
  await page.setViewportSize({ width: 1700, height: 1000 });
  expect((await page.screenshot({ clip })).equals(original)).toBe(true);
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  expect((await page.screenshot({ clip })).equals(original)).toBe(false);
  await expect(page.locator('.world-grid')).toHaveCount(0);
});
