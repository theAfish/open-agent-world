/** Native WebGL2 regression benchmark using the empty-canvas profile gestures.
 * node scripts/benchmark-terrain-webgl.mjs [--scenes near,wide,dpr2,dark,cards,long]
 *   [--rounds 2] [--trace] [--channel msedge] [--port 5188] [--backend-port 8028]
 * Starts isolated servers/data below .outputs; never reads or writes the user's world.
 */
import { spawn, spawnSync, execFile } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { appendFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { createServer } from 'node:net';
import { chromium } from '@playwright/test';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const project = path.resolve(frontend, '..');
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const tracing = args.includes('--trace');
const rounds = Number(option('--rounds', tracing ? '1' : '2'));
const scenes = option('--scenes', 'near,wide,dpr2,dark,cards,long').split(',');
if (args.includes('--renderers')) throw new Error('Renderer selection was removed; this benchmark uses WebGL2.');
const output = path.join(project, '.outputs', `terrain-webgl-${Date.now()}`);
const port = option('--port', '5188'), backendPort = option('--backend-port', '8028');
const origin = `http://127.0.0.1:${port}`, apiOrigin = `http://127.0.0.1:${backendPort}`;
const near = { x: -23223.4, y: 79807.1, zoom: 1.45509, width: 1600, height: 1000 };
const atZoom = zoom => ({ ...near, zoom, x: 800 - (800 - near.x) * zoom / near.zoom, y: 500 - (500 - near.y) * zoom / near.zoom });
const children = [], results = [];
const memoryTimers = new Set();
const execute = promisify(execFile);
let browser, browserCdp, environment;
await mkdir(output, { recursive: true });
console.log('OUTPUT', output);
function start(command, argv, cwd, env) {
  const child = spawn(command, argv, { cwd, env: { ...process.env, ...env }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const logfile = path.join(output, `${children.length}-server.log`);
  child.runtimePids = new Set();
  child.stdout.on('data', data => appendFileSync(logfile, data));
  child.stderr.on('data', data => {
    appendFileSync(logfile, data);
    // Windows venv launchers can create a second interpreter process.
    const match = String(data).match(/Started server process \[(\d+)\]/);
    if (match) child.runtimePids.add(Number(match[1]));
  });
  children.push(child);
}
async function ready(url) {
  for (let i = 0; i < 180; i++) {
    try { if ((await fetch(url)).ok) return; } catch { /* starting */ }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`Server unavailable: ${url}`);
}
async function requireFreePort(port) {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(Number(port), '127.0.0.1', () => server.close(resolve));
  });
}
async function api(url, method = 'GET', data) {
  const response = await fetch(`${apiOrigin}/api${url}`, { method, headers: data ? { 'content-type': 'application/json' } : undefined, body: data ? JSON.stringify(data) : undefined });
  if (!response.ok) throw new Error(`${method} ${url}: ${response.status}`);
  return response.status === 204 ? undefined : response.json();
}
const stat = values => {
  const sorted = values.slice().sort((a, b) => a - b);
  return { n: sorted.length, p50: sorted[Math.floor(sorted.length * .5)], p95: sorted[Math.floor(sorted.length * .95)], max: sorted.at(-1), over34: sorted.filter(x => x > 34).length };
};
function instrument() {
  window.__terrainBench = { active: false, frames: [], longTasks: [], workerMessages: 0 };
  const NativeWorker = window.Worker;
  window.Worker = class extends NativeWorker {
    constructor(...args) { super(...args); this.addEventListener('message', () => { if (window.__terrainBench.active) window.__terrainBench.workerMessages++; }); }
  };
  new PerformanceObserver(list => { if (window.__terrainBench.active) window.__terrainBench.longTasks.push(...list.getEntries().map(e => e.duration)); }).observe({ type: 'longtask' });
  let previous;
  function tick(now) { if (window.__terrainBench.active && previous) window.__terrainBench.frames.push(now - previous); previous = now; requestAnimationFrame(tick); }
  requestAnimationFrame(tick);
}
async function details(page) {
  return page.evaluate(() => ({
    cards: document.querySelectorAll('#oaw-world-map .world-card').length,
    svgTiles: document.querySelectorAll('.contour-chunk').length,
    svgPaths: document.querySelectorAll('.contour-chunk path').length,
    canvasCount: document.querySelectorAll('.terrain-webgl-background').length,
    camera: document.querySelector('#oaw-world-map .react-flow__viewport').style.transform,
    stats: document.querySelector('.terrain-webgl-background')?.terrainStats,
    error: document.querySelector('.terrain-webgl-background')?.dataset.terrainError,
    dpr: devicePixelRatio, theme: document.documentElement.dataset.theme,
  }));
}
async function settle(page) {
  await page.waitForFunction(() => { const s = document.querySelector('.terrain-webgl-background')?.terrainStats; return s && !s.contextLost && s.pendingTiles === 0 && s.visibleTiles > 0 && s.coveredTiles === s.visibleTiles; });
}
async function gesture(page, phase, samples) {
  if (phase === 'pan') {
    for (let pass = 0; pass < 2; pass++) {
      await page.mouse.move(800, 500); await page.mouse.down({ button: 'middle' });
      for (let i = 1; i <= 60; i++) { await page.mouse.move(800 + 250 * Math.sin(i / 60 * Math.PI * 2), 500 + 90 * Math.sin(i / 60 * Math.PI * 4)); await page.waitForTimeout(12); }
      await page.mouse.up({ button: 'middle' });
    }
  } else if (phase === 'zoom') {
    await page.mouse.move(800, 500);
    for (const sign of [-1, 1]) for (let i = 0; i < 30; i++) { await page.mouse.wheel(0, sign * 15); await page.waitForTimeout(16); }
    await page.waitForTimeout(350);
  } else {
    // Cross hundreds of new tiles and return through an already evicted region.
    for (let pass = 0; pass < 48; pass++) {
      const sign = pass < 32 ? -1 : 1, start = sign < 0 ? 1450 : 150;
      await page.mouse.move(start, 650); await page.mouse.down({ button: 'middle' });
      for (let i = 1; i <= 24; i++) { await page.mouse.move(start + sign * 1300 * i / 24, 650 + 30 * Math.sin(i / 24 * Math.PI * 2)); await page.waitForTimeout(12); }
      await page.mouse.up({ button: 'middle' });
      if (pass % 4 === 3) {
        for (const delta of [-80, -80, 80, 80]) { await page.mouse.wheel(0, delta); await page.waitForTimeout(35); }
        samples.push({ pass, ...(await details(page)) });
        console.log('LONG', pass, samples.at(-1).stats?.tiles, samples.at(-1).stats?.textureBytes);
      }
    }
    await page.waitForTimeout(500);
  }
}
function rasterSummary(events) {
  const threads = new Map(events.filter(e => e.ph === 'M' && e.name === 'thread_name').map(e => [`${e.pid}:${e.tid}`, e.args.name]));
  const totals = new Map();
  for (const event of events) {
    if (event.ph !== 'X' || !/DoRasterCHROMIUM|RasterTask/.test(event.name)) continue;
    const key = `${threads.get(`${event.pid}:${event.tid}`) ?? event.tid}/${event.name}`;
    const item = totals.get(key) ?? { count: 0, totalMs: 0, maxMs: 0 };
    item.count++; item.totalMs += event.dur / 1000; item.maxMs = Math.max(item.maxMs, event.dur / 1000); totals.set(key, item);
  }
  return Object.fromEntries(totals);
}
async function run(scene, round) {
  const renderer = 'webgl2';
  const camera = ['wide', 'long'].includes(scene) ? atZoom(.18424) : near;
  const profile = await api('/application');
  profile.values = { ...profile.values, 'oaw.locale': 'en', 'oaw-theme': scene === 'dark' ? 'dark' : 'light',
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: { viewport: camera, mapPins: [] } }) };
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: scene === 'dpr2' ? 2 : 1 });
  await context.route('**/api/**', async route => {
    const req = route.request(), pathname = new URL(req.url()).pathname;
    if (req.method() === 'GET' && pathname === '/api/application') return route.fulfill({ json: profile });
    if (req.method() === 'GET' && pathname === '/api/world') {
      const response = await route.fetch(), world = await response.json();
      world.terrain_seed = 1745868525;
      if (scene !== 'cards') { world.nodes = []; world.edges = []; }
      return route.fulfill({ response, json: world });
    }
    return ['GET', 'HEAD', 'OPTIONS'].includes(req.method()) ? route.continue() : route.fulfill({ json: {} });
  });
  await context.routeWebSocket('**/*', socket => socket.onMessage(() => {}));
  await context.addInitScript(instrument);
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(origin);
  await settle(page); await page.waitForTimeout(1600);
  const initial = await details(page);
  if (initial.error || initial.svgTiles || !initial.stats) throw new Error(`Background unavailable ${JSON.stringify(initial)}`);
  if (scene === 'cards' ? initial.cards !== 48 : initial.cards !== 0) throw new Error(`Wrong fixture ${JSON.stringify(initial)}`);
  if (round === 0) await page.screenshot({ path: path.join(output, `${scene}-${renderer}.png`) });
  const cdp = await context.newCDPSession(page); await cdp.send('Performance.enable');
  const events = [];
  if (tracing) { cdp.on('Tracing.dataCollected', event => events.push(...event.value)); await cdp.send('Tracing.start', { categories: 'devtools.timeline,disabled-by-default-devtools.timeline,blink,cc,gpu,blink.user_timing', transferMode: 'ReportEvents' }); }
  const phases = {}, samples = [], processMemory = [];
  let memoryTimer, memoryPending = Promise.resolve();
  if (args.includes('--memory') && process.platform === 'win32') {
    const gpuId = (await browserCdp.send('SystemInfo.getProcessInfo')).processInfo.find(p => p.type === 'GPU')?.id;
    if (!Number.isInteger(gpuId)) throw new Error('GPU process missing');
    const sampleMemory = async () => {
      const { stdout } = await execute('powershell.exe', ['-NoProfile', '-Command',
        `Get-Counter -Counter '\\GPU Process Memory(*)\\Dedicated Usage','\\GPU Process Memory(*)\\Shared Usage' -MaxSamples 1 | ForEach-Object { $_.CounterSamples | Where-Object { $_.InstanceName -like 'pid_${gpuId}_*' } | Select-Object Path,CookedValue } | ConvertTo-Json -Compress`], { windowsHide: true });
      processMemory.push({ time: Date.now(), gpuId, counters: JSON.parse(stdout), ...(await details(page)) });
    };
    await sampleMemory();
    memoryTimer = setInterval(() => {
      memoryPending = memoryPending.then(sampleMemory).catch(error => { processMemory.push({ time: Date.now(), error: String(error) }); });
    }, 5000);
    memoryTimers.add(memoryTimer);
  }
  for (const phase of scene === 'long' ? ['continuous'] : ['pan', 'zoom']) {
    const before = await cdp.send('Performance.getMetrics'), procBefore = await browserCdp.send('SystemInfo.getProcessInfo');
    await page.evaluate(() => { Object.assign(window.__terrainBench, { active: true, frames: [], longTasks: [], workerMessages: 0 }); });
    const started = performance.now();
    await gesture(page, phase, samples);
    const elapsed = performance.now() - started;
    const measured = await page.evaluate(() => { window.__terrainBench.active = false; return window.__terrainBench; });
    const after = await cdp.send('Performance.getMetrics'), procAfter = await browserCdp.send('SystemInfo.getProcessInfo');
    const processes = procAfter.processInfo.map(p => ({ ...p, cpuTime: p.cpuTime - (procBefore.processInfo.find(b => b.id === p.id)?.cpuTime ?? p.cpuTime) }));
    const gpuCpuPercent = processes.filter(p => p.type === 'GPU').reduce((sum, p) => sum + p.cpuTime, 0) / (elapsed / 1000) * 100;
    phases[phase] = { elapsed, frames: stat(measured.frames), gpuCpuPercent, processes, longTasks: measured.longTasks, workerMessages: measured.workerMessages,
      metrics: Object.fromEntries(after.metrics.filter(m => /Duration|LayoutCount|RecalcStyleCount/.test(m.name)).map(m => [m.name, m.value - (before.metrics.find(b => b.name === m.name)?.value ?? 0)])) };
    console.log(JSON.stringify({ scene, renderer, round, phase, ...phases[phase].frames, gpuCpuPercent }));
    await page.waitForTimeout(350);
  }
  let raster;
  if (tracing) { const done = new Promise(resolve => cdp.once('Tracing.tracingComplete', resolve)); await cdp.send('Tracing.end'); await done;
    await writeFile(path.join(output, `trace-${scene}-${renderer}-${round}.json`), JSON.stringify({ traceEvents: events })); raster = rasterSummary(events); }
  await settle(page);
  clearInterval(memoryTimer); memoryTimers.delete(memoryTimer); await memoryPending;
  results.push({ scene, renderer, round, initial, final: await details(page), phases, samples, processMemory, raster, errors });
  await writeFile(path.join(output, 'report.json'), JSON.stringify({ environment, methodology: { isolatedBackend: true, readonlyProfileAndWorldProjection: true, fixedSeed: 1745868525, rounds, tracing, cpuThrottle: 1, gpuCpuUnit: 'percent of one CPU core, not GPU hardware utilization', cardFixture: '48 real text cards from isolated backend; no live runtime events', frames: 'requestAnimationFrame intervals, not presentation timestamps' }, results }, null, 2));
  await context.close();
}
try {
  // Refuse to run fixture mutations against an already occupied service port.
  await Promise.all([requireFreePort(backendPort), requireFreePort(port)]);
  start(path.join(project, 'backend', '.venv', 'Scripts', 'python.exe'), ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', backendPort, '--no-proxy-headers'], project,
    { OPEN_AGENT_WORLD_AGENT_RUNTIME: 'mock', OPEN_AGENT_WORLD_DATA_ROOT: path.join(output, 'data') });
  start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', port, '--strictPort'], frontend,
    { OAW_DEV_BACKEND_HTTP_URL: apiOrigin, OAW_DEV_BACKEND_WS_URL: `ws://127.0.0.1:${backendPort}` });
  await Promise.all([ready(`${apiOrigin}/api/world`), ready(origin)]);
  if (scenes.includes('cards')) for (let i = 0; i < 48; i++) await api('/nodes', 'POST', { type: 'text', name: `Terrain benchmark ${i}`,
    position: { x: (140 + (i % 8) * 175 - near.x) / near.zoom, y: (140 + Math.floor(i / 8) * 130 - near.y) / near.zoom } });
  browser = await chromium.launch({ channel: option('--channel', 'msedge'), headless: true });
  browserCdp = await browser.newBrowserCDPSession();
  environment = { browser: browser.version(), headless: true, cpu: os.cpus()[0]?.model, logicalCpus: os.cpus().length, gpu: (await browserCdp.send('SystemInfo.getInfo')).gpu };
  for (const scene of scenes) for (let round = 0; round < (scene === 'long' ? 1 : rounds); round++) await run(scene, round);
} finally {
  for (const timer of memoryTimers) clearInterval(timer);
  await browser?.close();
  for (const child of children.reverse()) {
    if (!child.pid || child.exitCode !== null) continue;
    if (process.platform === 'win32') {
      const stopped = spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore', timeout: 10000 });
      if (stopped.status !== 0) {
        for (const pid of child.runtimePids) try { process.kill(pid, 'SIGKILL'); } catch { /* already exited */ }
        child.kill('SIGKILL');
      }
    }
    else child.kill('SIGTERM');
    child.stdout.destroy(); child.stderr.destroy(); child.unref();
  }
}
