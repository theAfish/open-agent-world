import { fileURLToPath } from 'node:url';
import hostConfig from '../../frontend/vite.config';

const frontend = (path: string) => fileURLToPath(new URL(`../../frontend/${path}`, import.meta.url));
export default async (env: Parameters<typeof hostConfig>[0]) => {
  const base = await hostConfig(env);
  return { ...base, root: frontend(''), resolve: { ...base.resolve, alias: [
    ...(base.resolve?.alias as {find:string;replacement:string}[] ?? []),
    { find: 'vitest', replacement: frontend('node_modules/vitest/dist/index.js') },
    { find: '@testing-library/react', replacement: frontend('node_modules/@testing-library/react/dist/index.js') },
  ] }, test: { ...base.test, include: ['../plugins/time/tests/**/*.test.tsx'], setupFiles: [frontend('src/test/setup.ts')] } };
};
