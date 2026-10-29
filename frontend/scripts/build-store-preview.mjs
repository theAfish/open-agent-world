import { build } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const root = fileURLToPath(new URL('../', import.meta.url));
const outDir = fileURLToPath(new URL('../../.tmp/store-preview/', import.meta.url));
const sources = new Map();
await build({
  configFile: false, root, plugins: [react(), {
    name: 'preview-source-receipt',
    async transform(_code, id) {
      if (id.replaceAll('\\', '/').startsWith(root.replaceAll('\\', '/') + 'src/')) {
        sources.set(id.slice(root.length).replaceAll('\\', '/'), createHash('sha256').update(await readFile(id)).digest('hex'));
      }
    },
  }],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  resolve: { alias: [{ find: /^(?:\.\.\/)+i18n$/, replacement: root + 'src/web/preview-i18n.ts' }] },
  build: { outDir, emptyOutDir: true, target: 'es2022', minify: 'esbuild',
    lib: { entry: root + 'src/web/store-preview.tsx', formats: ['es'], fileName: () => 'pack-preview.js', cssFileName: 'pack-preview' },
  },
});
await writeFile(outDir + 'source.json', JSON.stringify({
  source: 'open-agent-world/frontend', command: 'npm run build:store-preview',
  files: Object.fromEntries([...sources].sort(([a], [b]) => a.localeCompare(b))),
}, null, 2) + '\n');
const licenses = await Promise.all(['react', 'react-dom', 'scheduler', 'three', 'zustand', 'lucide-react'].map(async name =>
  `${name}\n${await readFile(root + 'node_modules/' + name + '/LICENSE', 'utf8')}`));
await writeFile(outDir + 'LICENSES.txt', licenses.join('\n\n'));
