// The Pack owns its tests; reuse the repository's React/SDK test environment.
import { fileURLToPath } from 'node:url';
import hostConfig from '../../frontend/vite.config';

const frontend = (path: string) => fileURLToPath(new URL(`../../frontend/${path}`, import.meta.url));
export default {
  ...hostConfig,
  root: frontend(''),
  resolve: {...hostConfig.resolve, alias: {
    ...hostConfig.resolve?.alias,
    'vitest': frontend('node_modules/vitest/dist/index.js'),
    '@testing-library/react': frontend('node_modules/@testing-library/react/dist/index.js'),
  }},
  test: {
    ...hostConfig.test,
    include: ['../plugins/visualization/tests/**/*.test.tsx'],
    setupFiles: [frontend('src/test/setup.ts')],
  },
};
