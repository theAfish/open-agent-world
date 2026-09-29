import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1920, height: 1080 } });

test('1000 stress cards keep wheel ownership, reversal and pointer anchoring', async ({ page, request }, testInfo) => {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-node-surfaces-v1': null,
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: {
        viewport: { x: 960, y: 540, zoom: .18, width: 1920, height: 1080 },
      } }),
    },
  } })).ok()).toBe(true);
  const syntheticRequests: string[] = [];
  page.on('request', req => { if (/\/api\/.*stress-\d+/.test(req.url())) syntheticRequests.push(req.url()); });
  await page.goto('/');
  await expect(page.locator('.terrain-webgl-background')).toBeVisible();
  await page.evaluate(async () => {
    const { useWorldStore } = await import(/* @vite-ignore */ performance.getEntriesByType('resource').find(e => /\/src\/state\/worldStore\.ts(?:\?|$)/.test(e.name))!.name);
    useWorldStore.getState().generateStressWorld(1000);
  });
  await expect(page.locator('.card-lod-view').first()).toBeAttached();
  await expect(page.locator('.sandbox-workspace')).toHaveCount(0);
  await page.waitForTimeout(500);

  const result = await page.evaluate(async () => {
    const { useWorldStore } = await import(/* @vite-ignore */ performance.getEntriesByType('resource').find(e => /\/src\/state\/worldStore\.ts(?:\?|$)/.test(e.name))!.name);
    const flow = document.querySelector('#oaw-world-map')!;
    const surface = flow.querySelector<HTMLElement>('.react-flow__viewport')!;
    const read = () => {
      const m = new DOMMatrix(surface.style.transform);
      return { x: m.e, y: m.f, zoom: m.a };
    };
    const wheel = (deltaY: number) => flow.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true, cancelable: true, deltaY, clientX: 700, clientY: 350,
    }));
    const frame = () => new Promise(requestAnimationFrame);
    const collect = async () => {
      const views = [];
      const end = performance.now() + 350;
      while (performance.now() < end) { await frame(); views.push(read()); }
      // The final move-end is deliberately deferred by XYFlow.
      await new Promise(resolve => setTimeout(resolve, 30));
      return views;
    };
    let writes = 0;
    const unsubscribe = useWorldStore.subscribe((s, previous) => { if (s.viewport !== previous.viewport) writes++; });
    const initial = read();
    wheel(-80); wheel(-80); wheel(-80);
    const forward = await collect();
    const forwardWrites = writes;
    wheel(-300);
    await frame();
    const reversedAt = read();
    wheel(120);
    const reverse = await collect();
    unsubscribe();
    return { initial, forward, forwardWrites, reversedAt, reverse, cards: useWorldStore.getState().stressCards.length };
  });
  expect(result.cards).toBe(1000);
  expect(result.forwardWrites).toBe(1);
  expect(result.forward.at(-1)!.zoom).toBeCloseTo(result.initial.zoom * 2 ** .48, 5);
  expect(result.reverse[0].zoom).toBeLessThan(result.reversedAt.zoom);
  expect(result.reverse.at(-1)!.zoom).toBeCloseTo(result.reversedAt.zoom * 2 ** -.24, 5);
  for (const view of [...result.forward, ...result.reverse]) {
    // CSS transform serialization rounds large world coordinates. Measure the
    // visible anchor error in CSS pixels, including at this far zoom level.
    expect(Math.abs(view.x + (700 - result.initial.x) / result.initial.zoom * view.zoom - 700)).toBeLessThan(.1);
    expect(Math.abs(view.y + (350 - result.initial.y) / result.initial.zoom * view.zoom - 350)).toBeLessThan(.1);
  }
  expect(syntheticRequests).toEqual([]);
  await testInfo.attach('stress-wheel-samples', { body: JSON.stringify(result), contentType: 'application/json' });
});
