import assert from 'node:assert/strict';
import { access, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import test from 'node:test';
import ts from 'typescript';
import { buildPackFrontend } from './build.mjs';
import { buildPackFrontendVite } from './build-vite.mjs';
import { sourcePackArtifacts } from '../server/source-packs.mjs';
import { buildSourcePack, checkProject, dependencyFingerprint, preparePack } from '../scripts/source-packs.mjs';
import { createServer } from 'vite';
import sdkExports from './sdk-exports.mjs';
import { sdkRuntimeExports } from '../scripts/pack-sdk-exports.mjs';
import { cachedBuild, treeFingerprint } from '../scripts/build-cache.mjs';
import { buildPackSdk } from '../scripts/build-pack-sdk.mjs';

async function temporaryDirectory() {
  const base = fileURLToPath(new URL('../../.tmp/', import.meta.url));
  await mkdir(base, { recursive: true });
  return mkdtemp(path.join(base, 'pack-build-'));
}

test('dev serves Core and independent Packs when one build fails, while CI stays strict', async () => {
  const root = await temporaryDirectory();
  const packs = [{ owner: 'fixture.broken', root: path.join(root, 'broken') }, { owner: 'fixture.good', root: path.join(root, 'good') }];
  await mkdir(path.join(packs[1].root, 'dist'), { recursive: true });
  await writeFile(path.join(packs[1].root, 'dist/index.js'), 'export default "good";');
  const buildPack = async pack => { if (pack.owner === 'fixture.broken') throw new Error('deliberate missing dependency'); };
  const discover = async () => packs;
  const strict = sourcePackArtifacts({ discover, buildPack });
  await assert.rejects(() => strict.buildStart(), /deliberate missing dependency/);
  const previousReport = process.env.OAW_PACK_FAILURE_REPORT;
  process.env.OAW_PACK_FAILURE_REPORT = path.join(root, 'setup-failures.jsonl');
  const server = await createServer({ configFile: false, root,
    server: { host: '127.0.0.1', port: 0 }, plugins: [sourcePackArtifacts({ discover, buildPack })] });
  try {
    await server.listen();
    const base = `http://127.0.0.1:${server.httpServer.address().port}`;
    assert.equal((await fetch(base + '/assets/packs/fixture.good/index.js')).status, 200);
    assert.equal((await fetch(base + '/assets/packs/fixture.broken/index.js')).status, 404);
    assert.deepEqual(JSON.parse((await readFile(process.env.OAW_PACK_FAILURE_REPORT, 'utf8')).trim()),
      { id: 'fixture.broken', error: 'deliberate missing dependency' });
  } finally {
    if (previousReport === undefined) delete process.env.OAW_PACK_FAILURE_REPORT;
    else process.env.OAW_PACK_FAILURE_REPORT = previousReport;
    await server.close();
    await rm(root, { recursive: true, force: true });
  }
});

test('published runtime export list matches the complete SDK value surface', () => {
  const root = new URL('../', import.meta.url);
  const configPath = fileURLToPath(new URL('tsconfig.app.json', root));
  const config = ts.readConfigFile(configPath, ts.sys.readFile);
  const filename = fileURLToPath(new URL('src/plugins/sdk.ts', root));
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, path.dirname(configPath));
  const program = ts.createProgram([filename], parsed.options);
  assert.deepEqual(sdkExports, sdkRuntimeExports(program, filename));
});

