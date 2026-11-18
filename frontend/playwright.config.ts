import { defineConfig, devices } from '@playwright/test'

/**
 * Browser end-to-end tests.
 *
 * These run against the **real stack** in its offline profile: a real API, a
 * real worker, real Postgres and Redis, with the deterministic stub model
 * adapter and the committed fixture corpus. That combination is the point —
 * the journey is genuinely executed rather than mocked, and it costs nothing
 * and needs no credentials, so it can run in CI on every push.
 *
 * Bring the stack up with `scripts/e2e-stack.sh up` (or let
 * `scripts/e2e-test.sh` do both).
 *
 * Specs that need a response the real backend will not readily produce — a
 * dead API, a 429, hostile report content — intercept the network instead,
 * and say so where they do it.
 */
const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:8080'

export default defineConfig({
  testDir: './e2e',
  // The capture file is a documentation tool that writes images, not a test.
  // It has its own config (playwright.screenshots.config.ts) because every
  // project in a config runs by default -- making it a project here would put
  // image generation into every test run.
  testIgnore: /\.capture\.ts$/,
  // The journey spec drives a real five-node workflow and polls for it. The
  // stub adapter makes each node fast, but the whole run is still seconds.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  // Serial by default: these share one backend and one session cookie per
  // worker, and a parallel run would interleave tasks in the shared list.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // Everything is keyboard- and screen-reader-relevant; a reduced-motion
    // run also removes spinner animation flake from screenshots.
    reducedMotion: 'reduce',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 860 } },
      testMatch: /\.spec\.ts$/,
    },
    {
      // Firefox runs the critical journey only, not the whole suite.
      //
      // The journey is the spec where an engine difference would actually
      // matter -- form submission, polling, a real download, the rendered
      // report. The rest is either engine-independent by construction
      // (axe-core's rules, our own ARIA attributes) or measured against
      // layout rules that Gecko and Blink agree on. Running all 33 specs
      // twice would roughly double the slowest CI job to re-prove that.
      name: 'firefox',
      use: { ...devices['Desktop Firefox'], viewport: { width: 1280, height: 860 } },
      testMatch: /journey\.spec\.ts/,
    },
  ],
})
