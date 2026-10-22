import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { appendFile, access, cp, mkdir, readFile, readdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildRuntime, cachedBuild, digest, filesFingerprint, installedFingerprint, treeFiles, treeFingerprint } from './build-cache.mjs';

export const frontendRoot = fileURLToPath(new URL('../', import.meta.url));
const repo = path.dirname(frontendRoot);
const readJson = async file => JSON.parse(await readFile(file, 'utf8'));
const exists = async file => { try { await access(file); return true; } catch { return false; } };

export async function reportPackFailure(id, error) {
  console.error(`Optional Pack ${id} unavailable: ${error.message}`);
  if (process.env.OAW_PACK_FAILURE_REPORT) {
    await appendFile(process.env.OAW_PACK_FAILURE_REPORT, JSON.stringify({ id, error: error.message }) + '\n');
  }
}

export async function sourcePacks({ strict = true, roots } = {}) {
  const packs = [];
  if (!roots && process.env.OPEN_AGENT_WORLD_CORE_ONLY === '1') return packs;
  if (!roots) {
    roots = [];
    for (const parent of ['plugins', 'examples/deployed-workspace/plugins']) {
      for (const directory of await readdir(path.join(repo, parent), { withFileTypes: true })) {
        if (!directory.isDirectory()) continue;
        const root = path.join(repo, parent, directory.name, 'frontend');
        if (!await exists(path.join(root, 'plugin.json'))) continue;
        roots.push(root);
      }
    }
  }
  for (const root of [...new Set(roots.map(root => path.resolve(root)))]) {
    try {
      const manifest = await readJson(path.join(root, 'plugin.json'));
      if (manifest.apiVersion !== 1 || !/^[a-z][a-z0-9._-]*$/.test(manifest.pluginId)) throw new Error(`Invalid Pack frontend manifest: ${root}`);
      if (packs.some(pack => pack.owner === manifest.pluginId)) throw new Error(`Duplicate Pack frontend owner: ${manifest.pluginId}`);
      packs.push({ root, owner: manifest.pluginId });
    } catch (error) {
      if (strict) throw error;
      await reportPackFailure(root, error);
    }
  }
  return packs;
}

export async function runNpm(args, cwd, env = process.env) {
  let executable = process.env.npm_execpath;
  if (!executable && process.platform === 'win32') {
    const require = createRequire(import.meta.url);
    try { executable = require.resolve('npm/bin/npm-cli.js'); }
    catch { executable = path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js'); }
  }
  // POSIX installations (system packages, Homebrew, nvm, etc.) need not place
  // npm beside node. Resolve their executable through PATH without a shell.
  // Windows keeps the JS entrypoint because .cmd cannot be spawned directly.
  await new Promise((resolve, reject) => {
    const child = spawn(executable ? process.execPath : 'npm', executable ? [executable, ...args] : args,
      { cwd, env, stdio: 'inherit', windowsHide: true });
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve() : reject(new Error(`npm ${args.join(' ')} failed for ${cwd} (${code})`)));
  });
}

/** A checkout may reuse already installed, exact lockfile versions. These are
 * ordinary project-local links; fresh checkouts use npm ci for each Pack.
 * No host runtime dependency or resolver alias is implied by this cache.
 */
const supports = (values, current) => !values || (!values.includes('!' + current) &&
  (!values.some(value => !value.startsWith('!')) || values.includes(current)));

async function projectDependencies(root) {
  const manifest = await readJson(path.join(root, 'package.json'));
  const lock = await readJson(path.join(root, 'package-lock.json'));
  for (const group of ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']) {
    const declared = manifest[group] ?? {}, locked = lock.packages['']?.[group] ?? {};
    if (Object.keys({ ...declared, ...locked }).some(name => declared[name] !== locked[name])) {
      throw new Error(`Update package-lock.json for ${root}: ${group} changed`);
    }
  }
  const required = Object.entries(lock.packages).filter(([key, item]) => key.startsWith('node_modules/') &&
    supports(item.os, process.platform) && supports(item.cpu, process.arch));
  return { manifest, lock, required };
}

