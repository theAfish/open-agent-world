import { expect, test, type Page } from '@playwright/test';

const canvas = '.terrain-webgl-background';
async function stats(page: Page) {
  return page.locator(canvas).evaluate(element => (element as HTMLCanvasElement & { terrainStats: Record<string, number> }).terrainStats);
}
async function settled(page: Page) {
  await expect(page.locator(canvas)).toBeVisible();
  await expect.poll(async () => {
    const s = await stats(page);
    return s && s.visibleTiles > 0 && s.pendingTiles === 0 && s.coveredTiles === s.visibleTiles;
  }).toBe(true);
  await expect(page.locator('.contour-chunk, .world-grid')).toHaveCount(0);
}

test.beforeEach(async ({ request }) => {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-theme': 'light',
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: { viewport: { x: 100, y: 100, zoom: 1, width: 1280, height: 800 }, mapPins: [] } }),
    },
  } })).ok()).toBe(true);
});

test('camera reuses textures and keeps wheel anchoring and card dragging', async ({ page, request }) => {
  const response = await request.post('/api/nodes', { data: { type: 'text', name: 'WebGL interaction', position: { x: 300, y: 200 } } });
  expect(response.status()).toBe(201);
  const card = await response.json();
  try {
    await page.goto('/');
    await settled(page);
    const initial = await stats(page);
    const view = () => page.locator('#oaw-world-map > .react-flow__renderer .react-flow__viewport').evaluate(el => {
      const m = new DOMMatrix(getComputedStyle(el).transform); return { x: m.e, y: m.f, z: m.a };
    });
    const before = await view();
    await page.mouse.move(900, 600); await page.mouse.down({ button: 'middle' });
    await page.mouse.move(940, 630, { steps: 12 }); await page.mouse.up({ button: 'middle' });
    const panned = await view();
    expect(panned.x - before.x).toBeCloseTo(40, 0);
    expect(panned.y - before.y).toBeCloseTo(30, 0);
    await page.mouse.move(800, 500); await page.mouse.wheel(0, -30);
    await page.waitForTimeout(500);
    const zoomed = await view();
    expect((800 - zoomed.x) / zoomed.z).toBeCloseTo((800 - panned.x) / panned.z, 2);
    expect((500 - zoomed.y) / zoomed.z).toBeCloseTo((500 - panned.y) / panned.z, 2);
    await settled(page);
    expect((await stats(page)).uploads).toBe(initial.uploads);
    const element = page.locator(`[data-card-id="${card.id}"]`);
    const box = (await element.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 90, box.y + box.height / 2 + 45, { steps: 12 }); await page.mouse.up();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${card.id}`)).json()).position.x).toBeGreaterThan(card.position.x + 50);
    expect(await page.locator(canvas).evaluate(el => getComputedStyle(el).pointerEvents)).toBe('none');
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

test('DPR, resize, theme, seed and context recovery preserve coverage', async ({ page }) => {
  await page.goto('/');
  await settled(page);
  const initial = await stats(page);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 2, mobile: false });
  // CDP changes devicePixelRatio without emitting the monitor's resize/media event.
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await expect.poll(async () => (await stats(page)).effectiveDpr).toBe(2);
  expect(await page.locator(canvas).evaluate(el => (el as HTMLCanvasElement).width)).toBe(2560);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 900, deviceScaleFactor: 2, mobile: false });
  await expect.poll(async () => page.locator(canvas).evaluate(el => (el as HTMLCanvasElement).width)).toBe(2800);
  await settled(page);
  const clip = { x: 700, y: 300, width: 180, height: 180 };
  const light = await page.screenshot({ clip });
  await page.getByRole('button', { name: 'Use dark theme', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  expect((await page.screenshot({ clip })).equals(light)).toBe(false);
  expect((await stats(page)).uploads).toBe(initial.uploads);
  await page.evaluate(async () => {
    const { useWorldStore } = await import('/src/state/worldStore.ts' /* @vite-ignore */);
    useWorldStore.setState({ terrainSeed: 7654321 });
  });
  await settled(page);
  expect((await stats(page)).seed).toBe(7654321);
  const seedImage = await page.screenshot({ clip });
  await page.evaluate(() => {
    const gl = document.querySelector<HTMLCanvasElement>('.terrain-webgl-background')!.getContext('webgl2')!;
    (window as any).__terrainLoss = gl.getExtension('WEBGL_lose_context');
    (window as any).__terrainLoss.loseContext();
  });
  await expect(page.locator(canvas)).toHaveAttribute('data-terrain-status', 'context-lost');
  await expect(page.locator('.contour-chunk, .world-grid')).toHaveCount(0);
  await expect(page.locator(canvas)).toBeHidden();
  await page.evaluate(() => (window as any).__terrainLoss.restoreContext());
  await settled(page);
  expect((await stats(page)).restores).toBe(1);
  expect((await page.screenshot({ clip })).equals(seedImage)).toBe(true);
  expect((await stats(page)).textureBytes).toBeLessThanOrEqual(initial.textureBudget);
});

test('GPU cache evicts after long travel and converges after rapid reversal', async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto('/');
  await settled(page);
  await page.mouse.move(700, 500); await page.mouse.wheel(0, 2200); await page.waitForTimeout(600);
  for (let pass = 0; pass < 28; pass++) {
    await page.mouse.move(1150, 500); await page.mouse.down({ button: 'middle' });
    await page.mouse.move(130, 500, { steps: 6 }); await page.mouse.up({ button: 'middle' });
    await settled(page);
    const s = await stats(page);
    expect(s.tiles).toBeLessThanOrEqual(s.tileLimit);
    expect(s.textureBytes).toBeLessThanOrEqual(s.textureBudget);
  }
  expect((await stats(page)).evictions).toBeGreaterThan(100);
  for (const delta of [-1000, 1000, -700, 700]) await page.mouse.wheel(0, delta);
  await settled(page);
});

test('obsolete renderer flags cannot mount SVG and unavailable WebGL2 keeps the canvas interactive', async ({ page }) => {
  await page.goto('/?terrainRenderer=svg');
  await settled(page);
  await page.addInitScript(() => {
    const native = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, ...args: any[]) {
      return type === 'webgl2' ? null : native.apply(this, [type, ...args] as any);
    } as typeof native;
  });
  await page.goto('/');
  await expect(page.locator(canvas)).toHaveAttribute('data-terrain-status', 'unavailable');
  await expect(page.locator(canvas)).toHaveAttribute('data-terrain-error', /unavailable/);
  await expect(page.locator(canvas)).toBeHidden();
  await expect(page.locator('.contour-chunk, .world-grid')).toHaveCount(0);
  const view = () => page.locator('#oaw-world-map .react-flow__viewport').evaluate(el => {
    const m = new DOMMatrix(getComputedStyle(el).transform); return { x: m.e, y: m.f, zoom: m.a };
  });
  const before = await view();
  await page.mouse.move(800, 500); await page.mouse.down({ button: 'middle' });
  await page.mouse.move(850, 530, { steps: 6 }); await page.mouse.up({ button: 'middle' });
  expect((await view()).x - before.x).toBeCloseTo(50, 0);
  await page.mouse.wheel(0, -120);
  await expect.poll(async () => (await view()).zoom).toBeCloseTo(before.zoom * 2 ** .24, 4);
  await page.getByRole('button', { name: 'Use dark theme', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('.world-shell')).toHaveCSS('background-color', 'rgb(34, 33, 30)');
});
