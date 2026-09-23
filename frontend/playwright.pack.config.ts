import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e', testMatch: 'pack-host.spec.ts', workers: 1,
  outputDir: process.env.OAW_PACK_E2E_DATA_ROOT ? `${process.env.OAW_PACK_E2E_DATA_ROOT}/playwright` : 'test-results/packs',
  timeout: 180000, expect: { timeout: 10000 }, reporter: 'list',
  use: { baseURL: process.env.OAW_E2E_BASE_URL, channel: process.env.PLAYWRIGHT_CHANNEL ?? 'chrome',
    headless: true, actionTimeout: 15000, viewport: { width: 1500, height: 1000 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
});
