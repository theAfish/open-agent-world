import { checkProject, frontendRoot, preparePack, sourcePacks } from './source-packs.mjs';
import { buildPackSdk } from './build-pack-sdk.mjs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Shared by npm dev and both launchers. No installation is permitted here.
export async function prepareDev() {
  await checkProject(frontendRoot);
  for (const pack of await sourcePacks()) await preparePack(pack, { install: false });
  await buildPackSdk();
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await prepareDev();
