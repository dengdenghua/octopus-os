import { existsSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { createRequire } from "node:module";

function isFile(path) {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

// Configuration discovery only: never create state, load credentials or start a
// service here. Release provenance remains enforced by production entrypoints.
export function resolveDevConfiguration(osRoot, env = process.env) {
  const localConfig = resolve(osRoot, "config.local.yaml");
  const configPath = env.ECHO_AGENT_CONFIG
    ? resolve(env.ECHO_AGENT_CONFIG)
    : existsSync(localConfig)
      ? localConfig
      : resolve(osRoot, "config.example.yaml");
  if (!isFile(configPath)) {
    throw new Error(
      `Embedded Agent development config not found: ${configPath}; set ECHO_AGENT_CONFIG to a YAML file`,
    );
  }

  const pythonCandidates = env.ECHO_AGENT_PYTHON
    ? [resolve(env.ECHO_AGENT_PYTHON)]
    : [
        resolve(osRoot, ".venv/bin/python"),
        resolve(osRoot, ".venv/Scripts/python.exe"),
      ];
  const python = pythonCandidates.find(isFile);
  if (!python) {
    throw new Error(
      "Embedded Agent Python not found; create .venv with Python 3.11+ and install .[dev,serve,tracing,web,local-auth], or set ECHO_AGENT_PYTHON to its executable path",
    );
  }

  const manifestPath = resolve(
    osRoot,
    "deploy/appliance/agent-codex/echo-codex-bundle.json",
  );
  let packagedCodexVersion = env.ECHO_PACKAGED_CODEX_VERSION?.trim();
  if (!packagedCodexVersion && existsSync(manifestPath)) {
    let manifest;
    try {
      manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    } catch {
      throw new Error(
        "Development Codex bundle manifest is unreadable or invalid JSON",
      );
    }
    packagedCodexVersion = manifest?.version;
    if (
      typeof packagedCodexVersion !== "string" ||
      !packagedCodexVersion.trim()
    ) {
      throw new Error("Development Codex bundle manifest has no version");
    }
  }
  if (
    packagedCodexVersion &&
    !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(packagedCodexVersion)
  ) {
    throw new Error("Development Codex version must be an exact version");
  }

  let codexExecutable;
  const localRuntime = resolve(
    env.ECHO_DEV_DATA_DIR || resolve(osRoot, "data/echo-appliance-dev"),
    "codex-runtime/package.json",
  );
  if (!env.ECHO_CODEX_EXECUTABLE && isFile(localRuntime)) {
    const requireRuntime = createRequire(localRuntime);
    const wrapper = requireRuntime.resolve("@openai/codex/package.json");
    const installedVersion = JSON.parse(readFileSync(wrapper, "utf8")).version;
    const requestedVersion = JSON.parse(readFileSync(localRuntime, "utf8"))
      .dependencies?.["@openai/codex"];
    if (
      installedVersion !== requestedVersion ||
      !/^\d+\.\d+\.\d+$/.test(installedVersion)
    ) {
      throw new Error(
        "Local Codex runtime version does not match its exact installation pin",
      );
    }
    const targets = {
      "win32-x64": "x86_64-pc-windows-msvc",
      "win32-arm64": "aarch64-pc-windows-msvc",
      "linux-x64": "x86_64-unknown-linux-musl",
      "linux-arm64": "aarch64-unknown-linux-musl",
      "darwin-x64": "x86_64-apple-darwin",
      "darwin-arm64": "aarch64-apple-darwin",
    };
    const target = `${process.platform}-${process.arch}`;
    if (!targets[target])
      throw new Error("Unsupported local Codex runtime platform");
    const platformPackage = createRequire(wrapper).resolve(
      `@openai/codex-${target}/package.json`,
    );
    codexExecutable = resolve(
      platformPackage,
      "..",
      "vendor",
      targets[target],
      "bin",
      process.platform === "win32" ? "codex.exe" : "codex",
    );
    if (!isFile(codexExecutable))
      throw new Error("Local Codex runtime executable is missing");
    packagedCodexVersion = installedVersion;
  }
  return {
    configPath,
    python,
    packagedCodexVersion,
    ...(codexExecutable ? { codexExecutable } : {}),
  };
}
