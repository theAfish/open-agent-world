import { defineConfig } from '@playwright/test';
import path from 'node:path';

const repository = path.resolve(import.meta.dirname, '..');
const python = path.join(repository, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export default defineConfig({
  testDir: './e2e', testMatch: 'deployment.runtime.spec.ts', workers: 1, timeout: 45_000,
  outputDir: path.join(repository, '.tmp', 'deployment-playwright'),
  use: { baseURL: 'http://127.0.0.1:5183', channel: process.env.PLAYWRIGHT_CHANNEL ?? (process.platform === 'win32' ? 'msedge' : undefined), headless: true, viewport: { width: 1400, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: `"${python}" -m backend.tests.deployment_app`, cwd: repository, url: 'http://127.0.0.1:5183/api/deployment', timeout: 60_000, reuseExistingServer: false },
});
