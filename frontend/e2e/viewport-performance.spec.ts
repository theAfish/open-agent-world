import { expect, test } from '@playwright/test';

test('pan across uncached terrain and zoom without losing terrain coverage', async ({ page, request }) => {
  test.setTimeout(90_000);
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': null,
    },
  } });
  await page.goto('/');
  await expect(page.locator('.terrain-webgl-background')).toHaveAttribute('data-terrain-status', 'ready');
  await page.waitForTimeout(1500);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await page.evaluate(() => {
    const w = window as any;
    w.panFrames = []; w.panLongTasks = []; w.panActive = true;
    w.panObserver = new PerformanceObserver(list => {
      w.panLongTasks.push(...list.getEntries().map(entry => entry.duration));
    });
    w.panObserver.observe({ type: 'longtask', buffered: false });
    let previous = performance.now();
    const tick = (now: number) => {
      w.panFrames.push(now - previous); previous = now;
      if (w.panActive) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  const viewport = page.locator('#oaw-world-map > .react-flow__renderer .react-flow__viewport').first();
  const origin = await viewport.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).e);
  for (let pass = 0; pass < 8; pass++) {
    await page.mouse.move(1100, 400);
    await page.mouse.down();
    await page.mouse.move(200, 400, { steps: 30 });
    await page.mouse.up();
  }
  expect(await viewport.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).e)).toBeLessThan(origin - 6500);
  const report = await page.evaluate(() => {
    const w = window as any; w.panActive = false; w.panObserver.disconnect();
    const gaps = w.panFrames.slice(1).sort((a: number, b: number) => a - b);
    return { frames: gaps.length, p95: gaps[Math.floor(gaps.length * .95)], max: gaps.at(-1),
      over34ms: gaps.filter((v: number) => v > 34).length, longTasks: w.panLongTasks };
  });
  console.log('VIEWPORT_PERFORMANCE', JSON.stringify(report));
  const checkCoverage = () => page.evaluate(() => {
    const s = (document.querySelector('.terrain-webgl-background') as any).terrainStats;
    return s.visibleTiles > 0 && s.coveredTiles === s.visibleTiles && s.pendingTiles === 0;
  });
  await expect.poll(checkCoverage).toBe(true);
  await page.mouse.move(700, 400);
  for (let step = 0; step < 4; step++) await page.locator('.world-controls .react-flow__controls-zoomin').click();
  await expect.poll(checkCoverage).toBe(true);
  await page.screenshot({ path: '../.outputs/viewport-performance.png' });
});
