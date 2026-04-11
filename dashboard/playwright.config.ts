import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL ?? "http://localhost:3001",
    headless: true,
    screenshot: "only-on-failure",
  },
  // Always start the dev server; reuseExistingServer skips it if already running.
  webServer: {
    command: "npm run dev",
    port: 3001,
    reuseExistingServer: true,
    timeout: 30_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
