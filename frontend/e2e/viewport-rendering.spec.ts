import { expect, test } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { resetTutorialProfile } from './tutorial-profile';

// Playwright DOM snapshots/hover inspection distort sustained-pan timings.
test.use({ trace: 'off' });

test('profile sustained panning with populated canvas', async ({ page, request }) => {
  test.setTimeout(90_000);
  await resetTutorialProfile(request);
  await page.setViewportSize({ width: 1920, height: 1080 });
  const ids: string[] = [];
  try {
    for (let i = 0; i < 48; i++) {
      const response = await request.post('/api/nodes', { data: {
        type: 'text', name: `Pan profile ${i}`, position: { x: 100 + i % 8 * 220, y: 220 + Math.floor(i / 8) * 180 },
      } });
      expect(response.ok()).toBe(true);
      ids.push((await response.json()).id);
    }
    await page.addInitScript(ids => localStorage.setItem('oaw-node-surfaces-v1', JSON.stringify({ version: 3,
      state: { surfaceLevels: Object.fromEntries(ids.map(id => [id, 'node'])), baseLevels: {}, maximizedWorkspaces: {} },
    })), ids);
    await page.goto('/');
    await expect(page.locator(`[data-card-id="${ids[0]}"]`).first()).toBeVisible();
    await expect(page.locator('.terrain-webgl-background')).toHaveAttribute('data-terrain-status', 'ready');
    await page.waitForTimeout(1500);
    if (process.env.OAW_PAN_CSS) await page.addStyleTag({ content: process.env.OAW_PAN_CSS });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
    await cdp.send('Performance.enable');
    await cdp.send('Profiler.enable');
    await cdp.send('Profiler.start');
    const events: any[] = [];
    cdp.on('Tracing.dataCollected', ({ value }) => events.push(...value));
    await cdp.send('Tracing.start', { categories: 'devtools.timeline', transferMode: 'ReportEvents' });
    await page.mouse.move(1600, 130);
    expect(await page.evaluate(() => document.elementFromPoint(1600, 130)?.classList.contains('react-flow__pane'))).toBe(true);
    await page.mouse.down();
    const before = await cdp.send('Performance.getMetrics');
    for (let pass = 0; pass < 4; pass++) {
      await page.mouse.move(1200, 130, { steps: 60 });
      await page.mouse.move(1600, 130, { steps: 60 });
    }
    const after = await cdp.send('Performance.getMetrics');
    await page.mouse.up();
    const complete = new Promise<void>(resolve => cdp.once('Tracing.tracingComplete', () => resolve()));
    await cdp.send('Tracing.end');
    await complete;
    const { profile } = await cdp.send('Profiler.stop');
    const durations: Record<string, { count: number; ms: number }> = {};
    for (const event of events) {
      if (event.ph !== 'X' || !event.dur) continue;
      const value = durations[event.name] ??= { count: 0, ms: 0 };
      value.count++; value.ms += event.dur / 1000;
    }
    const report = {
      hotFunctions: profile.nodes.filter(node => node.hitCount).sort((a, b) => (b.hitCount ?? 0) - (a.hitCount ?? 0)).slice(0, 25)
        .map(node => ({ name: node.callFrame.functionName, url: node.callFrame.url, line: node.callFrame.lineNumber, hits: node.hitCount })),
      nodes: await page.locator('#oaw-world-map .react-flow__node').count(),
      metrics: Object.fromEntries(after.metrics.filter(m => /Duration|LayoutCount|RecalcStyleCount/.test(m.name))
        .map(m => [m.name, m.value - (before.metrics.find(b => b.name === m.name)?.value ?? 0)])),
      durations: Object.fromEntries(Object.entries(durations).sort((a, b) => b[1].ms - a[1].ms).slice(0, 18)),
    };
    console.log('PAN_RENDERING', JSON.stringify(report));
    if (process.env.OAW_PAN_REPORT) await writeFile(process.env.OAW_PAN_REPORT, JSON.stringify(report, null, 2));
    await page.screenshot({ path: '../.outputs/viewport-rendering.png' });
  } finally {
    for (const id of ids) await request.delete(`/api/nodes/${id}`);
  }
});
