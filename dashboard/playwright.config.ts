import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  retries: 0,
  expect: {
    // CI Ubuntu runners are slower; auth+data render cycles can exceed 5s default.
    timeout: 15_000,
  },
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL ?? "http://localhost:3001",
    headless: true,
    screenshot: "only-on-failure",
  },
  // CI: serve the pre-built bundle (fast static files, no per-request compilation).
  // Local: use the dev server (HMR, instant feedback).
  webServer: {
    command: process.env.CI ? "npm run build && npm run preview" : "npm run dev",
    port: 3001,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
