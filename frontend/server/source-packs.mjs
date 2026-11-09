import { createReadStream } from 'node:fs';
import { cp, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { buildSourcePack, sourcePacks, reportPackFailure } from '../scripts/source-packs.mjs';

const types = { '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.wasm': 'application/wasm', '.svg': 'image/svg+xml', '.png': 'image/png', '.gif': 'image/gif', '.jpg': 'image/jpeg', '.woff': 'font/woff', '.woff2': 'font/woff2' };

/** Source development serves the exact independent artifact used by releases.
 * A source edit rebuilds its Pack, then reloads the host so module and stylesheet
 * identities are refreshed together. Failed builds are shown by Vite's overlay.
 */
export function sourcePackArtifacts({ discover = sourcePacks, buildPack = buildSourcePack } = {}) {
  let packs = [], server, output, artifactsReady = false, chain = Promise.resolve();
  const failed = new Set();
  const timers = new Map();
  const buildAll = async () => {
    artifactsReady = false;
    const strict = !server && process.env.OAW_ALLOW_PACK_FAILURES !== '1';
    packs = await discover({ strict });
    for (const pack of packs) {
      try { await buildPack(pack); failed.delete(pack.owner); }
      catch (error) {
        if (strict) throw error;
        failed.add(pack.owner);
        await reportPackFailure(pack.owner, error);
      }
    }
    artifactsReady = true;
  };
  return {
    name: 'oaw-source-pack-artifacts',
    configResolved(config) { output = path.resolve(config.root, config.build.outDir, 'assets/packs'); },
    async buildStart() { if (!server) await buildAll(); },
    async configureServer(vite) {
      server = vite;
      await buildAll();
      for (const pack of packs) server.watcher.add(pack.root);
      const changed = file => {
        const normalized = file.replaceAll('\\', '/');
        const pack = packs.find(item => normalized.startsWith(item.root.replaceAll('\\', '/') + '/'));
        if (!pack || /\/(dist|node_modules)\//.test(normalized)) return;
        clearTimeout(timers.get(pack.owner));
        timers.set(pack.owner, setTimeout(() => {
          timers.delete(pack.owner);
          chain = chain.then(() => buildPack(pack)).then(() => {
            failed.delete(pack.owner);
            server.ws.send({ type: 'full-reload' });
          }).catch(error => {
            failed.add(pack.owner);
            server.config.logger.error(error.message);
            server.ws.send({ type: 'error', err: { message: error.message, stack: error.stack ?? '', plugin: 'oaw-source-pack-artifacts' } });
          });
        }, 100));
      };
      server.watcher.on('add', changed).on('change', changed).on('unlink', changed);
      server.httpServer?.once('close', () => { for (const timer of timers.values()) clearTimeout(timer); server.watcher.off('add', changed).off('change', changed).off('unlink', changed); });
      server.middlewares.use(async (request, response, next) => {
        const match = /^\/assets\/packs\/([a-z][a-z0-9._-]*)\/(.+)$/.exec((request.url ?? '').split('?')[0]);
        if (!match) return next();
        const pack = packs.find(item => item.owner === match[1]);
        if (!pack || failed.has(pack.owner)) { response.statusCode = 404; response.end(); return; }
        try {
          const relative = decodeURIComponent(match[2]);
          const root = path.join(pack.root, 'dist');
          const file = path.resolve(root, relative);
          if (!file.startsWith(root + path.sep) || !(await stat(file)).isFile()) throw new Error('Missing asset');
          response.setHeader('Content-Type', types[path.extname(file)] ?? 'application/octet-stream');
          response.setHeader('Cache-Control', 'no-cache');
          createReadStream(file).pipe(response);
        } catch { response.statusCode = 404; response.end(); }
      });
    },
    async closeBundle() {
      if (server || !artifactsReady) return;
      for (const pack of packs) {
        if (failed.has(pack.owner)) continue;
        // The manifest proves that the builder completed before artifacts are
        // copied. Copy the full dist to include worker and lazy asset outputs.
        await readFile(path.join(pack.root, 'dist/frontend-build.json'));
        await cp(path.join(pack.root, 'dist'), path.join(output, pack.owner), { recursive: true });
      }
    },
  };
}