async function matching(base, key, item) {
  try {
    if (item.link) return await realpath(path.join(base, key)) === await realpath(path.resolve(base, item.resolved));
    return (await readJson(path.join(base, key, 'package.json'))).version === item.version;
  } catch { return false; }
}

export async function checkProject(root) {
  const { required } = await projectDependencies(root);
  const missing = (await Promise.all(required.map(async ([key, item]) =>
    await matching(root, key, item) ? null : key))).filter(Boolean);
  if (missing.length) throw new Error(`Dependencies missing or stale in ${root}: ${missing.slice(0, 5).join(', ')}. Run scripts/setup.ps1 or scripts/setup.sh with your --profile before dev.`);
}

export async function dependencyFingerprint(root) {
  const { required } = await projectDependencies(root);
  return digest(JSON.stringify([
    await filesFingerprint(['package.json', 'package-lock.json'].map(file => path.join(root, file))),
    await installedFingerprint([
      path.join(root, 'node_modules/.package-lock.json'),
      ...required.map(([key]) => path.join(root, key, 'package.json')),
    ]),
  ]));
}

export async function preparePack(pack, { install = true } = {}) {
  const { manifest, lock, required } = await projectDependencies(pack.root);
  const sdkReference = manifest.devDependencies?.['@oaw/plugin-api'];
  if (!sdkReference) throw new Error(`Declare the versioned @oaw/plugin-api SDK: ${pack.owner}`);
  // Registry versions and published tarballs use the project's ordinary npm
  // lockfile. Checkout junctions are only an optional local cache optimization.
  const sdkDirectory = sdkReference.startsWith('file:') && await exists(path.join(pack.root, sdkReference.slice(5), 'package.json'));
  if (!sdkDirectory) {
    try { await checkProject(pack.root); return; }
    catch (error) { if (!install) throw error; }
    await runNpm(['ci', '--include=dev', '--ignore-scripts', '--no-audit', '--no-fund'], pack.root);
    await checkProject(pack.root);
    return;
  }
  const sdk = path.resolve(pack.root, sdkReference.slice(5));
  const modules = path.join(pack.root, 'node_modules');
  const cache = path.join(frontendRoot, 'node_modules');
  const sdkReady = async () => {
    try { return await realpath(path.join(modules, '@oaw/plugin-api')) === await realpath(sdk); }
    catch { return false; }
  };
  if (await sdkReady() &&
      (await Promise.all(required.map(([key, item]) => matching(pack.root, key, item)))).every(Boolean)) return;
  if (!install) throw new Error(`Dependencies missing or stale for Pack ${pack.owner}. Run setup for your development profile (or npm --prefix frontend run prepare:packs).`);
  // Reusing a cache is optional. Never mix a partially matching cache with a
  // project install, and never alter an existing project's dependency tree.
  const cached = required.filter(([, item]) => !item.link);
  if (!await exists(modules) && required.every(([key, item]) => !item.link || key === 'node_modules/@oaw/plugin-api') &&
      (await Promise.all(cached.map(([key, item]) => matching(frontendRoot, key, item)))).every(Boolean)) {
    await mkdir(path.join(modules, '@oaw'), { recursive: true });
    await symlink(sdk, path.join(modules, '@oaw/plugin-api'), 'junction');
    for (const [key] of cached) {
      if (key.slice('node_modules/'.length).includes('/node_modules/')) continue;
      const relative = key.slice('node_modules/'.length);
      const target = path.join(modules, relative);
      await mkdir(path.dirname(target), { recursive: true });
      await symlink(path.join(cache, relative), target, 'junction');
    }
    await writeFile(path.join(modules, '.oaw-cache.json'), JSON.stringify({ sha256: createHash('sha256').update(JSON.stringify(lock)).digest('hex') }));
  } else await runNpm(['ci', '--include=dev', '--ignore-scripts', '--no-audit', '--no-fund'], pack.root);
  // Never report a partial install or an incorrectly linked SDK as prepared.
  await preparePack(pack, { install: false });
}

