import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { delimiter, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { seedDevCustomModels } from "./dev-appliance-model-seed.mjs";
import { resolveDevConfiguration } from "./dev-appliance-config.mjs";

const legacyEnvironmentPrefix = "OCTO" + "PUS_";
for (const [name, value] of Object.entries(process.env)) {
  if (name.startsWith(legacyEnvironmentPrefix) && value !== undefined) {
    process.env[`ECHO_${name.slice(legacyEnvironmentPrefix.length)}`] ??= value;
  }
}

const scriptDirectory = fileURLToPath(new URL(".", import.meta.url));
const osRoot = resolve(scriptDirectory, "../..");
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
    return typeof payload.jwt_secret === "string"
      ? payload.jwt_secret.trim()
      : "";
  } catch {
    return "";
  }
}

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
const { configPath, python, packagedCodexVersion, codexExecutable } =
  resolveDevConfiguration(osRoot);

if (process.argv.includes("--check")) {
  console.info(
    JSON.stringify({
      check: "development-configuration-only",
      configPath,
      python,
      packagedCodexVersion: packagedCodexVersion || null,
      codexExecutable: codexExecutable || null,
      host: "127.0.0.1",
      port: Number(port),
      frontendPort: Number(frontendPort),
    }),
  );
  process.exit(0);
}

const localJwtSecret =
  process.env.ECHO_LOCAL_JWT_SECRET ||
  persistedApplianceJwtSecret() ||
  randomBytes(48).toString("base64url");

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
      // Repository/configuration text is UTF-8 even on a Windows GBK locale.
      PYTHONUTF8: "1",
      ...(codexExecutable ? { ECHO_CODEX_EXECUTABLE: codexExecutable } : {}),
      ECHO_APPLIANCE: "1",
      // Do not serve a partial Agent when the device extension cannot acquire
      // its state directory (for example, while another dev instance owns it).
      ECHO_REQUIRED_APP_EXTENSIONS: "1",
      // The local Vite workflow deliberately keeps the admin login passwordless.
      // Production entrypoints never set this development-only override.
      ECHO_APPLIANCE_DEV_PASSWORDLESS:
        process.env.ECHO_APPLIANCE_DEV_PASSWORDLESS || "1",
      ECHO_APP_EXTENSIONS: "appliance.extension",
      ECHO_APPLIANCE_TRUSTED_ORIGINS: trustedOrigins,
      // Local development gets per-process secrets when the operator has not
      // supplied persistent ones. They are never printed or written to disk.
      ECHO_LOCAL_JWT_SECRET: localJwtSecret,
      // A fresh source checkout has no release bundle. Let the runtime resolve
      // its development package pin without claiming a verified bundled binary.
      ...(packagedCodexVersion
        ? { ECHO_PACKAGED_CODEX_VERSION: packagedCodexVersion }
        : {}),
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
