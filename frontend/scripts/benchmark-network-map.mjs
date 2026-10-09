/** Run against `npm run dev -- --port 5189`: node scripts/benchmark-network-map.mjs [snapshot.json]. */
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
const output = path.resolve('../.outputs/graph-audit');
await mkdir(output, { recursive: true });
const url = process.env.OAW_GRAPH_PREVIEW_URL ?? 'http://127.0.0.1:5189/?network-map';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL ?? 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = []; page.on('pageerror', error => errors.push(error.message));
await page.addInitScript(() => {
  localStorage.setItem('oaw.locale', 'en');
  window.graphProbe = { workers: 0, contexts: [], frames: [], running: false, firstInputMs: null, longTasks: [] };
  const NativeWorker = window.Worker;
  window.Worker = class extends NativeWorker {
    alive = true;
    constructor(...args) { super(...args); window.graphProbe.workers++; }
    terminate() { if (this.alive) { this.alive = false; window.graphProbe.workers--; } super.terminate(); }
  };
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (...args) {
    const context = original.apply(this, args);
    if (context && /webgl/.test(String(args[0])) && !window.graphProbe.contexts.some(ref => ref.deref() === context)) window.graphProbe.contexts.push(new WeakRef(context));
    return context;
  };
  new PerformanceObserver(list => window.graphProbe.longTasks.push(...list.getEntries().map(e => e.duration))).observe({ type: 'longtask', buffered: true });
});
const client = await page.context().newCDPSession(page);
const report = { browser: await browser.version(), viewport: { width: 1440, height: 1000 }, url, cases: [], errors };
const settle = async () => {
  await page.locator('.network-map[data-layout-state="ready"]').waitFor({ timeout: 90000 });
  await page.waitForTimeout(350);
};
try {
  await page.goto(url); await settle();
  report.environment = await page.evaluate(() => ({ hardwareConcurrency: navigator.hardwareConcurrency, devicePixelRatio, userAgent: navigator.userAgent }));
  for (const size of [process.argv[2] ? 'real' : null, 1000, 10000].filter(Boolean)) {
    const started = Date.now();
    if (size === 'real') await page.getByLabel('Open graph JSON').setInputFiles(path.resolve(process.argv[2]));
    else await page.getByLabel('Graph scale').selectOption(String(size));
    await settle();
    const readyMs = Date.now() - started;
    const layout = await page.locator('.network-map-stage').evaluate(el => ({ layoutMs: Number(el.dataset.layoutMs), nodes: el.networkDiagnostics.nodes, edges: el.networkDiagnostics.edges, islands: el.networkDiagnostics.islands }));
    await page.evaluate(() => {
      const p = window.graphProbe; p.frames = []; p.longTasks = []; p.running = true; p.firstInputMs = null;
      let last = performance.now();
      const frame = now => { if (!p.running) return; p.frames.push(now - last); last = now; requestAnimationFrame(frame); }; requestAnimationFrame(frame);
      document.querySelector('.network-map-stage').addEventListener('pointermove', () => {
        const start = performance.now(); requestAnimationFrame(() => { p.firstInputMs = performance.now() - start; });
      }, { once: true });
    });
    const box = await page.locator('.network-map-stage').boundingBox();
    await page.mouse.move(box.x + 35, box.y + box.height * .45); await page.mouse.down();
    for (let i = 1; i <= 90; i++) await page.mouse.move(box.x + 35 + i * 2, box.y + box.height * .45 + Math.sin(i / 12) * 55);
    await page.mouse.up();
    await page.mouse.wheel(0, 280); await page.waitForTimeout(250); await page.mouse.wheel(0, -280);
    const timing = await page.evaluate(() => {
      const p = window.graphProbe; p.running = false;
      const frames = p.frames.slice(2).sort((a, b) => a - b);
      return { frames: frames.length, fps: 1000 / (frames.reduce((a, b) => a + b, 0) / frames.length),
        frameP95Ms: frames[Math.floor(frames.length * .95)], inputToFrameMs: p.firstInputMs,
        longTasks: p.longTasks.length, longestTaskMs: Math.max(0, ...p.longTasks) };
    });
    await page.evaluate(() => {
      const p = window.graphProbe; p.frames = []; p.running = true;
      let last = performance.now();
      const frame = now => { if (!p.running) return; p.frames.push(now - last); last = now; requestAnimationFrame(frame); }; requestAnimationFrame(frame);
    });
    for (let i = 0; i < 60; i++) await page.mouse.move(box.x + box.width * (.2 + i / 100), box.y + box.height * (.5 + Math.sin(i / 8) * .2));
    const hoverTiming = await page.evaluate(() => {
      const p = window.graphProbe; p.running = false; const frames = p.frames.slice(2).sort((a, b) => a - b);
      return { fps: 1000 / (frames.reduce((a, b) => a + b, 0) / frames.length), frameP95Ms: frames[Math.floor(frames.length * .95)] };
    });
    const queryStarted = Date.now();
    await page.getByLabel('Find in map', { exact: true }).fill(size === 'real' ? 'structure' : 'Atomic structure');
    await page.locator('.network-results').getByRole('option').first().waitFor();
    const searchResponseMs = Date.now() - queryStarted;
    await page.getByLabel('Find in map', { exact: true }).fill('');
    await page.locator('.network-map-scale button').last().click(); await page.waitForTimeout(350);
    await page.screenshot({ path: path.join(output, `${size}-light.png`) });
    await page.getByRole('button', { name: 'Toggle theme' }).click(); await page.waitForTimeout(100);
    await page.screenshot({ path: path.join(output, `${size}-dark.png`) });
    await page.getByRole('button', { name: 'Toggle theme' }).click();
    if (size === 10000) {
      for (let i = 0; i < 5; i++) await page.locator('.network-map-scale button').nth(1).click();
      await page.screenshot({ path: path.join(output, '10000-aggregate.png') });
      await page.locator('.network-map-tools button').last().click();
      await page.locator('.network-topics button').first().click(); await page.waitForTimeout(350);
      await page.screenshot({ path: path.join(output, '10000-island-focus.png') });
    }
    await client.send('HeapProfiler.collectGarbage');
    const memory = await client.send('Runtime.getHeapUsage');
    const item = { scale: size, ...layout, readyMs, ...timing, hoverTiming, searchResponseMs, heapMiB: memory.usedSize / 1048576 };
    report.cases.push(item); console.log(JSON.stringify(item));
  }
  const lifecycle = [];
  for (let i = 0; i < 8; i++) {
    await page.getByRole('button', { name: 'Unmount map', exact: true }).click();
    await page.locator('.network-map').waitFor({ state: 'detached' });
    await client.send('HeapProfiler.collectGarbage');
    const memory = await client.send('Runtime.getHeapUsage');
    const probe = await page.evaluate(() => ({ workers: window.graphProbe.workers,
      liveWebGL: window.graphProbe.contexts.filter(ref => { const c = ref.deref(); return c && !c.isContextLost(); }).length,
      canvases: document.querySelectorAll('canvas').length }));
    lifecycle.push({ cycle: i + 1, heapMiB: memory.usedSize / 1048576, ...probe, ...await client.send('Memory.getDOMCounters') });
    await page.getByRole('button', { name: 'Mount map', exact: true }).click(); await settle();
  }
  report.lifecycle = lifecycle;
  console.log(JSON.stringify({ lifecycle, errors }));
} finally {
  await writeFile(path.join(output, 'performance.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