export async function buildSourcePack(pack, { force = false } = {}) {
  const started = performance.now();
  const sdk = path.join(frontendRoot, 'pack-sdk');
  const built = await cachedBuild({
    receiptFile: path.join(pack.root, 'node_modules/.cache/oaw/build.json'),
    force,
    inputs: async () => digest(JSON.stringify([
      buildRuntime(), pack.owner,
      await treeFingerprint(pack.root, { exclude: ['node_modules', 'dist', '.git'] }),
      // Generated declarations do not affect release JS. SDK export/ABI and
      // builder changes do; every Pack must see those changes, including in dev.
      await filesFingerprint((await treeFiles(sdk, { exclude: ['types', 'node_modules'] }))
        .filter(file => file.endsWith('.mjs') && !file.endsWith('.test.mjs') || file === path.join(sdk, 'package.json'))),
      await filesFingerprint([fileURLToPath(import.meta.url), fileURLToPath(new URL('./build-cache.mjs', import.meta.url))]),
      await dependencyFingerprint(frontendRoot), await dependencyFingerprint(pack.root),
      await installedFingerprint([path.join(pack.root, 'node_modules/@oaw/plugin-api/package.json')]),
    ])),
    outputs: () => treeFingerprint(path.join(pack.root, 'dist')),
    build: async () => {
      await preparePack(pack, { install: false });
      // Keep host development mode and the Pack release ABI separate.
      await runNpm(['run', 'build'], pack.root, { ...process.env, NODE_ENV: 'production' });
      const artifact = await readJson(path.join(pack.root, 'dist/frontend-build.json'));
      for (const file of [artifact.entry, ...artifact.files.map(item => item.path)]) {
        if (!await exists(path.join(pack.root, 'dist', file))) throw new Error(`Missing Pack output: ${pack.owner}/${file}`);
      }
    },
  });
  console.log(`[Pack ${pack.owner}] ${built ? 'Built' : 'Reused'} (${((performance.now() - started) / 1000).toFixed(2)}s)`);
  return built;
}

function outputDirectory(pack, output) {
  const root = path.resolve(output);
  const destination = path.resolve(root, pack.owner);
  if (!/^[a-z][a-z0-9._-]*$/.test(pack.owner) || path.dirname(destination) !== root) {
    throw new Error(`Invalid Pack frontend owner: ${pack.owner}`);
  }
  return destination;
}

export async function publishSourcePack(pack, output) {
  const destination = outputDirectory(pack, output);
  await readFile(path.join(pack.root, 'dist/frontend-build.json'));
  try {
    await rm(destination, { recursive: true, force: true });
    await cp(path.join(pack.root, 'dist'), destination, { recursive: true });
  } catch (error) {
    await rm(destination, { recursive: true, force: true });
    throw error;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes('--check-host')) await checkProject(frontendRoot);
  else {
    if (process.argv.includes('--check')) await checkProject(frontendRoot);
    const strict = process.env.OAW_ALLOW_PACK_FAILURES !== '1';
    const roots = process.argv.flatMap((value, index) => value === '--frontend-path' ? [process.argv[index + 1]] : []);
    const outputIndex = process.argv.indexOf('--output');
    const output = outputIndex < 0 ? null : process.argv[outputIndex + 1];
    for (const pack of await sourcePacks({ strict, ...(roots.length ? { roots } : {}) })) {
      try {
        if (process.argv.includes('--prepare')) await preparePack(pack);
        else if (process.argv.includes('--check')) await preparePack(pack, { install: false });
        if (process.argv.includes('--build') || !process.argv.includes('--prepare') && !process.argv.includes('--check')) {
          await buildSourcePack(pack, { force: process.argv.includes('--force') });
          if (output) await publishSourcePack(pack, output);
        }
      } catch (error) {
        if (output) await rm(outputDirectory(pack, output), { recursive: true, force: true });
        if (strict) throw error;
        await reportPackFailure(pack.owner, error);
      }
    }
  }
}
