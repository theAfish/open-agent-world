import { expect, test, type Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { resetTutorialProfile } from './tutorial-profile';

test.use({ trace: 'off' });

async function coverage(page: Page) {
  return page.evaluate(() => {
    const world = document.querySelector('#oaw-world-map');
    if (!world?.querySelector('.react-flow__viewport')) return false;
    const matrix = new DOMMatrix(getComputedStyle(world.querySelector('.react-flow__viewport')!).transform);
    const keys = new Set(Array.from(world.querySelectorAll('.contour-chunk')).map(el => el.getAttribute('data-chunk')));
    for (let y = Math.floor(-matrix.f / matrix.a / 2048); y <= Math.floor((world.clientHeight - matrix.f) / matrix.a / 2048); y++) {
      for (let x = Math.floor(-matrix.e / matrix.a / 2048); x <= Math.floor((world.clientWidth - matrix.e) / matrix.a / 2048); x++) {
        if (!keys.has(`${x}:${y}`)) return false;
      }
    }
    return true;
  });
}

test('wide viewport terrain coverage and sustained pan cost', async ({ page, request }) => {
  test.setTimeout(110_000);
  await resetTutorialProfile(request);
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: {
        viewport: { x: 100, y: 100, zoom: 0.12, width: 1920, height: 1080 }, mapPins: [],
      } }),
    },
  } })).ok()).toBe(true);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/');
  await expect.poll(() => coverage(page), { timeout: 30_000 }).toBe(true);
  const viewport = page.locator('#oaw-world-map > .react-flow__renderer .react-flow__viewport').first();
  const transform = () => viewport.evaluate(el => {
    const matrix = new DOMMatrix(getComputedStyle(el).transform);
    return { x: matrix.e, zoom: matrix.a };
  });
  expect((await transform()).zoom).toBeCloseTo(0.12, 5);
  await page.getByRole('button', { name: 'Start Empty', exact: true }).click();
  await page.waitForTimeout(1500);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await cdp.send('Performance.enable');
  const reports = [];
  for (const mode of process.env.OAW_WIDE_COMPARE ? ['normal', 'hidden', 'uncomposited'] : ['normal']) {
    const style = mode === 'normal' ? undefined : await page.addStyleTag({ content: mode === 'hidden'
      ? '.contour-chunk { visibility: hidden !important; }'
      : '.contour-chunk { will-change: auto !important; }' });
    await page.waitForTimeout(500);
    await page.mouse.move(1600, 130);
    expect(await page.evaluate(() => document.elementFromPoint(1600, 130)?.classList.contains('react-flow__pane'))).toBe(true);
    await page.evaluate(() => {
      const w = window as any;
      w.wideFrames = []; w.wideActive = true;
      let previous = performance.now();
      const tick = (now: number) => {
        w.wideFrames.push(now - previous); previous = now;
        if (w.wideActive) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    const events: any[] = [];
    const collect = ({ value }: { value: any[] }) => events.push(...value);
    cdp.on('Tracing.dataCollected', collect);
    await cdp.send('Tracing.start', { categories: 'devtools.timeline', transferMode: 'ReportEvents' });
    await page.mouse.down();
    const origin = (await transform()).x;
    const before = await cdp.send('Performance.getMetrics');
    for (let pass = 0; pass < 2; pass++) {
      await page.mouse.move(1200, 130, { steps: 45 });
      expect((await transform()).x).toBeCloseTo(origin - 400, 2);
      await page.mouse.move(1600, 130, { steps: 45 });
    }
    const after = await cdp.send('Performance.getMetrics');
    await page.mouse.up();
    const complete = new Promise<void>(resolve => cdp.once('Tracing.tracingComplete', resolve));
    await cdp.send('Tracing.end'); await complete;
    cdp.off('Tracing.dataCollected', collect);
    const frames = await page.evaluate(() => {
      const w = window as any; w.wideActive = false;
      const gaps = w.wideFrames.slice(1).sort((a: number, b: number) => a - b);
      return { count: gaps.length, p95: gaps[Math.floor(gaps.length * .95)], max: gaps.at(-1), over34: gaps.filter((v: number) => v > 34).length };
    });
    const report = { mode, frames, chunks: await page.locator('.contour-chunk').count(),
      pathBytes: await page.locator('.contour-chunk path').evaluateAll(paths => paths.reduce((sum, path) => sum + (path.getAttribute('d')?.length ?? 0), 0)),
      paintMs: events.filter(e => e.name === 'Paint' && e.ph === 'X').reduce((sum, e) => sum + (e.dur ?? 0) / 1000, 0),
      metrics: Object.fromEntries(after.metrics.filter(m => /Duration/.test(m.name)).map(m => [m.name, m.value - (before.metrics.find(b => b.name === m.name)?.value ?? 0)])),
    };
    reports.push(report);
    console.log('WIDE_PAN', JSON.stringify(report));
    await style?.evaluate(el => el.remove());
  }
  await expect.poll(() => coverage(page)).toBe(true);
  if (process.env.OAW_WIDE_REPORT) await writeFile(process.env.OAW_WIDE_REPORT, JSON.stringify(reports, null, 2));

  // Keep filling new terrain at overview scale, including negative coordinates.
  for (let pass = 0; pass < 3; pass++) {
    await page.mouse.move(1600, 130); await page.mouse.down();
    await page.mouse.move(400, 130, { steps: 25 }); await page.mouse.up();
  }
  await expect.poll(() => coverage(page), { timeout: 20_000 }).toBe(true);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
    await page.screenshot({ path: `../.outputs/viewport-wide-${theme}.png` });
  }
  if (process.env.OAW_WIDE_COMPARE) {
    const style = await page.addStyleTag({ content: '.contour-chunk { will-change: auto !important; }' });
    await page.screenshot({ path: '../.outputs/viewport-wide-dark-uncomposited.png' });
    await style.evaluate(el => el.remove());
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  // Release promoted overview tiles at high zoom; preserve screen-space lines.
  for (let step = 0; step < 20 && (await transform()).zoom < 1.2; step++) {
    const zoom = (await transform()).zoom;
    await page.locator('.world-controls .react-flow__controls-zoomin').click();
    await expect.poll(async () => (await transform()).zoom).toBeCloseTo(Math.min(2.2, zoom * 1.2), 4);
  }
  await expect.poll(async () => Number(await page.locator('.contour-chunk').first().getAttribute('data-resolution'))).toBe(80);
  await expect.poll(() => coverage(page)).toBe(true);
  expect(await page.locator('.contour-chunk').evaluateAll(chunks => chunks.every(el => getComputedStyle(el).willChange === 'auto'))).toBe(true);
  const stroke = await page.locator('.contour-minor').first().evaluate(el => parseFloat(getComputedStyle(el).strokeWidth));
  expect(stroke * (await transform()).zoom).toBeCloseTo(1.15, 3);
  await page.setViewportSize({ width: 2560, height: 1440 });
  await expect.poll(() => coverage(page)).toBe(true);
});
