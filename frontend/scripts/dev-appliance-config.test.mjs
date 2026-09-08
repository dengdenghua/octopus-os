import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test, { after } from "node:test";

import { resolveDevConfiguration } from "./dev-appliance-config.mjs";

// Keep isolated fixtures in the OS temporary directory; no workspace or user
// state is modified and no backend, model or release tool is executed.
const fixtureRoots = [];
after(() => {
  for (const root of fixtureRoots) {
    const target = resolve(root);
    assert.equal(dirname(target), resolve(tmpdir()));
    assert.ok(basename(target).startsWith("echo-dev-config-"));
    rmSync(target, { recursive: true, force: true });
  }
});

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "echo-dev-config-"));
  fixtureRoots.push(root);
  writeFileSync(join(root, "config.example.yaml"), "preset: personal\n");
  mkdirSync(join(root, ".venv/Scripts"), { recursive: true });
  writeFileSync(join(root, ".venv/Scripts/python.exe"), "fixture");
  return root;
}

test("fresh checkout selects example config without a release bundle", () => {
  const root = fixture();
  const result = resolveDevConfiguration(root, {});
  assert.equal(result.configPath, join(root, "config.example.yaml"));
  assert.equal(result.python, join(root, ".venv/Scripts/python.exe"));
  assert.equal(result.packagedCodexVersion, undefined);
  assert.equal(existsSync(join(root, "data")), false);
});

test("an explicitly selected executable bypasses the optional local installation", () => {
  const root = fixture();
  const runtime = join(root, "data/echo-appliance-dev/codex-runtime");
  mkdirSync(runtime, { recursive: true });
  writeFileSync(
    join(runtime, "package.json"),
    '{"dependencies":{"@openai/codex":"1.2.3"}}',
  );
  const result = resolveDevConfiguration(root, {
    ECHO_CODEX_EXECUTABLE: "explicit-codex",
  });
  assert.equal(result.codexExecutable, undefined);
});

test("local runtime pin and installed executable are resolved together", () => {
  const root = fixture();
  const runtime = join(root, "data/echo-appliance-dev/codex-runtime");
  const wrapper = join(runtime, "node_modules/@openai/codex");
  mkdirSync(wrapper, { recursive: true });
  writeFileSync(
    join(runtime, "package.json"),
    '{"dependencies":{"@openai/codex":"1.2.3"}}',
  );
  writeFileSync(join(wrapper, "package.json"), '{"version":"1.2.2"}');
  assert.throws(() => resolveDevConfiguration(root, {}), /installation pin/);
  writeFileSync(join(wrapper, "package.json"), '{"version":"1.2.3"}');
  const targets = {
    "win32-x64": "x86_64-pc-windows-msvc",
    "win32-arm64": "aarch64-pc-windows-msvc",
    "linux-x64": "x86_64-unknown-linux-musl",
    "linux-arm64": "aarch64-unknown-linux-musl",
    "darwin-x64": "x86_64-apple-darwin",
    "darwin-arm64": "aarch64-apple-darwin",
  };
  const platform = `${process.platform}-${process.arch}`;
  const pkg = join(runtime, `node_modules/@openai/codex-${platform}`);
  const bin = join(pkg, "vendor", targets[platform], "bin");
  mkdirSync(bin, { recursive: true });
  writeFileSync(join(pkg, "package.json"), "{}");
  assert.throws(
    () => resolveDevConfiguration(root, {}),
    /executable is missing/,
  );
  const executable = join(
    bin,
    process.platform === "win32" ? "codex.exe" : "codex",
  );
  writeFileSync(executable, "fixture");
  const result = resolveDevConfiguration(root, {});
  assert.equal(result.codexExecutable, executable);
  assert.equal(result.packagedCodexVersion, "1.2.3");
});

test("local config wins and explicit config is never silently replaced", () => {
  const root = fixture();
  const local = join(root, "config.local.yaml");
  writeFileSync(local, "name: custom\n");
  assert.equal(resolveDevConfiguration(root, {}).configPath, local);
  assert.throws(
    () =>
      resolveDevConfiguration(root, {
        ECHO_AGENT_CONFIG: join(root, "missing.yaml"),
      }),
    /config not found/,
  );
  assert.equal(readFileSync(local, "utf8"), "name: custom\n");
});

test("invalid explicit Python does not fall back to another environment", () => {
  const root = fixture();
  for (const path of [join(root, "missing.exe"), join(root, ".venv")]) {
    assert.throws(
      () => resolveDevConfiguration(root, { ECHO_AGENT_PYTHON: path }),
      /Python not found/,
    );
  }
});

test("Linux venv is supported and config directories are rejected", () => {
  const root = fixture();
  mkdirSync(join(root, ".venv/bin"));
  writeFileSync(join(root, ".venv/bin/python"), "fixture");
  assert.equal(
    resolveDevConfiguration(root, {}).python,
    join(root, ".venv/bin/python"),
  );
  mkdirSync(join(root, "config.local.yaml"));
  assert.throws(() => resolveDevConfiguration(root, {}), /config not found/);
});

test("present bundle metadata is validated instead of silently ignored", () => {
  const root = fixture();
  const directory = join(root, "deploy/appliance/agent-codex");
  mkdirSync(directory, { recursive: true });
  const manifest = join(directory, "echo-codex-bundle.json");
  for (const content of [
    "{broken",
    "null",
    "{}",
    '{"version":true}',
    '{"version":"latest"}',
  ]) {
    writeFileSync(manifest, content);
    assert.throws(() => resolveDevConfiguration(root, {}), /Codex/);
  }
  writeFileSync(manifest, '{"version":"1.2.3"}');
  assert.equal(resolveDevConfiguration(root, {}).packagedCodexVersion, "1.2.3");
  assert.equal(
    resolveDevConfiguration(root, { ECHO_PACKAGED_CODEX_VERSION: "2.3.4" })
      .packagedCodexVersion,
    "2.3.4",
  );
});

test("launcher preflight is read-only and does not expose credentials", () => {
  const root = fixture();
  const data = join(root, "untouched-data");
  const env = Object.fromEntries(
    Object.entries(process.env).filter(
      ([key]) => !key.startsWith("ECHO_") && !key.startsWith("OCTO" + "PUS_"),
    ),
  );
  const launcher = fileURLToPath(
    new URL("./dev-appliance-backend.mjs", import.meta.url),
  );
  const result = spawnSync(process.execPath, [launcher, "--check"], {
    encoding: "utf8",
    env: {
      ...env,
      ECHO_AGENT_CONFIG: join(root, "config.example.yaml"),
      ECHO_AGENT_PYTHON: process.execPath,
      ECHO_DEV_DATA_DIR: data,
      ECHO_LOCAL_JWT_SECRET: "must-not-appear",
      GATEWAY_PORT: "8100",
      FRONTEND_PORT: "3100",
    },
  });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.check, "development-configuration-only");
  assert.equal(report.port, 8100);
  assert.equal(report.frontendPort, 3100);
  assert.equal(existsSync(data), false);
  assert.equal(
    (result.stdout + result.stderr).includes("must-not-appear"),
    false,
  );
});
