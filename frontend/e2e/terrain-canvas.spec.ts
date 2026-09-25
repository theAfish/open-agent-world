import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

test.use({ trace: 'off' });

async function prepare(request: APIRequestContext, zoom: number) {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: {
        viewport: { x: 100, y: 100, zoom, width: 1280, height: 800 }, mapPins: [],
      } }),
    },
  } })).ok()).toBe(true);
}

async function coverage(page: Page) {
  return page.evaluate(() => {
    const world = document.querySelector('#oaw-world-map');
    const viewport = world?.querySelector('.react-flow__viewport');
    if (!world || !viewport) return false;
    const matrix = new DOMMatrix(getComputedStyle(viewport).transform);
    const keys = new Set(Array.from(world.querySelectorAll('svg.contour-chunk')).map(tile => tile.getAttribute('data-chunk')));
    for (let y = Math.floor(-matrix.f / matrix.a / 2048); y <= Math.floor((world.clientHeight - matrix.f) / matrix.a / 2048); y++) {
      for (let x = Math.floor(-matrix.e / matrix.a / 2048); x <= Math.floor((world.clientWidth - matrix.e) / matrix.a / 2048); x++) {
        if (!keys.has(`${x}:${y}`)) return false;
      }
    }
    return true;
  });
}

async function ready(page: Page) {
  await expect.poll(() => coverage(page), { timeout: 25_000 }).toBe(true);
  await expect.poll(() => page.locator('canvas.contour-canvas').count(), { timeout: 20_000 }).toBeGreaterThan(0);
  await expect(page.locator('.contour-canvas-experiment')).toHaveAttribute('data-raster-pending', '0', { timeout: 20_000 });
  await expect(page.locator('.contour-canvas-experiment')).toHaveAttribute('data-fallback', '', { timeout: 20_000 });
  const budget = await page.locator('.contour-canvas-experiment').evaluate(element => ({
    bytes: Number((element as HTMLElement).dataset.rasterBytes),
    peak: Number((element as HTMLElement).dataset.rasterPeakBytes),
    maximum: Number((element as HTMLElement).dataset.rasterBudget),
  }));
  expect(budget.bytes).toBeGreaterThan(0);
  expect(budget.peak).toBeLessThanOrEqual(budget.maximum);
}

test('Canvas experiment preserves world geometry, themes, seed changes and warm return coverage', async ({ page, request }) => {
  test.setTimeout(100_000);
  await prepare(request, 0.35);
  let seed = 123;
  await page.route(/\/api\/world(?:\?|$)/, async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), terrain_seed: seed } });
  });
  await page.goto('/?terrainRenderer=canvas');
  const loadedSeed = () => page.evaluate(async () => {
    const modulePath = '/src/state/worldStore.ts';
    const { useWorldStore } = await import(/* @vite-ignore */ modulePath);
    return useWorldStore.getState().terrainSeed;
  });
  await expect.poll(loadedSeed, { timeout: 25_000 }).toBe(123);
  await ready(page);
  const original = await page.locator('svg.contour-chunk[data-chunk="0:0"] .contour-minor').getAttribute('d');
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await page.waitForTimeout(200);
    await ready(page);
    await page.screenshot({ path: `../.outputs/terrain-canvas-${theme}.png` });
    const style = await page.addStyleTag({ content: '.contour-canvas { visibility: hidden !important; } svg.contour-chunk[data-canvas-ready="true"] { visibility: visible !important; }' });
    await page.screenshot({ path: `../.outputs/terrain-svg-${theme}.png` });
    await style.evaluate(element => element.remove());
  }
  // Cache entries follow the world transform exactly, including padded edges.
  const positions = await page.locator('canvas.contour-canvas').evaluateAll(canvases => canvases.map(element => {
    const canvas = element as HTMLCanvasElement;
    const [x, y] = canvas.dataset.chunk!.split(':').map(Number);
    const zoom = Number(canvas.dataset.rasterZoom), dpr = Number(canvas.dataset.rasterDpr);
    const padding = Math.ceil(2 * dpr) / (zoom * dpr);
    return { zoom, dx: parseFloat(canvas.style.left) + padding - x * 2048,
      dy: parseFloat(canvas.style.top) + padding - y * 2048 };
  }));
  // CSSOM serializes large world offsets with limited decimal precision.
  positions.forEach(position => {
    expect(Math.abs(position.dx * position.zoom)).toBeLessThan(0.01);
    expect(Math.abs(position.dy * position.zoom)).toBeLessThan(0.01);
  });
  for (const [from, to] of [[1100, 200], [200, 1100]]) {
    await page.mouse.move(from, 160);
    await page.mouse.down();
    await page.mouse.move(to, 160, { steps: 20 });
    await page.mouse.up();
    await ready(page);
  }
  seed = 456;
  await page.reload();
  await expect.poll(loadedSeed, { timeout: 25_000 }).toBe(456);
  await expect.poll(() => page.locator('svg.contour-chunk[data-chunk="0:0"] .contour-minor').getAttribute('d'),
    { timeout: 25_000 }).not.toBe(original);
  await ready(page);
  await page.screenshot({ path: '../.outputs/terrain-canvas-new-seed.png' });
});

test('Canvas experiment uses sharp SVG and releases rasters when high DPR exceeds its budget', async ({ browser, request }) => {
  test.setTimeout(60_000);
  await prepare(request, 0.12);
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  try {
    await page.goto(`${process.env.OAW_E2E_BASE_URL ?? 'http://127.0.0.1:5177'}/?terrainRenderer=canvas`);
    await ready(page);
    const zoom = () => page.locator('#oaw-world-map > .react-flow__renderer .react-flow__viewport').first()
      .evaluate(element => new DOMMatrix(getComputedStyle(element).transform).a);
    for (let step = 0; step < 20 && await zoom() < 2.2; step++) {
      const previous = await zoom();
      await page.locator('.world-controls .react-flow__controls-zoomin').click();
      await expect.poll(zoom).toBeCloseTo(Math.min(2.2, previous * 1.2), 4);
    }
    await expect.poll(() => coverage(page), { timeout: 25_000 }).toBe(true);
    const experiment = page.locator('.contour-canvas-experiment');
    await expect(experiment).toHaveAttribute('data-fallback', 'budget');
    await expect(experiment).toHaveAttribute('data-raster-bytes', '0');
    await expect(page.locator('canvas.contour-canvas')).toHaveCount(0);
    await expect(page.locator('svg.contour-chunk[data-canvas-ready="true"]')).toHaveCount(0);
    const stroke = await page.locator('.contour-minor').first().evaluate(element => parseFloat(getComputedStyle(element).strokeWidth));
    expect(stroke * 2.2).toBeCloseTo(1.15, 3);
    await page.screenshot({ path: '../.outputs/terrain-canvas-high-dpr-svg-fallback.png' });
  } finally { await context.close(); }
});
