// Browser tests for the Controllarr panel. The webServer is tests/harness.py:
// the panel from this checkout, wired to tests/fake_stack.py — no real service is ever touched.
// Deterministic by construction: one worker, no retries, a hard stop after five failures.
import { defineConfig, devices } from '@playwright/test';

const PORT = process.env.MC_E2E_PORT || '3999';
const BASE = `http://127.0.0.1:${PORT}`;
// An image that preinstalls Chromium (Claude Code on the web) may carry a build this Playwright
// version does not expect. PW_CHROMIUM_PATH points at that binary; unset locally, so no effect.
const CHROMIUM = process.env.PW_CHROMIUM_PATH || '';

export default defineConfig({
  testDir: 'tests/e2e',
  testMatch: /.*\.spec\.mjs$/,
  timeout: 20_000,
  expect: { timeout: 6_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  maxFailures: 5,
  globalTimeout: 8 * 60_000,
  forbidOnly: !!process.env.CI,
  reporter: [['line'], ['json', { outputFile: 'test-results/e2e.json' }]],
  outputDir: 'test-results/artifacts',
  use: {
    baseURL: BASE, headless: true, colorScheme: 'dark', locale: 'en-GB', timezoneId: 'UTC',
    actionTimeout: 6_000, navigationTimeout: 10_000,
    screenshot: 'only-on-failure', trace: 'retain-on-failure', video: 'off',
    ...(CHROMIUM ? { launchOptions: { executablePath: CHROMIUM } } : {}),
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } }, testIgnore: /responsive/ },
    { name: 'phone', use: { ...devices['Pixel 7'], viewport: { width: 360, height: 780 } }, testMatch: /responsive/ },
  ],
  webServer: {
    command: `python3 -I tests/harness.py serve --port ${PORT} --quiet`,
    url: `${BASE}/health`, reuseExistingServer: true, timeout: 60_000, stdout: 'ignore', stderr: 'pipe',
  },
});