test('build delivers shared SDK exports, CSS, relative assets and a worker without bundling React', async () => {
  const root = await temporaryDirectory();
  try {
    await writeFile(path.join(root, 'entry.tsx'), `import { Settings2, CardFace, useWorkspaceNavigation } from '@oaw/plugin-api';
      import { useState } from 'react'; import './style.css'; import asset from './sample.svg?url';
      export default { apiVersion: 1, views: { body: () => <Settings2 /> }, asset, useState, CardFace, useWorkspaceNavigation };
      export const worker = () => new Worker(new URL('./workers/analysis.js', import.meta.url), {type: 'module'});`);
    await writeFile(path.join(root, 'style.css'), '.example { background-image: url("./sample.svg"); color: red; }');
    await writeFile(path.join(root, 'sample.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>');
    await writeFile(path.join(root, 'worker.ts'), 'self.onmessage = event => self.postMessage(event.data + 1);');
    await writeFile(path.join(root, 'package.json'), '{"type":"module"}');
    const outfile = path.join(root, 'dist/index.js');
    const result = await buildPackFrontend({ entryPoint: path.join(root, 'entry.tsx'), outfile, minify: false,
      workers: { analysis: path.join(root, 'worker.ts') } });
    const js = await readFile(outfile, 'utf8');
    assert.match(js, /oaw\.frontend\.host\.v1/);
    assert.match(js, /shared\.Settings2/);
    assert.match(js, /new URL\(.*import\.meta\.url/);
    assert.match(js, /link\.onerror/);
    assert.ok(!Object.keys(result.metafile.inputs).some(name => /node_modules[/\\]react/.test(name)));
    const manifest = JSON.parse(await readFile(path.join(root, 'dist/frontend-build.json'), 'utf8'));
    assert.equal(manifest.entry, 'index.js');
    assert.ok(manifest.files.some(file => file.path === 'index.css'));
    assert.ok(manifest.files.some(file => file.path === 'workers/analysis.js'));
    assert.ok(manifest.files.some(file => /^assets\/sample-.*\.svg$/.test(file.path)));
    for (const file of manifest.files) {
      assert.equal((await readFile(path.join(root, 'dist', file.path))).length, file.size_bytes);
      assert.match(file.sha256, /^[a-f0-9]{64}$/);
    }
    // Execute the emitted module too: shared identity and asset bases are
    // runtime contracts that text matching alone does not prove.
    const key = Symbol.for('oaw.frontend.host.v1');
    const original = globalThis[key];
    const host = { react: { useState() {} }, reactDom: {}, jsx: { jsx() {} }, sdk: { Settings2() {}, CardFace() {}, useWorkspaceNavigation() {} } };
    globalThis[key] = host;
    try {
      const module = await import(pathToFileURL(outfile).href);
      assert.equal(module.default.useState, host.react.useState);
      assert.equal(module.default.CardFace, host.sdk.CardFace);
      assert.equal(module.default.useWorkspaceNavigation, host.sdk.useWorkspaceNavigation);
      assert.ok(module.default.asset.startsWith(pathToFileURL(path.join(root, 'dist/assets/')).href));
    } finally {
      if (original === undefined) delete globalThis[key]; else globalThis[key] = original;
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('workers reject window-only shared SDK imports', async () => {
  const root = await temporaryDirectory();
  try {
    await writeFile(path.join(root, 'entry.js'), 'export default {};');
    await writeFile(path.join(root, 'worker.js'), 'import { t } from "@oaw/plugin-api"; self.postMessage(t("hello"));');
    await assert.rejects(buildPackFrontend({ entryPoint: path.join(root, 'entry.js'), outfile: path.join(root, 'dist/index.js'),
      workers: { analysis: path.join(root, 'worker.js') } }), /cannot import the window host runtime/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('Vite Pack artifact carries native workers and CSS and refuses private host imports', async () => {
  const root = await temporaryDirectory();
  try {
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ type: 'module', peerDependencies: { react: '^18' } }));
    await writeFile(path.join(root, 'index.tsx'), `import { useState } from 'react'; import './style.css';
      export const worker = () => new Worker(new URL('./worker.ts', import.meta.url), {type:'module'});
      export default {apiVersion:1, views:{}, useState};`);
    await writeFile(path.join(root, 'style.css'), '.pack-example { color: red }');
    await writeFile(path.join(root, 'worker.ts'), 'self.onmessage = event => self.postMessage(event.data + 1);');
    const artifact = await buildPackFrontendVite({ root });
    assert.ok(artifact.files.some(file => /worker.*\.js$/.test(file.path)));
    assert.ok(artifact.files.some(file => file.path.endsWith('.css')));
    assert.match(await readFile(path.join(root, 'dist/index.js'), 'utf8'), /link\.onerror|Unable to load Pack stylesheet/);
    await writeFile(path.join(root, 'index.tsx'), 'import value from "../../../frontend/src/plugins/sdk"; export default value;');
    await assert.rejects(buildPackFrontendVite({ root }), /source import escapes/);
    await writeFile(path.join(root, 'index.tsx'), 'import value from "undeclared-package"; export default value;');
    await assert.rejects(buildPackFrontendVite({ root }), /Undeclared Pack frontend dependency/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('Vite Pack dependencies use their owning manifests without exposing transitives to Pack sources', async () => {
  const root = await temporaryDirectory();
  const install = async (relative, manifest, source) => {
    const directory = path.join(root, 'node_modules', relative);
    await mkdir(directory, { recursive: true });
    await writeFile(path.join(directory, 'package.json'), JSON.stringify({ type: 'module', main: 'index.js', ...manifest }));
    await writeFile(path.join(directory, 'index.js'), source);
  };
  try {
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ type: 'module', dependencies: { '@fixture/parent': '1.0.0' },
      devDependencies: { devpeer: '1.0.0' } }));
    await install('@fixture/parent', { name: '@fixture/parent', dependencies: { leaf: '1.0.0' },
      peerDependencies: { peer: '1.0.0' }, optionalDependencies: { optional: '1.0.0' }, devDependencies: { devpeer: '1.0.0' } },
      'import a from "leaf"; import b from "peer"; import c from "optional"; import d from "devpeer"; export default a + b + c + d;');
    await install('@fixture/parent/node_modules/leaf', { name: 'leaf', dependencies: { nested: '1.0.0' } },
      'import value from "nested"; export default value;');
    await install('@fixture/parent/node_modules/leaf/node_modules/nested', { name: 'nested' }, 'export default 1;');
    await install('peer', { name: 'peer' }, 'export default 2;');
    await install('optional', { name: 'optional' }, 'export default 3;');
    await install('devpeer', { name: 'devpeer' }, 'export default 4;');
    await writeFile(path.join(root, 'index.tsx'), 'import value from "@fixture/parent"; export default value;');
    await buildPackFrontendVite({ root });
    assert.equal((await import(pathToFileURL(path.join(root, 'dist/index.js')).href)).default, 10);

    // Merely having a package installed does not authorize the Pack to import it.
    await writeFile(path.join(root, 'index.tsx'), 'import value from "peer"; export default value;');
    await assert.rejects(buildPackFrontendVite({ root }), /Undeclared Pack frontend dependency: peer/);
    // Published vendors may declare runtime peers as dev dependencies. This
    // compatibility does not grant Pack source access to its build tooling.
    await writeFile(path.join(root, 'index.tsx'), 'import value from "devpeer"; export default value;');
    await assert.rejects(buildPackFrontendVite({ root }), /Undeclared Pack frontend dependency: devpeer/);
    await writeFile(path.join(root, 'index.tsx'), 'import value from "@fixture/parent"; export default value;');
    // The nearest nested package also cannot borrow its parent's declaration.
    await writeFile(path.join(root, 'node_modules/@fixture/parent/node_modules/leaf/index.js'),
      'import value from "optional"; export default value;');
    await assert.rejects(buildPackFrontendVite({ root }), /Undeclared Pack frontend dependency: optional/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('Vite checks cached dependency links with the same Pack boundary as installed packages', async () => {
  const root = await temporaryDirectory();
  const cache = await temporaryDirectory();
  try {
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ type: 'module', dependencies: { linked: '1.0.0' } }));
    await writeFile(path.join(root, 'index.tsx'), 'import value from "linked"; export default value;');
    await mkdir(path.join(root, 'node_modules/undeclared'), { recursive: true });
    await writeFile(path.join(root, 'node_modules/undeclared/package.json'), '{"name":"undeclared","type":"module","main":"index.js"}');
    await writeFile(path.join(root, 'node_modules/undeclared/index.js'), 'export default 7;');
    await writeFile(path.join(cache, 'package.json'), '{"name":"linked","type":"module","main":"index.js"}');
    await writeFile(path.join(cache, 'index.js'), 'import value from "undeclared"; export default value;');
    await symlink(cache, path.join(root, 'node_modules/linked'), 'junction');
    await assert.rejects(buildPackFrontendVite({ root }), /Undeclared Pack frontend dependency: undeclared/);
    await writeFile(path.join(cache, 'package.json'), JSON.stringify({ name: 'linked', type: 'module', main: 'index.js',
      dependencies: { undeclared: '1.0.0' } }));
    await buildPackFrontendVite({ root });
    assert.equal((await import(pathToFileURL(path.join(root, 'dist/index.js')).href)).default, 7);
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(cache, { recursive: true, force: true });
  }
});

test('real source Pack build isolates development NODE_ENV and emits release JSX', async () => {
  const root = await temporaryDirectory();
  const previous = process.env.NODE_ENV;
  const runtimeKey = Symbol.for('oaw.frontend.host.v1');
  const previousRuntime = globalThis[runtimeKey];
  try {
    const sdk = fileURLToPath(new URL('./', import.meta.url));
    const devDependencies = { '@oaw/plugin-api': 'file:' + path.relative(root, sdk).replaceAll('\\', '/') };
    await writeFile(path.join(root, 'package.json'), JSON.stringify({
      name: 'oaw-development-build-fixture', version: '1.0.0', type: 'module',
      scripts: { build: 'node build.mjs' }, devDependencies,
    }));
    await writeFile(path.join(root, 'package-lock.json'), JSON.stringify({
      lockfileVersion: 3, packages: { '': { devDependencies } },
    }));
    await writeFile(path.join(root, 'index.tsx'), 'export default <section>shared JSX</section>;');
    await writeFile(path.join(root, 'build.mjs'), `
      import { buildPackFrontendVite } from '@oaw/plugin-api/build-vite';
      if (process.env.NODE_ENV !== 'production') throw new Error('Pack inherited host development mode');
      await buildPackFrontendVite({});
    `);
    process.env.NODE_ENV = 'development';
    // Direct SDK callers also get release JSX without altering their own env.
    await buildPackFrontendVite({ root });
    assert.equal(process.env.NODE_ENV, 'development');
    // Exercise npm and the actual child process, unlike the injected watcher fixture.
    const pack = { root, owner: 'test.development' };
    await assert.rejects(buildSourcePack(pack), /Dependencies missing or stale/);
    await assert.rejects(access(path.join(root, 'node_modules')));
    await preparePack(pack);
    assert.equal(await buildSourcePack(pack), true);
    assert.equal(await buildSourcePack(pack), false, 'unchanged source reuses the completed artifact');
    await writeFile(path.join(root, 'index.tsx'), 'export default <section>changed JSX</section>;');
    assert.equal(await buildSourcePack(pack), true);
    assert.equal(await buildSourcePack(pack), false);
    await rm(path.join(root, 'dist/index.js'));
    assert.equal(await buildSourcePack(pack), true, 'missing outputs are rebuilt');
    assert.equal(await buildSourcePack(pack, { force: true }), true);
    assert.equal(process.env.NODE_ENV, 'development');
    const output = path.join(root, 'dist/index.js');
    assert.doesNotMatch(await readFile(output, 'utf8'), /jsx-dev-runtime|jsxDEV/);
    globalThis[runtimeKey] = { jsx: { jsx: (tag, props) => ({ tag, props }) } };
    assert.deepEqual((await import(pathToFileURL(output).href)).default,
      { tag: 'section', props: { children: 'changed JSX' } });
    // A valid artifact cannot hide dependency loss or a failed rebuild, and
    // neither case is allowed to invoke an installer.
    await rm(path.join(root, 'node_modules/@oaw/plugin-api'), { force: true, recursive: true });
    await assert.rejects(buildSourcePack(pack), /Dependencies missing or stale/);
    await assert.rejects(access(path.join(root, 'node_modules/@oaw/plugin-api')));
  } finally {
    if (previous === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = previous;
    if (previousRuntime === undefined) delete globalThis[runtimeKey]; else globalThis[runtimeKey] = previousRuntime;
    await rm(root, { recursive: true, force: true });
  }
});

test('build receipts invalidate on added/deleted sources, damaged assets, failures and concurrent source edits', async () => {
  const root = await temporaryDirectory();
  try {
    const source = path.join(root, 'src');
    const output = path.join(root, 'dist');
    const receiptFile = path.join(root, 'cache/build.json');
    await mkdir(source); await mkdir(output);
    const entry = path.join(source, 'entry.js');
    await writeFile(entry, 'before');
    let builds = 0, fail = false, editDuringBuild = false;
    const run = () => cachedBuild({ receiptFile,
      inputs: () => treeFingerprint(source), outputs: () => treeFingerprint(output),
      build: async () => {
        builds++;
        if (fail) throw new Error('compile failed');
        await writeFile(path.join(output, 'index.js'), await readFile(entry));
        await writeFile(path.join(output, 'worker.js'), 'worker');
        if (editDuringBuild) await writeFile(entry, 'racing');
      },
    });
    assert.equal(await run(), true); assert.equal(await run(), false); assert.equal(builds, 1);
    await writeFile(entry, 'after!'); // Same byte length, different content.
    assert.equal(await run(), true);
    await writeFile(path.join(source, 'new.css'), 'new'); assert.equal(await run(), true);
    await rm(path.join(source, 'new.css')); assert.equal(await run(), true);
    await writeFile(path.join(output, 'worker.js'), 'broken'); assert.equal(await run(), true);
    await rm(path.join(output, 'worker.js')); assert.equal(await run(), true);
    await writeFile(entry, 'failed'); fail = true;
    await assert.rejects(run(), /compile failed/);
    await assert.rejects(access(receiptFile));
    await writeFile(entry, 'after!'); fail = false;
    assert.equal(await run(), true, 'reverting a failed source must not revive an old receipt');
    await writeFile(entry, 'before'); editDuringBuild = true;
    assert.equal(await run(), true); await assert.rejects(access(receiptFile));
    editDuringBuild = false;
    assert.equal(await run(), true); assert.equal(await run(), false);
    await writeFile(receiptFile, '{broken'); assert.equal(await run(), true);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('dependency checks detect missing, changed and nested packages without repairing them', async () => {
  const root = await temporaryDirectory();
  try {
    const dependencies = { fixture: '^1.0.0' };
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ dependencies }));
    const lock = { packages: {
      '': { dependencies },
      'node_modules/fixture': { version: '1.2.0' },
      'node_modules/fixture/node_modules/nested': { version: '2.0.0' },
      'node_modules/other-platform': { version: '1.0.0', os: ['!' + process.platform] },
    } };
    await writeFile(path.join(root, 'package-lock.json'), JSON.stringify(lock));
    await assert.rejects(checkProject(root), /Dependencies missing or stale/);
    await assert.rejects(access(path.join(root, 'node_modules')));
    for (const [key, item] of Object.entries(lock.packages).filter(([key]) => key.includes('fixture'))) {
      await mkdir(path.join(root, key), { recursive: true });
      await writeFile(path.join(root, key, 'package.json'), JSON.stringify({ version: item.version }));
    }
    await checkProject(root);
    const originalFingerprint = await dependencyFingerprint(root);
    const nested = path.join(root, 'node_modules/fixture/node_modules/nested/package.json');
    await writeFile(nested, '{"version":"2.0.0"}');
    assert.notEqual(await dependencyFingerprint(root), originalFingerprint, 'reinstalling even the same version invalidates builds');
    await writeFile(nested, '{"version":"1.0.0"}');
    await assert.rejects(checkProject(root), /fixture\/node_modules\/nested/);
    assert.equal(await readFile(nested, 'utf8'), '{"version":"1.0.0"}');
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ dependencies: { fixture: '^2.0.0' } }));
    await assert.rejects(checkProject(root), /Update package-lock.json/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('SDK reuses declarations and exports, rebuilds missing outputs, and does not cache compilation failures', async () => {
  const root = await temporaryDirectory();
  try {
    await mkdir(path.join(root, 'src/plugins'), { recursive: true });
    await mkdir(path.join(root, 'pack-sdk'));
    await writeFile(path.join(root, 'package.json'), '{}');
    await writeFile(path.join(root, 'package-lock.json'), '{"packages":{"":{}}}');
    await writeFile(path.join(root, 'tsconfig.app.json'), '{"compilerOptions":{"target":"ES2022","module":"ESNext","skipLibCheck":true},"include":["src"]}');
    await writeFile(path.join(root, 'src/vite-env.d.ts'), '');
    const source = path.join(root, 'src/plugins/sdk.ts');
    await writeFile(source, 'export const first = 1;');
    assert.equal(await buildPackSdk({ root }), true);
    assert.equal(await buildPackSdk({ root }), false);
    await writeFile(source, 'export const second = 2;');
    assert.equal(await buildPackSdk({ root }), true);
    assert.match(await readFile(path.join(root, 'pack-sdk/sdk-exports.mjs'), 'utf8'), /\["second"\]/);
    const declaration = path.join(root, 'pack-sdk/types/plugins/sdk.d.ts');
    await rm(declaration);
    assert.equal(await buildPackSdk({ root }), true);
    await writeFile(source, 'export const second: number = "bad";');
    await assert.rejects(buildPackSdk({ root }), /not assignable/);
    await assert.rejects(access(path.join(root, 'node_modules/.cache/oaw/pack-sdk.json')));
    assert.match(await readFile(declaration, 'utf8'), /second/);
    await writeFile(source, 'export const second = 2;');
    assert.equal(await buildPackSdk({ root }), true);
    assert.equal(await buildPackSdk({ root }), false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('Pack checks reject an SDK link to a different checkout without replacing it', async () => {
  const root = await temporaryDirectory();
  try {
    const sdk = fileURLToPath(new URL('./', import.meta.url));
    const devDependencies = { '@oaw/plugin-api': 'file:' + path.relative(root, sdk).replaceAll('\\', '/') };
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ devDependencies }));
    await writeFile(path.join(root, 'package-lock.json'), JSON.stringify({ packages: { '': { devDependencies } } }));
    const wrong = path.join(root, 'other-sdk');
    await mkdir(wrong);
    await writeFile(path.join(wrong, 'package.json'), '{"name":"@oaw/plugin-api"}');
    await mkdir(path.join(root, 'node_modules/@oaw'), { recursive: true });
    await symlink(wrong, path.join(root, 'node_modules/@oaw/plugin-api'), 'junction');
    await assert.rejects(preparePack({ root, owner: 'fixture' }, { install: false }), /Dependencies missing or stale/);
    assert.equal(await readFile(path.join(wrong, 'package.json'), 'utf8'), '{"name":"@oaw/plugin-api"}');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('development reuses artifacts across restarts and still rebuilds on source changes', async () => {
  const root = await temporaryDirectory();
  let server;
  try {
    const sdk = fileURLToPath(new URL('./', import.meta.url));
    const devDependencies = { '@oaw/plugin-api': 'file:' + path.relative(root, sdk).replaceAll('\\', '/') };
    await writeFile(path.join(root, 'package.json'), JSON.stringify({ type: 'module', scripts: { build: 'node build.mjs' }, devDependencies }));
    await writeFile(path.join(root, 'package-lock.json'), JSON.stringify({ packages: { '': { devDependencies } } }));
    await writeFile(path.join(root, 'build.mjs'), "import { buildPackFrontendVite } from '@oaw/plugin-api/build-vite'; await buildPackFrontendVite({});");
    await writeFile(path.join(root, 'index.tsx'), 'export default {value:"before-watch"};');
    const pack = { root, owner: 'test.source' };
    await preparePack(pack);
    let builds = 0;
    const start = () => createServer({ root, configFile: false, server: { port: 0, host: '127.0.0.1' },
      plugins: [sourcePackArtifacts({ discover: async () => [pack], buildPack: async () => { if (await buildSourcePack(pack)) builds++; } })] });
    server = await start();
    await server.listen();
    assert.equal(builds, 1);
    await server.close();
    server = await start();
    await server.listen();
    assert.equal(builds, 1, 'restart must reuse the Pack');
    const address = server.httpServer.address();
    const url = `http://127.0.0.1:${address.port}/assets/packs/test.source/index.js`;
    assert.equal(await (await fetch(url)).text(), await readFile(path.join(root, 'dist/index.js'), 'utf8'));
    // Wait until the OS watcher has registered the source, then change it.
    await new Promise(resolve => setTimeout(resolve, 150));
    await writeFile(path.join(root, 'index.tsx'), 'export default {value:"after-watch"};');
    let observed = false;
    for (let attempt = 0; attempt < 50; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 100));
      if ((await (await fetch(url)).text()).includes('after-watch') && builds === 2) { observed = true; break; }
    }
    assert.equal(observed, true); assert.equal(builds, 2);
    assert.equal((await fetch(url.replace('index.js', '%2e%2e%2fpackage.json'))).status, 404);
  } finally { await server?.close(); await rm(root, { recursive: true, force: true }); }
});
