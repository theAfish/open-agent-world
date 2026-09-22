import { buildPackFrontend } from '@oaw/plugin-api/build';
import { mkdir, copyFile } from 'node:fs/promises';

await mkdir('dist/backend', { recursive: true });
await buildPackFrontend({ entryPoint: 'frontend/index.tsx', outfile: 'dist/frontend/index.js' });
await copyFile('manifest.json', 'dist/manifest.json');
await copyFile('README.md', 'dist/README.md');
