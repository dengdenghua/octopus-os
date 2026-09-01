import {
  defineConfig,
  devices,
  type ReporterDescription,
} from "@playwright/test";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendPort = process.env.FRONTEND_PORT || "13000";
const backendPort = process.env.GATEWAY_PORT || "18000";
const backendHost = process.env.GATEWAY_HOST || "127.0.0.1";
const backendBase = `http://${backendHost}:${backendPort}`;
const configDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(configDir, "..");
const rawE2eStateRoot =
  process.env.ECHO_E2E_STATE_ROOT || "test-results/full-stack-state";
const e2eStateRoot = isAbsolute(rawE2eStateRoot)
  ? resolve(rawE2eStateRoot)
  : resolve(repoRoot, rawE2eStateRoot);
const e2eDataDir = join(e2eStateRoot, "data");
const pythonBin = process.env.PYTHON || "./.venv/bin/python";
const reuseServers = process.env.ECHO_E2E_REUSE_SERVER === "1";
const jsonReportPath = process.env.ECHO_E2E_JSON_REPORT;
const reporter: ReporterDescription | ReporterDescription[] = jsonReportPath
  ? [["list"], ["json", { outputFile: jsonReportPath }]]
  : process.env.CI
    ? "github"
    : "list";
const defaultTestMatch = [
  "full-stack-smoke.spec.ts",
  "chat.spec.ts",
  "regression.spec.ts",
  "workflow-editor.spec.ts",
  "visual-regression.spec.ts",
];
const testMatch =
  process.env.ECHO_E2E_TEST_MATCH?.split(",")
    .map((entry) => entry.trim())
    .filter(Boolean) || defaultTestMatch;
const backendEnv =
  "ECHO_FF_REGENERATION_ENABLED=0 " +
  "ECHO_FF_CAMOUFLAGE_ENABLED=0 " +
  "ECHO_FF_UI_AMBIENT_SUGGESTIONS=0 " +
  `GATEWAY_PORT=${backendPort} ` +
  `ECHO_INTERNAL_GATEWAY_BASE_URL=${backendBase} ` +
  `ECHO_HOME=${e2eStateRoot} ` +
  `ECHO_DATA_DIR=${e2eDataDir}`;

const resolvedTestResultsRoot = resolve(repoRoot, "test-results");
const e2eStateRootRelative = relative(resolvedTestResultsRoot, e2eStateRoot);
const e2eStateRootIsDisposable =
  e2eStateRootRelative &&
  !e2eStateRootRelative.startsWith("..") &&
  e2eStateRootRelative !== ".." &&
  e2eStateRootRelative !== ".";

if (!reuseServers && !e2eStateRootIsDisposable) {
  throw new Error(
    [
      "ECHO_E2E_STATE_ROOT must resolve under repo test-results when",
      "ECHO_E2E_REUSE_SERVER is not enabled.",
      `resolved state root: ${e2eStateRoot}`,
    ].join(" "),
  );
}

// Playwright loads its config in both the runner and worker processes. State
// cleanup must therefore happen in the single backend web-server command, not
// as a module side effect here; otherwise a worker can delete SQLite's parent
// directory after the backend has already opened.
const prepareStateCommand = reuseServers
  ? ""
  : `${backendEnv} node frontend/e2e/prepare-full-stack-state.mjs && `;

/**
 * Full-stack Playwright configuration.
 *
 * Unlike the default config, this starts both halves of the local app:
 * FastAPI backend with the deterministic offline e2e config, then Vite with
 * its /api proxy pointed at that backend. This catches the common failure mode
 * where the frontend is reachable but the backend on :8000 is stale or absent.
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch,
  // Visual-regression baselines are shared across platforms (CI runs on
  // Linux, dev on macOS): drop the default -{project}-{platform} suffix so
  // both lanes compare against the same committed PNGs. Font rasterisation
  // noise is absorbed by the maxDiffPixelRatio in visual-regression.spec.ts.
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}{ext}",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter,
  timeout: 45_000,

  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: [
    {
      command: `${prepareStateCommand}${backendEnv} ${pythonBin} -m runtime serve --config config.e2e.yaml --host ${backendHost} --port ${backendPort}`,
      url: `${backendBase}/api/status`,
      cwd: repoRoot,
      reuseExistingServer: reuseServers,
      timeout: 120_000,
    },
    {
      command: `cross-env GATEWAY_PORT=${backendPort} ECHO_INTERNAL_GATEWAY_BASE_URL=${backendBase} pnpm exec vite --host 0.0.0.0 --port ${frontendPort} --strictPort`,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: reuseServers,
      timeout: 90_000,
    },
  ],
});
