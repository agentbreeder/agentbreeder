import { defineConfig, devices } from '@playwright/test';
import { config } from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

config({ path: path.resolve(__dirname, '.env.e2e') });

export default defineConfig({
  testDir: './tests/e2e-live',
  timeout: 60_000,
  retries: 1,
  workers: 1,
  fullyParallel: false,
  reporter: [
    ['html', { outputFolder: 'playwright-report-live', open: 'never' }],
    ['list'],
  ],
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL ?? 'http://localhost:3001',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  projects: [
    {
      name: 'setup',
      testMatch: /global\.setup\.ts/,
    },
    {
      name: 'live',
      testMatch: /\d{2}-.*\.spec\.ts/,
      dependencies: ['setup'],
      teardown: 'teardown',
    },
    {
      name: 'teardown',
      testMatch: /global\.teardown\.ts/,
    },
  ],
});
