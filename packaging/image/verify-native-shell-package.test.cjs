"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const asar = require(
  path.resolve(
    __dirname,
    "..",
    "..",
    "frontend",
    "node_modules",
    "@electron",
    "asar",
  ),
);
const {
  verifyNativeShellPackage,
} = require("./verify-native-shell-package.cjs");

const REPO_ROOT = path.resolve(__dirname, "..", "..");

function fakeElf(machine = 62) {
  const header = Buffer.alloc(64);
  Buffer.from([0x7f, 0x45, 0x4c, 0x46]).copy(header);
  header[4] = 2;
  header[5] = 1;
  header.writeUInt16LE(machine, 18);
  return header;
}

async function fixture({
  staleMain = false,
  testFile = false,
  nativeAddon = false,
} = {}) {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "echo-native-shell-test-"),
  );
  const packageRoot = path.join(root, "linux-unpacked");
  const resources = path.join(packageRoot, "resources");
  const asarSource = path.join(root, "asar-source");
  fs.mkdirSync(path.join(resources, "liquid-glass"), { recursive: true });
  fs.mkdirSync(path.join(asarSource, "dist"), { recursive: true });
  fs.mkdirSync(path.join(asarSource, "electron"), { recursive: true });
  fs.writeFileSync(path.join(packageRoot, "echo-os-desktop"), fakeElf());
  fs.chmodSync(path.join(packageRoot, "echo-os-desktop"), 0o755);
  fs.writeFileSync(
    path.join(resources, "liquid-glass", "NOTICE.txt"),
    "notice\n",
  );
  fs.writeFileSync(
    path.join(resources, "liquid-glass", "wallpaper-day2.jpg"),
    "jpeg\n",
  );
  fs.writeFileSync(
    path.join(resources, "native-shell-profile.json"),
    '{ "schema": "echo.native_shell_profile.v1" }\n',
  );
  fs.writeFileSync(
    path.join(asarSource, "dist", "index.html"),
    "<!doctype html>\n",
  );
  for (const name of ["main.cjs", "desktop-updater.cjs", "shell-profile.cjs"]) {
    const source = fs.readFileSync(
      path.join(REPO_ROOT, "frontend", "electron", name),
    );
    fs.writeFileSync(
      path.join(asarSource, "electron", name),
      staleMain && name === "main.cjs" ? Buffer.from("stale\n") : source,
    );
  }
  if (testFile) {
    fs.writeFileSync(path.join(asarSource, "electron", "leak.test.cjs"), "\n");
  }
  if (nativeAddon) {
    fs.mkdirSync(path.join(asarSource, "native"), { recursive: true });
    fs.writeFileSync(path.join(asarSource, "native", "host.node"), "binary\n");
  }
  fs.writeFileSync(
    path.join(asarSource, "package.json"),
    JSON.stringify({
      name: "echo-frontend",
      version: "0.2.0",
      main: "electron/main.cjs",
    }),
  );
  await asar.createPackage(asarSource, path.join(resources, "app.asar"));
  return { packageRoot, root };
}

async function rejectsMutation(options, mutate) {
  const created = await fixture(options);
  try {
    mutate?.(created.packageRoot);
    assert.throws(
      () => verifyNativeShellPackage(created.packageRoot),
      /verification failed/,
    );
  } finally {
    fs.rmSync(created.root, { recursive: true, force: true });
  }
}

async function run() {
  const valid = await fixture();
  try {
    const result = verifyNativeShellPackage(valid.packageRoot);
    assert.equal(result.schema, "echo.native_shell_package.v1");
    assert.equal(result.architecture, "x86_64");
  } finally {
    fs.rmSync(valid.root, { recursive: true, force: true });
  }

  await rejectsMutation({}, (packageRoot) =>
    fs.writeFileSync(
      path.join(packageRoot, "resources", "app-update.yml"),
      "provider: github\n",
    ),
  );
  await rejectsMutation({ staleMain: true });
  await rejectsMutation({ testFile: true });
  await rejectsMutation({ nativeAddon: true });
  await rejectsMutation({}, (packageRoot) =>
    fs.writeFileSync(path.join(packageRoot, "echo-os-desktop"), fakeElf(183)),
  );
  await rejectsMutation({}, (packageRoot) =>
    fs.writeFileSync(
      path.join(packageRoot, "resources", "native-shell-profile.json"),
      '{ "schema": "wrong" }\n',
    ),
  );
  console.log("Echo native shell package verifier tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
