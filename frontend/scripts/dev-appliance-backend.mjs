import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { delimiter, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { seedDevCustomModels } from "./dev-appliance-model-seed.mjs";

const legacyEnvironmentPrefix = "OCTO" + "PUS_";
for (const [name, value] of Object.entries(process.env)) {
  if (name.startsWith(legacyEnvironmentPrefix) && value !== undefined) {
    process.env[`ECHO_${name.slice(legacyEnvironmentPrefix.length)}`] ??= value;
  }
}

const scriptDirectory = fileURLToPath(new URL(".", import.meta.url));
const osRoot = resolve(scriptDirectory, "../..");
const configPath = resolve(
  process.env.ECHO_AGENT_CONFIG || resolve(osRoot, "config.local.yaml"),
);
const dataRoot = resolve(
  process.env.ECHO_DEV_DATA_DIR || resolve(osRoot, "data/echo-appliance-dev"),
);
const nasRoot = resolve(process.env.ECHO_NAS_ROOT || resolve(dataRoot, "nas"));
const sourceCustomModelsPath = resolve(osRoot, "data/custom_models.json");
const targetCustomModelsPath = resolve(dataRoot, "custom_models.json");
const applianceAuthStorePath = resolve(dataRoot, "appliance-auth.json");
const port = process.env.GATEWAY_PORT || "8000";
const frontendPort = process.env.FRONTEND_PORT || "3000";

function persistedApplianceJwtSecret() {
  if (!existsSync(applianceAuthStorePath)) return "";
  try {
    const payload = JSON.parse(readFileSync(applianceAuthStorePath, "utf8"));
    return typeof payload.jwt_secret === "string" ? payload.jwt_secret.trim() : "";
  } catch {
    return "";
  }
}

const localJwtSecret =
  process.env.ECHO_LOCAL_JWT_SECRET ||
  persistedApplianceJwtSecret() ||
  randomBytes(48).toString("base64url");
const moliliJwtSecret =
  process.env.MOLILI_JWT_SECRET || randomBytes(48).toString("base64url");
const codexBundleManifest = resolve(
  osRoot,
  "deploy/appliance/agent-codex/echo-codex-bundle.json",
);
const packagedCodexVersion =
  process.env.ECHO_PACKAGED_CODEX_VERSION ||
  JSON.parse(readFileSync(codexBundleManifest, "utf8")).version;

// Agent and OS now share one frontend. The CSRF boundary only needs the
// current Vite origin; the historical 3001 sibling UI is deliberately absent.
const trustedOrigins = [
  ...(process.env.ECHO_APPLIANCE_TRUSTED_ORIGINS || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
  `http://localhost:${frontendPort}`,
  `http://127.0.0.1:${frontendPort}`,
]
  .filter((value, index, origins) => origins.indexOf(value) === index)
  .join(",");

if (!/^\d{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
  throw new Error(`GATEWAY_PORT must be a valid TCP port, received ${port}`);
}
if (
  !/^\d{1,5}$/.test(frontendPort) ||
  Number(frontendPort) < 1 ||
  Number(frontendPort) > 65535
) {
  throw new Error(
    `FRONTEND_PORT must be a valid TCP port, received ${frontendPort}`,
  );
}
if (!existsSync(configPath)) {
  throw new Error(`Embedded Agent development config not found: ${configPath}`);
}

const pythonCandidates = [
  process.env.ECHO_AGENT_PYTHON,
  resolve(osRoot, ".venv/bin/python"),
  resolve(osRoot, ".venv/Scripts/python.exe"),
].filter(Boolean);
const python = pythonCandidates.find((candidate) => existsSync(candidate));
if (!python) {
  throw new Error(
    `Embedded Agent Python not found under ${osRoot}; set ECHO_AGENT_PYTHON`,
  );
}

mkdirSync(dataRoot, { recursive: true, mode: 0o700 });
mkdirSync(nasRoot, { recursive: true, mode: 0o700 });
if (
  seedDevCustomModels({
    sourcePath: sourceCustomModelsPath,
    targetPath: targetCustomModelsPath,
  })
) {
  console.info(
    `[echo] Initialized development custom models from ${sourceCustomModelsPath}`,
  );
}

const pythonPath = [osRoot, process.env.PYTHONPATH]
  .filter(Boolean)
  .join(delimiter);
const child = spawn(
  python,
  [
    "-m",
    "runtime",
    "serve",
    "--config",
    configPath,
    "--host",
    "127.0.0.1",
    "--port",
    port,
  ],
  {
    cwd: osRoot,
    env: {
      ...process.env,
      PYTHONPATH: pythonPath,
      ECHO_APPLIANCE: "1",
      // The local Vite workflow deliberately keeps the admin login passwordless.
      // Production entrypoints never set this development-only override.
      ECHO_APPLIANCE_DEV_PASSWORDLESS:
        process.env.ECHO_APPLIANCE_DEV_PASSWORDLESS || "1",
      ECHO_APP_EXTENSIONS: "appliance.extension",
      ECHO_APPLIANCE_TRUSTED_ORIGINS: trustedOrigins,
      // Local development gets per-process secrets when the operator has not
      // supplied persistent ones. They are never printed or written to disk.
      ECHO_LOCAL_JWT_SECRET: localJwtSecret,
      MOLILI_JWT_SECRET: moliliJwtSecret,
      ECHO_PACKAGED_CODEX_VERSION: packagedCodexVersion,
      ECHO_DATA_DIR: dataRoot,
      ECHO_NAS_ROOT: nasRoot,
    },
    stdio: "inherit",
  },
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (!child.killed) child.kill(signal);
  });
}

child.on("error", (error) => {
  console.error(`Unable to start Echo appliance backend: ${error.message}`);
  process.exitCode = 1;
});
child.on("exit", (code, signal) => {
  if (signal) {
    process.exitCode = 0;
    return;
  }
  process.exitCode = code ?? 1;
});
