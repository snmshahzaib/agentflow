import { defineConfig, devices } from '@playwright/test'

/**
 * Documentation screenshots, not tests.
 *
 * A separate config because every project in a config runs by default: as a
 * project in `playwright.config.ts` this would regenerate images on every
 * test run and in CI. Invoked by `scripts/capture-screenshots.sh`.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: /\.capture\.ts$/,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    viewport: { width: 1280, height: 900 },
    reducedMotion: 'reduce',
  },
})
