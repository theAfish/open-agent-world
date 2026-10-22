import { buildPackFrontend } from '@oaw/plugin-api/build';

await buildPackFrontend({ entryPoint: 'frontend/index.tsx', outfile: 'dist/frontend/index.js' });
