import { fileURLToPath } from 'node:url';
import { buildPackFrontendVite } from '@oaw/plugin-api/build-vite';

await buildPackFrontendVite({ root: fileURLToPath(new URL('.', import.meta.url)) });
