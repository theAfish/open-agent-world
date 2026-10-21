import { access, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildRuntime, cachedBuild, digest, filesFingerprint, treeFingerprint } from './build-cache.mjs';
import { buildSourcePack, dependencyFingerprint, frontendRoot, runNpm, sourcePacks } from './source-packs.mjs';
import { prepareDev } from './prepare-dev.mjs';

export async function frontendBuildFingerprint(root, packs) {
  const rootFiles = (await readdir(root, { withFileTypes: true })).filter(entry => entry.isFile())
    .map(entry => path.join(root, entry.name));
  return digest(JSON.stringify([
    buildRuntime(), await filesFingerprint(rootFiles), await dependencyFingerprint(root),
    await Promise.all(['src', 'public', 'server', 'scripts'].map(directory => treeFingerprint(path.join(root, directory)))),
    await Promise.all(packs.map(async pack => [pack.owner,
      await treeFingerprint(pack.root, { exclude: ['node_modules', 'dist', '.git'] }),
      await dependencyFingerprint(pack.root), await treeFingerprint(path.join(pack.root, 'dist')),
    ])),
  ]));
}

// Research serves a production frontend. Prepare its independent Pack outputs
// first so the host receipt describes the artifacts actually copied into dist.
export async function buildSourceFrontend({ force = false } = {}) {
  const started = performance.now();
  await prepareDev();
  const packs = [];
  for (const pack of await sourcePacks({ strict: false })) {
    try { await buildSourcePack(pack); packs.push(pack); }
    catch (error) { console.error(`Optional Pack ${pack.owner} unavailable: ${error.message}`); }
  }
  const built = await cachedBuild({
    receiptFile: path.join(frontendRoot, 'node_modules/.cache/oaw/frontend.json'), force,
    inputs: () => frontendBuildFingerprint(frontendRoot, packs),
    outputs: () => treeFingerprint(path.join(frontendRoot, 'dist')),
    build: async () => {
      // Shared preparation already completed; prebuild can install dependencies.
      await runNpm(['--ignore-scripts', 'run', 'build'], frontendRoot, { ...process.env, NODE_ENV: 'production', OAW_ALLOW_PACK_FAILURES: '1' });
      await access(path.join(frontendRoot, 'dist/index.html'));
    },
  });
  console.log(`[Frontend] ${built ? 'Built' : 'Reused'} (${((performance.now() - started) / 1000).toFixed(2)}s including preparation)`);
  return built;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildSourceFrontend({ force: process.argv.includes('--force') });
}
