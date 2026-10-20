import { fileURLToPath } from 'node:url';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { buildPackFrontendVite } from '@oaw/plugin-api/build-vite';

const measure = fileURLToPath(new URL('./threlteMeasure.svelte.ts', import.meta.url)).replaceAll('\\', '/');
await buildPackFrontendVite({ root: fileURLToPath(new URL('.', import.meta.url)), plugins: [{
  name: 'structure-viewer-canvas-dimensions', enforce: 'pre',
  resolveId(source, importer) {
    if (source.endsWith('/useMeasure.svelte.js') && importer?.replaceAll('\\', '/').includes('/@threlte/core/')) return measure;
  },
}, svelte({ configFile: false })] });
