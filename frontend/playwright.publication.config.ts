import { defineConfig } from '@playwright/test';
import path from 'node:path';

const repository = path.resolve(import.meta.dirname, '..');
const python = process.env.OAW_TEST_PYTHON ?? path.join(repository, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export default defineConfig({
  testDir: './e2e', testMatch: 'workspace.publication.spec.ts', workers: 1, timeout: 60_000,
  outputDir: path.join(repository, '.tmp', 'publication-playwright'),
  use: { baseURL: 'http://127.0.0.1:5184', channel: process.env.PLAYWRIGHT_CHANNEL ?? (process.platform === 'win32' ? 'msedge' : undefined), headless: true, viewport: { width: 1600, height: 1000 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: `"${python}" -m backend.tests.research_deployment_app`, cwd: repository, url: 'http://127.0.0.1:5184/api/deployment', timeout: 90_000, reuseExistingServer: false },
});
