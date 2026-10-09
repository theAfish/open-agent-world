import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

const repository = path.resolve(import.meta.dirname, '..');
const python = process.env.OAW_TEST_PYTHON ?? path.join(repository, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');

export default defineConfig({
  testDir: './e2e', testMatch: 'cross-platform-ui.spec.ts', workers: 1, timeout: 45_000,
  outputDir: path.join(repository, '.tmp', 'ui-playwright'),
  use: {
    baseURL: 'http://127.0.0.1:5184', headless: true,
    viewport: { width: 1440, height: 940 }, screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium', channel: process.env.PLAYWRIGHT_CHANNEL ?? (process.platform === 'win32' ? 'msedge' : undefined) } },
    { name: 'webkit', use: { browserName: 'webkit' } },
  ],
  webServer: {
    command: `"${python}" -m backend.launcher --mode preview --profile ui-${randomUUID()} --port 5184 --strict-port --frontend frontend/dist --no-sandbox`,
    cwd: repository, url: 'http://127.0.0.1:5184/api/health', timeout: 60_000, reuseExistingServer: false,
    stdout: 'ignore', stderr: 'ignore',
    env: { OPEN_AGENT_WORLD_AGENT_RUNTIME: 'mock', OPEN_AGENT_WORLD_PLUGIN_DIRS: '', PYTHONUTF8: '1' },
  },
});
