import { defineConfig } from "@playwright/test";

/**
 * Playwright E2E config for the Echo desktop (Electron) shell.
 *
 * Unlike the browser lane (playwright.config.ts) this does NOT start a Vite
 * dev server: the Electron app is launched unpackaged with ``--smoke-test``,
 * which loads the already-built ``dist/`` so the shell + preload bridge +
 * workbench boot path is exercised end to end.
 *
 * Usage:
 *   pnpm build                 # once — dist/index.html must exist
 *   pnpm exec playwright test -c playwright.electron.config.ts
 */
export default defineConfig({
  testDir: "./e2e/electron",
  testMatch: "desktop-smoke.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: "list",
  use: {
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "electron",
      testMatch: /desktop-smoke\.spec\.ts/,
    },
  ],
});
