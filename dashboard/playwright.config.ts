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
  // In CI the server is already running (built image). Locally, start dev server.
  webServer: process.env.CI
    ? undefined
    : {
        command: "npm run dev",
        port: 3001,
        reuseExistingServer: true,
        timeout: 15_000,
      },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
