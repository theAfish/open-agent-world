/** Reproducible real-card pan/zoom investigation (dev only; no production hooks).
 * node scripts/benchmark-panzoom.mjs --label after [--frontend-root path] [--canvas]
 * Isolated data is retained with the report; never reads or resets a user's profile.
 */
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { appendFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const project = path.resolve(frontend, '..');
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const label = option('--label', 'current');
const selectedFrontend = path.resolve(option('--frontend-root', frontend));
const repeats = Number(option('--repeats', '2'));
const rate = Number(option('--cpu', '4'));
const backendPort = Number(option('--backend-port', '8028'));
const frontendPort = Number(option('--port', '5188'));
const output = path.resolve(project, '.outputs', `panzoom-${label}-${Date.now()}`);
const apiOrigin = `http://127.0.0.1:${backendPort}`;
const origin = `http://127.0.0.1:${frontendPort}`;
const children = [];
const terrainSeed = 0x5eeda11;
let stopRequested = false;
process.on('SIGINT', () => { stopRequested = true; console.log('STOP_REQUESTED: finishing the current complete variant'); });
await mkdir(output, { recursive: true });
async function api(url, method = 'GET', data) {
  const response = await fetch(apiOrigin + '/api' + url, { method,
    headers: data ? { 'content-type': 'application/json' } : undefined,
    body: data ? JSON.stringify(data) : undefined });
  if (!response.ok) throw new Error(`${method} ${url}: ${response.status} ${await response.text()}`);
  return response.status === 204 ? undefined : response.json();
}
function start(command, argv, cwd, env) {
  const child = spawn(command, argv, { cwd, env: { ...process.env, ...env }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const logPath = path.join(output, `${children.length}-server.log`);
  child.stdout.on('data', data => { appendFileSync(logPath, data); });
  child.stderr.on('data', data => { appendFileSync(logPath, data); if (String(data).includes('Error')) console.error(String(data)); });
  children.push(child); return child;
}
async function ready(url) {
  for (let attempt = 0; attempt < 180; attempt++) {
    try { if ((await fetch(url)).ok) return; } catch { /* starting */ }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`Server did not start: ${url}`);
}
const cards = [];
const create = async (type, i, extra = {}) => {
  const card = await api('/nodes', 'POST', { type, name: `Benchmark ${type} ${i}`,
    position: { x: 220 + i % 12 * 290, y: 230 + Math.floor(i / 12) * 245 }, ...extra });
  cards.push(card); return card;
};
async function preferences(shell = false) {
  const profile = await api('/application');
  await api('/application/preferences', 'PATCH', { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: { viewport: { x: 40, y: 40, zoom: .35, width: 1600, height: 1000 }, mapPins: [] } }),
    'oaw-node-surfaces-v1': JSON.stringify({ version: 4, state: { surfaceLevels: Object.fromEntries(cards.map(card => [card.id, shell ? 'node' : 'preview'])), baseLevels: {}, surfaceSizes: {}, maximizedWorkspaces: {} } }),
  } });
}
async function seedMixed(page) {
  // Generated locally, decoded through the real Image card, without external resources.
  const image = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 1280; canvas.height = 800;
    const ctx = canvas.getContext('2d'); const gradient = ctx.createLinearGradient(0, 0, 1280, 800);
    gradient.addColorStop(0, '#214573'); gradient.addColorStop(1, '#cbca87'); ctx.fillStyle = gradient; ctx.fillRect(0, 0, 1280, 800);
    for (let i = 0; i < 200; i++) { ctx.strokeStyle = `hsl(${i},50%,60%)`; ctx.strokeRect(i * 5, i * 3, 240, 140); }
    return canvas.toDataURL('image/png').split(',')[1];
  });
  const types = ['oaw.tasks', 'image', 'agent', 'conversation', 'sandbox', 'xrd.spectrum-canvas'];
  for (let i = 100; i < 244; i++) {
    const type = types[(i - 100) % types.length];
    const card = await create(type, i - 100, type === 'agent' ? { config: { runtime_provider_id: 'core.mock' } } : {});
    if (type === 'oaw.tasks') await api(`/nodes/${card.id}/actions/upsert`, 'POST', { expected_revision: 0, arguments: {
      tasks: Array.from({ length: 40 }, (_, j) => ({ id: `task-${j}`, title: `Task ${j}`, description: 'Detailed research task. '.repeat(100), status: j < 10 ? 'done' : 'todo', depends_on: j ? [`task-${j - 1}`] : [] })),
    } });
    if (type === 'image') await api(`/resources/${card.id}/image`, 'POST', { filename: 'benchmark.png', media_type: 'image/png', data_base64: image });
    if (type === 'xrd.spectrum-canvas') {
      const document = await api(`/nodes/${card.id}/document`);
      const csv = Array.from({ length: 1800 }, (_, j) => `${10 + j * .02},${100 + 900 * Math.sin(j * .01) ** 20}`).join('\n');
      await api(`/nodes/${card.id}/actions/import`, 'POST', { expected_revision: document.revision, arguments: { filename: 'benchmark.csv', source_base64: Buffer.from(csv).toString('base64') } });
    }
    if (type === 'agent') await api(`/agents/${card.id}/run`, 'POST', { prompt: 'Generate benchmark runtime events' });
  }
  const grouped = await api('/legion-groups', 'POST', { name: 'Benchmark large Legion', node_ids: cards.slice(24, 100).map(card => card.id) });
  cards.push(...grouped.filter(card => !cards.some(existing => existing.id === card.id)));
  const box = await create('oaw.barracks', 250, { position: { x: 3200, y: 1800 } });
  for (let i = 0; i < 12; i++) await create('agent', 260 + i, { parent_id: box.id, position: { x: 3300 + i % 4 * 150, y: 1920 + Math.floor(i / 4) * 130 }, config: { runtime_provider_id: 'core.mock' } });
}
function instrumentation() {
  window.__panBenchmark = { active: false, phase: 'pan', commits: 0, mounts: 0, unmounts: 0, frames: [], panFrames: [], zoomFrames: [], longTasks: [] };
  window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, renderers: new Map(),
    inject(renderer) { this.renderers.set(1, renderer); return 1; },
    onCommitFiberRoot() { if (window.__panBenchmark.active) window.__panBenchmark.commits++; },
    onCommitFiberUnmount() {}, checkDCE() {},
  };
  new PerformanceObserver(list => { if (window.__panBenchmark.active) window.__panBenchmark.longTasks.push(...list.getEntries().map(entry => entry.duration)); }).observe({ type: 'longtask', buffered: false });
  let previous = performance.now();
  function tick(now) { const b = window.__panBenchmark; if (b.active) { b.frames.push(now - previous); b[b.phase + 'Frames'].push(now - previous); } previous = now; requestAnimationFrame(tick); }
  requestAnimationFrame(tick);
  document.addEventListener('DOMContentLoaded', () => {
    const count = node => node.nodeType === 1 ? (node.matches('.world-card') ? 1 : 0) + node.querySelectorAll('.world-card').length : 0;
    new MutationObserver(records => { if (!window.__panBenchmark.active) return;
      for (const record of records) {
        record.addedNodes.forEach(node => { window.__panBenchmark.mounts += count(node); });
        record.removedNodes.forEach(node => { window.__panBenchmark.unmounts += count(node); });
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
  });
}
async function measure(browser, scenario, variant, repeat) {
  console.log('MEASURE_START', scenario, variant.name, repeat);
  await preferences(variant.shell);
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, reducedMotion: 'reduce' });
  await context.addInitScript(instrumentation);
  const page = await context.newPage();
  let gestureHeartbeat, gestureTimeout;
  try {
  const pendingRequests = new Set();
  page.on('request', request => pendingRequests.add(request.url()));
  page.on('requestfinished', request => pendingRequests.delete(request.url()));
  page.on('requestfailed', request => pendingRequests.delete(request.url()));
  page.on('console', message => { if (message.type() === 'error') console.error('BROWSER_CONSOLE', message.text()); });
  const errors = [], requests = [], overlappingDocumentGets = [], inFlight = new Map();
  let measured = false, completedGesture = false;
  const requestPhase = () => measured ? 'gesture' : completedGesture ? 'after-gesture' : 'warmup';
  page.on('pageerror', error => { errors.push(error.message); console.error('PAGEERROR', error.message); });
  page.on('requestfailed', request => console.error('REQUEST_FAILED', request.url(), request.failure()?.errorText));
  page.on('response', response => { if (response.status() >= 400) console.error('HTTP_ERROR', response.status(), response.url()); });
  page.on('request', request => { if (request.url().includes('/api/')) {
    const url = request.url().replace(origin, '');
    requests.push({ url, method: request.method(), phase: requestPhase() });
    if (request.method() === 'GET' && /\/document/.test(url)) {
      if (inFlight.get(url)) overlappingDocumentGets.push({ url, phase: requestPhase() });
      inFlight.set(url, (inFlight.get(url) ?? 0) + 1);
    }
  } });
  const completeRequest = request => { const url = request.url().replace(origin, ''); if (request.method() === 'GET' && /\/document/.test(url)) inFlight.set(url, Math.max(0, (inFlight.get(url) ?? 0) - 1)); };
  page.on('requestfinished', completeRequest); page.on('requestfailed', completeRequest);
  const cdp = await context.newCDPSession(page);
  await cdp.send('Performance.enable');
  const started = Date.now();
  const heartbeat = setInterval(() => {
    const state = { elapsedMs: Date.now() - started, url: page.url(), pending: [...pendingRequests] };
    console.log('STARTUP_PENDING', JSON.stringify(state));
    void writeFile(path.join(output, 'startup-pending.json'), JSON.stringify(state, null, 2));
  }, 15000);
  try {
    await page.goto(origin + (variant.canvas ? '/?terrainRenderer=canvas' : '/'), { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.locator('.world-card').first().waitFor({ timeout: 30000 });
  } catch (error) {
    await writeFile(path.join(output, 'startup-pending.json'), JSON.stringify({ error: String(error), pending: [...pendingRequests], requests, errors }, null, 2));
    await Promise.allSettled([
      page.screenshot({ path: path.join(output, 'startup-failure.png'), timeout: 5000 }),
      Promise.race([page.content(), new Promise(resolve => setTimeout(() => resolve('page content timed out'), 5000))]).then(html => writeFile(path.join(output, 'startup-failure.html'), html)),
    ]);
    console.error('APP_IMPORT', await Promise.race([page.evaluate(() => Promise.race([import('/src/App.tsx').then(() => 'ok', error => error.stack), new Promise(resolve => setTimeout(() => resolve('import timed out'), 2000))])), new Promise(resolve => setTimeout(() => resolve('evaluation timed out'), 5000))]));
    throw error;
  } finally { clearInterval(heartbeat); }
  const firstCardMs = Date.now() - started;
  await page.waitForTimeout(2000);
  if (variant.hidden) await page.addStyleTag({ content: '.contour-chunk, .contour-layer { visibility:hidden !important; }' });
  // Exercise the real inspector lifecycle before measuring hidden visited content.
  if (scenario === 'mixed' && !variant.shell) {
    const visit = ['oaw.tasks', 'xrd.spectrum-canvas', 'conversation', 'text'].flatMap(type => cards.filter(card => card.type === type).slice(0, 1).map(card => card.id));
    await page.evaluate(async ids => {
      const { useNodeSurfaceStore: surfaces } = await import('/src/state/nodeSurfaces.ts');
      for (const id of ids) { surfaces.getState().openInspector(id); await new Promise(resolve => setTimeout(resolve, 120)); }
    }, visit);
    await page.waitForTimeout(800);
    await page.evaluate(async ids => { const { useNodeSurfaceStore: surfaces } = await import('/src/state/nodeSurfaces.ts'); ids.forEach(id => surfaces.getState().dismiss(id)); }, visit);
  }
  await page.waitForTimeout(1200);
  // Let the initial snapshot, document-driven size updates and closing-inspector
  // displacement settle before throttling. Do not prime any pan destination.
  const settleStarted = Date.now();
  let stableSince = Date.now(), previousCount = -1, initialCoverage;
  while (Date.now() - settleStarted < 60000) {
    initialCoverage = await page.evaluate(async () => {
      const { useWorldStore } = await import('/src/state/worldStore.ts');
      const state = useWorldStore.getState();
      const transform = new DOMMatrix(getComputedStyle(document.querySelector('#oaw-world-map .react-flow__viewport')).transform);
      return { cards: document.querySelectorAll('.world-card').length, loadedCards: state.cards.length,
        surfaceCounts: [...document.querySelectorAll('.world-card')].reduce((counts, card) => { const level = card.getAttribute('data-surface-level'); counts[level] = (counts[level] ?? 0) + 1; return counts; }, {}),
        loadingChunks: state.loadingChunkKeys.length, syncState: state.syncState,
        allActiveChunksLoaded: state.activeChunkKeys.every(key => state.loadedChunkKeys.includes(key)),
        terrainTiles: document.querySelectorAll('svg.contour-chunk').length,
        viewport: { x: transform.e, y: transform.f, zoom: transform.a } };
    });
    const pendingData = [...pendingRequests].some(url => /\/api\/(world(?:\?|$)|nodes\/[^/]+\/document|resources\/)/.test(url));
    if (initialCoverage.cards !== previousCount || pendingData || initialCoverage.loadingChunks || initialCoverage.syncState !== 'online' || !initialCoverage.allActiveChunksLoaded || initialCoverage.loadedCards < cards.length) stableSince = Date.now();
    previousCount = initialCoverage.cards;
    if (initialCoverage.cards > 0 && Date.now() - stableSince >= 1000) break;
    await page.waitForTimeout(250);
  }
  if (Date.now() - stableSince < 1000) throw new Error(`Initial canvas did not settle: ${JSON.stringify(initialCoverage)}`);
  if (Math.abs(initialCoverage.viewport.x - 40) > 1 || Math.abs(initialCoverage.viewport.y - 40) > 1 || Math.abs(initialCoverage.viewport.zoom - .35) > .001) throw new Error(`Initial viewport drifted: ${JSON.stringify(initialCoverage)}`);
  console.log('WARMUP_SETTLED', scenario, variant.name, JSON.stringify(initialCoverage));
  if (variant.canvas) await page.waitForFunction(() => {
    const host = document.querySelector('.contour-canvas-experiment');
    return host && (host.dataset.fallback || (host.dataset.rasterPending === '0' && Number(host.dataset.activeTiles) > 0));
  }, undefined, { timeout: 15000 });
  await cdp.send('Emulation.setCPUThrottlingRate', { rate });
  const beforeCards = await page.locator('.world-card').count();
  const before = await cdp.send('Performance.getMetrics');
  const events = [];
  const tracing = args.includes('--trace');
  if (tracing) {
    cdp.on('Tracing.dataCollected', ({ value }) => events.push(...value));
    await cdp.send('Tracing.start', { categories: 'devtools.timeline,disabled-by-default-devtools.timeline', transferMode: 'ReportEvents' });
  }
  measured = true;
  console.log('GESTURE_START', scenario, variant.name, { tracing, beforeCards });
  const gestureStarted = Date.now();
  let phase = 'pan';
  gestureHeartbeat = setInterval(() => console.log('GESTURE_PENDING', scenario, variant.name, phase, Date.now() - gestureStarted), 15000);
  const maxVariantMs = Number(option('--max-variant-ms', '0'));
  if (maxVariantMs > 0) gestureTimeout = setTimeout(() => {
    console.error('VARIANT_TIMEOUT', scenario, variant.name, phase);
    void writeFile(path.join(output, `${scenario}-${variant.name}-${repeat}-aborted.json`), JSON.stringify({ status: 'aborted-incomplete-trajectory', scenario, variant, phase, maxVariantMs }, null, 2));
    void context.close();
  }, maxVariantMs);
  await page.evaluate(() => { window.__panBenchmark.active = true; });
  const runtimeAgents = cards.filter(card => card.type === 'agent').slice(0, 2);
  const runtimeRuns = runtimeAgents.map(card => api(`/agents/${card.id}/run`, 'POST', { prompt: 'Benchmark event updates during pan and zoom' }));
  const transforms = [];
  const viewportX = () => page.evaluate(() => new DOMMatrix(getComputedStyle(document.querySelector('#oaw-world-map .react-flow__viewport')).transform).e);
  // Middle-button pans avoid selecting or moving actual cards. Every pass crosses
  // chunk boundaries and the reverse traversal revisits evicted card DOM.
  for (const direction of [-1, -1, 1, 1]) {
    const beforeX = await viewportX();
    await page.mouse.move(direction < 0 ? 1480 : 160, 800);
    await page.mouse.down({ button: 'middle' });
    await page.mouse.move(direction < 0 ? 160 : 1480, 800, { steps: 12 });
    await page.mouse.up({ button: 'middle' });
    const afterX = await viewportX();
    if (Math.abs(beforeX - afterX) < 500) throw new Error(`Pan did not move the viewport: ${beforeX} -> ${afterX}`);
    transforms.push({ beforeX, afterX });
    console.log('PAN_COMPLETE', transforms.length, { beforeX, afterX });
  }
  console.log('ZOOM_START', scenario, variant.name);
  phase = 'zoom';
  await page.evaluate(() => { window.__panBenchmark.phase = 'zoom'; });
  await page.mouse.move(900, 500);
  for (const delta of [...Array(6).fill(-98), ...Array(6).fill(98)]) { await page.mouse.wheel(0, delta); await page.waitForTimeout(35); }
  // Cross the direct low/medium LOD boundary repeatedly while checking cached terrain.
  for (const delta of [190, -190, 190, -190]) { await page.mouse.wheel(0, delta); await page.waitForTimeout(100); }
  await page.waitForTimeout(300);
  const measuredEnd = await page.evaluate(() => { const b = window.__panBenchmark; b.active = false; return { commits: b.commits, mounts: b.mounts, unmounts: b.unmounts, frames: b.frames, panFrames: b.panFrames, zoomFrames: b.zoomFrames, longTasks: b.longTasks }; });
  const gestureElapsedMs = Date.now() - gestureStarted;
  measured = false; completedGesture = true;
  clearInterval(gestureHeartbeat); clearTimeout(gestureTimeout);
  await Promise.all(runtimeRuns);
  const after = await cdp.send('Performance.getMetrics');
  if (tracing) {
    const completed = new Promise(resolve => cdp.once('Tracing.tracingComplete', resolve));
    await cdp.send('Tracing.end'); await completed;
  }
  const duration = {};
  for (const event of events) if (event.ph === 'X' && event.dur) { const value = duration[event.name] ??= { count: 0, ms: 0 }; value.count++; value.ms += event.dur / 1000; }
  const metrics = Object.fromEntries(after.metrics.filter(metric => /Duration|LayoutCount|RecalcStyleCount/.test(metric.name)).map(metric => [metric.name, metric.value - (before.metrics.find(other => other.name === metric.name)?.value ?? 0)]));
  const memory = { beforeHeapMB: before.metrics.find(metric => metric.name === 'JSHeapUsedSize')?.value / 1048576, afterHeapMB: after.metrics.find(metric => metric.name === 'JSHeapUsedSize')?.value / 1048576,
    dom: await cdp.send('Memory.getDOMCounters') };
  await cdp.send('HeapProfiler.collectGarbage');
  memory.retainedHeapMB = (await cdp.send('Performance.getMetrics')).metrics.find(metric => metric.name === 'JSHeapUsedSize')?.value / 1048576;
  const frames = measuredEnd.frames.slice(1).sort((a, b) => a - b);
  const percentile = p => frames[Math.min(frames.length - 1, Math.floor(frames.length * p))] ?? 0;
  const duplicates = Object.entries(requests.filter(request => request.method === 'GET' && /\/document/.test(request.url)).reduce((counts, request) => { const key = request.url; counts[key] = (counts[key] ?? 0) + 1; return counts; }, {})).filter(([, count]) => count > 1);
  const coverage = await page.evaluate(() => ({ cards: document.querySelectorAll('.world-card').length, terrainTiles: document.querySelectorAll('svg.contour-chunk').length,
    surfaceCounts: [...document.querySelectorAll('.world-card')].reduce((counts, card) => { const level = card.getAttribute('data-surface-level'); counts[level] = (counts[level] ?? 0) + 1; return counts; }, {}),
    transform: document.querySelector('#oaw-world-map .react-flow__viewport')?.getAttribute('style'), terrainCanvas: document.querySelectorAll('.contour-layer canvas').length,
    canvasState: { ...document.querySelector('.contour-canvas-experiment')?.dataset } }));
  const phaseFrames = values => { const sorted = values.slice(1).sort((a, b) => a - b); return { count: sorted.length, p95: sorted[Math.floor(sorted.length * .95)], max: sorted.at(-1) }; };
  const result = { scenario, variant, repeat, tracing, cards: cards.length, beforeCards, firstCardMs, cpuThrottle: rate,
    initialCoverage,
    sampledDurationMs: measuredEnd.frames.reduce((sum, value) => sum + value, 0),
    gestureElapsedMs,
    frame: { count: frames.length, p50: percentile(.5), p95: percentile(.95), p99: percentile(.99), max: frames.at(-1), over33ms: frames.filter(value => value > 33.4).length },
    longTasks: { count: measuredEnd.longTasks.length, totalMs: measuredEnd.longTasks.reduce((sum, value) => sum + value, 0), maxMs: Math.max(0, ...measuredEnd.longTasks) },
    reactGlobalCommits: measuredEnd.commits, cardDomMounts: measuredEnd.mounts, cardDomUnmounts: measuredEnd.unmounts,
    panFrames: phaseFrames(measuredEnd.panFrames), zoomFrames: phaseFrames(measuredEnd.zoomFrames), transforms,
    runtimeAgentsDuringGesture: runtimeAgents.map(card => card.id),
    metrics, memory, duration, requests, repeatedDocumentGets: duplicates, overlappingDocumentGets, coverage, errors };
  if (scenario === 'mixed' && variant.name === 'svg-real' && repeat === 0) {
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
    result.derivations = await page.evaluate(async () => {
      const { filterCardsToChunks, getViewportChunkKeys } = await import('/src/state/chunks.ts');
      const { containerSizes } = await import('/src/state/containers.ts');
      const { useWorldStore } = await import('/src/state/worldStore.ts');
      const state = useWorldStore.getState();
      const template = state.cards.find(card => card.type === 'text');
      const keys = getViewportChunkKeys(state.viewport);
      const time = action => { for (let i = 0; i < 5; i++) action(); const values = []; for (let i = 0; i < 30; i++) { const start = performance.now(); action(); values.push(performance.now() - start); } values.sort((a, b) => a - b); return { medianMs: values[15], p95Ms: values[28] }; };
      return [200, 1000].map(count => {
        const group = { ...template, id: 'bench-group', type: 'legion', position: { x: 0, y: 0 }, size: { width: 6000, height: 6000 } };
        const nodes = [group, ...Array.from({ length: count }, (_, i) => ({ ...template, id: `bench-${i}`, parent_id: group.id, position: { x: i % 20 * 150, y: Math.floor(i / 20) * 130 } }))];
        const levels = new Map(nodes.map(card => [card.id, 'node']));
        return { count: nodes.length, cpuThrottle: 1, filterCardsToChunks: time(() => filterCardsToChunks(nodes, keys, state.catalog)), containerSizes: time(() => containerSizes(nodes, state.catalog, levels)) };
      });
    });
  }
  const stem = `${scenario}-${variant.name}-${repeat}`;
  await writeFile(path.join(output, stem + '.json'), JSON.stringify(result, null, 2));
  await page.screenshot({ path: path.join(output, stem + '.png'), timeout: 15000 }).catch(error => console.error('SCREENSHOT_FAILED', error.message));
  console.log(JSON.stringify({ scenario, variant: variant.name, repeat, frame: result.frame, longTasks: result.longTasks, commits: result.reactGlobalCommits, retainedHeapMB: memory.retainedHeapMB, requests: requests.length, errors: errors.length }));
  return result;
  } finally { clearInterval(gestureHeartbeat); clearTimeout(gestureTimeout); await context.close(); }
}
let browser;
try {
  // Seed only this newly-created fixture database. Both revisions must draw the
  // same terrain; normal new profiles deliberately choose random geography.
  const prepare = spawn(path.join(project, 'backend/.venv/Scripts/python.exe'), ['-c', [
    'from backend.config import Settings',
    'from backend.persistence.database import Database',
    'settings = Settings.from_environment()',
    'settings.database_path.parent.mkdir(parents=True, exist_ok=True)',
    'database = Database(settings.database_path)',
    'with database.transaction(immediate=True) as connection:',
    `    connection.execute("INSERT INTO application_settings(key,value_json) VALUES (?,?)", ("canvas_terrain_seed.v1", "${terrainSeed}"))`,
    'database.close()',
  ].join('\n')], { cwd: project, windowsHide: true, stdio: 'inherit', env: { ...process.env, OPEN_AGENT_WORLD_DATA_ROOT: path.join(output, 'data') } });
  const prepared = await new Promise((resolve, reject) => { prepare.on('error', reject); prepare.on('exit', resolve); });
  if (prepared !== 0) throw new Error(`Benchmark fixture database initialization failed: ${prepared}`);
  start(path.join(project, 'backend/.venv/Scripts/python.exe'), ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', String(backendPort), '--no-proxy-headers'], project,
    { OPEN_AGENT_WORLD_AGENT_RUNTIME: 'mock', OPEN_AGENT_WORLD_DATA_ROOT: path.join(output, 'data') });
  const configPath = path.join(output, 'vite-benchmark.config.mjs');
  const relativeConfig = path.relative(output, path.join(selectedFrontend, 'vite.config.ts')).replaceAll('\\', '/');
  await writeFile(configPath, `import config from ${JSON.stringify(relativeConfig.startsWith('.') ? relativeConfig : './' + relativeConfig)};\nexport default {...config, cacheDir: ${JSON.stringify(path.join(project, '.outputs', `panzoom-vite-cache-${label}`))}};\n`);
  start(process.execPath, [path.join(frontend, 'node_modules/vite/bin/vite.js'), '--config', configPath, '--host', '127.0.0.1', '--port', String(frontendPort), '--strictPort'], selectedFrontend,
    { OAW_DEV_BACKEND_HTTP_URL: apiOrigin, OAW_DEV_BACKEND_WS_URL: `ws://127.0.0.1:${backendPort}` });
  await Promise.all([ready(apiOrigin + '/api/world'), ready(origin)]);
  if ((await api('/world')).terrain_seed !== terrainSeed) throw new Error('Benchmark terrain seed was not applied');
  console.log(`BENCHMARK_OUTPUT ${output}`);
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL ?? 'chrome', headless: true });
  for (let i = 0; i < 100; i++) await create('text', i);
  if (args.includes('--seed-only')) {
    const seedPage = await browser.newPage(); await seedMixed(seedPage); await seedPage.close();
    await writeFile(path.join(output, 'fixture.json'), JSON.stringify({ terrainSeed, cards: cards.length, types: cards.reduce((counts, card) => { counts[card.type] = (counts[card.type] ?? 0) + 1; return counts; }, {}) }, null, 2));
    console.log('SEED_ONLY_PASSED', cards.length);
  } else {
  const results = [];
  if (!args.includes('--skip-light')) results.push(await measure(browser, 'light-100', { name: 'svg-real' }, 0));
  const seedPage = await browser.newPage(); await seedMixed(seedPage); await seedPage.close();
  console.log(`Seeded ${cards.length} real cards`);
  const variants = [{ name: 'svg-real' }, { name: 'hidden-real', hidden: true }, { name: 'svg-shell', shell: true }, { name: 'hidden-shell', hidden: true, shell: true }, ...(args.includes('--canvas') ? [{ name: 'canvas-real', canvas: true }] : [])];
  const selectedVariants = option('--variants', '').split(',').filter(Boolean);
  variantsLoop: for (let repeat = 0; repeat < repeats; repeat++) for (const variant of (repeat % 2 ? [...variants].reverse() : variants).filter(variant => !selectedVariants.length || selectedVariants.includes(variant.name))) {
    if (stopRequested || existsSync(path.join(output, 'STOP_AFTER_CURRENT'))) { stopRequested = true; break variantsLoop; }
    results.push(await measure(browser, 'mixed', variant, repeat));
  }
  await writeFile(path.join(output, 'report.json'), JSON.stringify({ label, selectedFrontend, terrainSeed, interruptedAfterVariant: stopRequested, createdAt: new Date().toISOString(),
    environment: { browser: browser.version(), platform: os.platform(), osRelease: os.release(), arch: os.arch(), cpuModel: os.cpus()[0]?.model, logicalCpus: os.cpus().length, ramGiB: os.totalmem() / 1024 ** 3 },
    methodology: { cpuThrottle: rate, viewport: '1600x1000', dev: true, pairedReverseOrder: repeats > 1,
    terrainHidden: 'CSS visibility hidden; geometry generation still runs', shell: 'Existing node surface (96px geometry) instead of preview (different dimensions)', mixedDensity: '100 light and 144 mixed cards overlap the same first 12 columns intentionally', firstCard: 'Unthrottled navigation to first visible .world-card; includes cold Vite compilation', commits: 'Global React root commits, not per-component renders', trace: 'Nested CPU trace durations overlap; do not sum categories', dataRoot: path.join(output, 'data') }, results }, null, 2));
  }
} finally {
  for (const child of children) child.kill();
  await Promise.race([browser?.close(), new Promise(resolve => setTimeout(resolve, 5000))]);
}
