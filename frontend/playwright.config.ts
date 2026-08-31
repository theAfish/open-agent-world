import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 7_000 },
  reporter: [["list"], ["./e2e/completion-reporter.ts"]],
  use: {
    baseURL: "http://127.0.0.1:5177",
    channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome",
    headless: true,
    viewport: { width: 1280, height: 800 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
