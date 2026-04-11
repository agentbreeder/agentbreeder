import { defineConfig } from "@playwright/test";

// Used by the Docker E2E suite. The full stack must already be running.
// Set PLAYWRIGHT_DOCKER_BASE_URL to override (default: http://localhost:3001).
export default defineConfig({
  testDir: "./tests/e2e-docker",
  timeout: 60_000,   // Docker cold-start responses can be slower
  retries: 1,        // One retry for transient Docker networking hiccups
  use: {
    baseURL: process.env.PLAYWRIGHT_DOCKER_BASE_URL ?? "http://localhost:3001",
    headless: true,
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  // No webServer — the stack is managed by docker-compose externally
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
