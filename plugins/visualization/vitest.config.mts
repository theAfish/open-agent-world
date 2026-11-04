// The Pack owns its tests; reuse the repository's React/SDK test environment.
import { fileURLToPath } from 'node:url';
import hostConfig from '../../frontend/vite.config';

const frontend = (path: string) => fileURLToPath(new URL(`../../frontend/${path}`, import.meta.url));
const baseConfig = await hostConfig({command: 'serve', mode: 'test'});
export default {
  ...baseConfig,
  root: frontend(''),
  resolve: {...baseConfig.resolve, alias: [
    ...(Array.isArray(baseConfig.resolve?.alias) ? baseConfig.resolve.alias : []),
    {find: 'vitest', replacement: frontend('node_modules/vitest/dist/index.js')},
    {find: '@testing-library/react', replacement: frontend('node_modules/@testing-library/react/dist/index.js')},
  ]},
  test: {
    ...baseConfig.test,
    include: ['../plugins/visualization/tests/**/*.test.tsx'],
    setupFiles: [frontend('src/test/setup.ts')],
  },
};
