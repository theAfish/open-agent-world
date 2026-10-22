// Only Node built-ins: run on a fresh checkout without npm ci, including POSIX.
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { runNpm, sourcePacks, publishSourcePack } from './source-packs.mjs';
import { cachedBuild, treeFingerprint } from './build-cache.mjs';
import { frontendBuildFingerprint } from './build-source-frontend.mjs';

test('a direct Node launcher finds npm without npm_execpath and preserves the build environment', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'oaw source startup '));
  const previous = process.env.npm_execpath;
  try {
    delete process.env.npm_execpath;
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ type: 'module', scripts: { build: 'node build.mjs' } }));
    await writeFile(path.join(root, 'build.mjs'), `import {writeFileSync} from 'node:fs';
      writeFileSync('result.json', JSON.stringify({cwd:process.cwd(), mode:process.env.NODE_ENV}));`);
    await runNpm(['run', 'build'], root, { ...process.env, NODE_ENV: 'production' });
    const result = JSON.parse(await readFile(path.join(root, 'result.json'), 'utf8'));
    assert.deepEqual({ ...result, cwd: await realpath(result.cwd) }, { cwd: await realpath(root), mode: 'production' });
  } finally {
    if (previous === undefined) delete process.env.npm_execpath; else process.env.npm_execpath = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test('research frontend freshness includes host config, Pack types and copied Pack artifacts', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'oaw research build '));
  const previousApi = process.env.VITE_API_BASE;
  try {
    const pack = { owner: 'fixture', root: path.join(root, 'pack') };
    for (const project of [root, pack.root]) {
      await mkdir(project, { recursive: true });
      await writeFile(path.join(project, 'package.json'), '{}');
      await writeFile(path.join(project, 'package-lock.json'), '{"packages":{"":{}}}');
    }
    const input = () => frontendBuildFingerprint(root, [pack]);
    let previous = await input();
    for (const file of ['vite.config.ts', 'src/host.ts', 'public/icon.svg', 'pack/index.tsx', 'pack/dist/index.js']) {
      const target = path.join(root, file);
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, 'changed');
      const current = await input();
      assert.notEqual(current, previous, file + ' must invalidate the built frontend');
      previous = current;
    }
    assert.equal(await input(), previous);
    process.env.VITE_API_BASE = '/fixture-api';
    assert.notEqual(await input(), previous, 'compiled API configuration must invalidate the frontend');
  } finally {
    if (previousApi === undefined) delete process.env.VITE_API_BASE; else process.env.VITE_API_BASE = previousApi;
    await rm(root, { recursive: true, force: true });
  }
});

test('build receipts work across platforms and repair missing outputs without an installer', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'oaw build cache '));
  try {
    await mkdir(path.join(root, 'src')); await mkdir(path.join(root, 'dist'));
    const source = path.join(root, 'src/input');
    const output = path.join(root, 'dist/output');
    await writeFile(source, 'first');
    let builds = 0;
    const run = () => cachedBuild({ receiptFile: path.join(root, 'cache/receipt.json'),
      inputs: () => treeFingerprint(path.join(root, 'src')), outputs: () => treeFingerprint(path.join(root, 'dist')),
      build: async () => { builds++; await writeFile(output, await readFile(source)); },
    });
    assert.equal(await run(), true); assert.equal(await run(), false);
    await writeFile(source, 'later'); assert.equal(await run(), true);
    await rm(output); assert.equal(await run(), true);
    assert.equal(builds, 3);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('explicit Pack preparation excludes siblings and publishes only its own assets', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'oaw selected packs '));
  const previous = process.env.OPEN_AGENT_WORLD_CORE_ONLY;
  try {
    const selected = path.join(root, 'selected');
    const sibling = path.join(root, 'sibling');
    const output = path.join(root, 'host/assets/packs');
    await mkdir(path.join(selected, 'dist'), { recursive: true });
    await mkdir(sibling);
    await writeFile(path.join(selected, 'plugin.json'), JSON.stringify({ apiVersion: 1, pluginId: 'test.selected' }));
    await writeFile(path.join(sibling, 'plugin.json'), 'invalid JSON');
    process.env.OPEN_AGENT_WORLD_CORE_ONLY = '1';
    const packs = await sourcePacks({ roots: [selected, selected] });
    assert.deepEqual(packs.map(pack => pack.owner), ['test.selected']);
    await writeFile(path.join(selected, 'dist/frontend-build.json'), '{}');
    await writeFile(path.join(selected, 'dist/index.js'), 'new asset');
    await mkdir(path.join(output, 'test.selected'), { recursive: true });
    await writeFile(path.join(output, 'test.selected/obsolete.js'), 'old asset');
    await mkdir(path.join(output, 'test.sibling'));
    await writeFile(path.join(output, 'test.sibling/index.js'), 'keep sibling');
    await publishSourcePack(packs[0], output);
    assert.equal(await readFile(path.join(output, 'test.selected/index.js'), 'utf8'), 'new asset');
    await assert.rejects(readFile(path.join(output, 'test.selected/obsolete.js')), { code: 'ENOENT' });
    assert.equal(await readFile(path.join(output, 'test.sibling/index.js'), 'utf8'), 'keep sibling');
    await assert.rejects(publishSourcePack({ ...packs[0], owner: '../test.sibling' }, output), /Invalid Pack frontend owner/);
    assert.equal(await readFile(path.join(output, 'test.sibling/index.js'), 'utf8'), 'keep sibling');
  } finally {
    if (previous === undefined) delete process.env.OPEN_AGENT_WORLD_CORE_ONLY; else process.env.OPEN_AGENT_WORLD_CORE_ONLY = previous;
    await rm(root, { recursive: true, force: true });
  }
});
