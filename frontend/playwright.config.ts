import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration for echo-frontend.
 *
 * Expects the backend (FastAPI) on port 8000 and the frontend (Vite) on
 * port 3000. In CI, start both services before running `npx playwright test`.
 * Locally, you can let the `webServer` block below start the frontend for you.
 *
 * Usage:
 *   npx playwright test              # headless
 *   npx playwright test --ui         # interactive UI mode
 *   npx playwright test --headed     # headed browser
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  timeout: 30_000,

  use: {
    baseURL: `http://localhost:${process.env.FRONTEND_PORT || "3000"}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  /* Optionally start the Vite dev server before tests. */
  // webServer: {
  //   command: "npm run dev",
  //   port: 3000,
  //   reuseExistingServer: !process.env.CI,
  //   timeout: 30_000,
  // },
});
