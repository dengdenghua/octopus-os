#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const asar = require(
  path.join(REPO_ROOT, "frontend", "node_modules", "@electron", "asar"),
);

const EXPECTED_RESOURCE_ENTRIES = [
  "app.asar",
  "liquid-glass",
  "native-shell-profile.json",
];
const EXPECTED_GLASS_ENTRIES = ["NOTICE.txt", "wallpaper-day2.jpg"];
const EXPECTED_PROFILE_CONTENT =
  '{ "schema": "echo.native_shell_profile.v1" }\n';
const FORBIDDEN_RESOURCE_ENTRIES = new Set([
  "app-update.yml",
  "backend",
  "codex",
  "config.desktop.yaml",
  "agents",
  "prompts",
  "protocols",
  "resources",
  "extensions",
  "skills.lock.json",
]);

function fail(message) {
  throw new Error(`native shell package verification failed: ${message}`);
}

function regularFile(filePath, label) {
  let info;
  try {
    info = fs.lstatSync(filePath);
  } catch {
    fail(`${label} is missing: ${filePath}`);
  }
  if (!info.isFile() || info.isSymbolicLink()) {
    fail(`${label} must be a non-symlink regular file: ${filePath}`);
  }
  return info;
}

function regularDirectory(directoryPath, label) {
  let info;
  try {
    info = fs.lstatSync(directoryPath);
  } catch {
    fail(`${label} is missing: ${directoryPath}`);
  }
  if (!info.isDirectory() || info.isSymbolicLink()) {
    fail(`${label} must be a non-symlink directory: ${directoryPath}`);
  }
}

function verifyLinuxX64Elf(executable) {
  const info = regularFile(executable, "native shell executable");
  if (process.platform !== "win32" && (info.mode & 0o111) === 0) {
    fail("native shell executable has no executable mode bit");
  }
  const descriptor = fs.openSync(executable, "r");
  try {
    const header = Buffer.alloc(20);
    if (
      fs.readSync(descriptor, header, 0, header.length, 0) !== header.length
    ) {
      fail("native shell executable has a truncated ELF header");
    }
    if (!header.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46]))) {
      fail("native shell executable is not ELF");
    }
    if (header[4] !== 2 || header[5] !== 1 || header.readUInt16LE(18) !== 62) {
      fail("native shell executable is not little-endian Linux x86-64 ELF");
    }
  } finally {
    fs.closeSync(descriptor);
  }
}

function sortedDirectoryEntries(directoryPath) {
  return fs.readdirSync(directoryPath).sort();
}

function assertExactEntries(actual, expected, label) {
  if (
    actual.length !== expected.length ||
    actual.some((item, index) => item !== expected[index])
  ) {
    fail(
      `${label} changed: expected ${expected.join(",")}; got ${actual.join(",")}`,
    );
  }
}

function verifyAsar(archive, sourceRoot) {
  const info = regularFile(archive, "native shell ASAR");
  if (info.size <= 0 || info.size > 2 * 1024 * 1024 * 1024) {
    fail("native shell ASAR size is outside its bounded contract");
  }
  const entries = asar
    .listPackage(archive)
    .map((entry) => entry.replace(/^\//, ""));
  for (const entry of [
    "dist/index.html",
    "electron/main.cjs",
    "electron/desktop-updater.cjs",
    "electron/shell-profile.cjs",
    "package.json",
  ]) {
    if (!entries.includes(entry)) fail(`ASAR entry is missing: ${entry}`);
  }
  for (const entry of entries) {
    if (
      !entry ||
      entry === "." ||
      entry.startsWith("../") ||
      path.isAbsolute(entry)
    ) {
      fail(`ASAR contains an unsafe path: ${entry}`);
    }
    if (
      /electron\/.*(?:\.test\.cjs|\.node-test\.cjs|\.test\.mjs)$/.test(entry)
    ) {
      fail(`ASAR contains a test file: ${entry}`);
    }
    if (entry.endsWith(".node") || entry.startsWith("native/")) {
      fail(`ASAR contains a host-native addon: ${entry}`);
    }
  }
  for (const relative of [
    "electron/main.cjs",
    "electron/desktop-updater.cjs",
    "electron/shell-profile.cjs",
  ]) {
    const packaged = asar.extractFile(archive, relative);
    const source = fs.readFileSync(path.join(sourceRoot, "frontend", relative));
    if (!packaged.equals(source))
      fail(`ASAR source differs from checkout: ${relative}`);
  }
  const packagedManifest = JSON.parse(
    asar.extractFile(archive, "package.json").toString("utf8"),
  );
  if (
    packagedManifest.name !== "echo-frontend" ||
    packagedManifest.version !== "0.2.0" ||
    packagedManifest.main !== "electron/main.cjs"
  ) {
    fail("packaged application identity changed");
  }
  return entries.length;
}

function verifyNativeShellPackage(packageRoot, sourceRoot = REPO_ROOT) {
  if (!path.isAbsolute(packageRoot)) fail("package root must be absolute");
  regularDirectory(packageRoot, "native shell package root");
  const resources = path.join(packageRoot, "resources");
  const glass = path.join(resources, "liquid-glass");
  regularDirectory(resources, "native shell resources");
  regularDirectory(glass, "native shell visual resources");

  const resourceEntries = sortedDirectoryEntries(resources);
  for (const entry of resourceEntries) {
    if (FORBIDDEN_RESOURCE_ENTRIES.has(entry)) {
      fail(`standalone desktop resource is present: ${entry}`);
    }
  }
  assertExactEntries(
    resourceEntries,
    EXPECTED_RESOURCE_ENTRIES,
    "native shell resource inventory",
  );
  assertExactEntries(
    sortedDirectoryEntries(glass),
    EXPECTED_GLASS_ENTRIES,
    "visual resource inventory",
  );
  for (const entry of EXPECTED_GLASS_ENTRIES) {
    const info = regularFile(
      path.join(glass, entry),
      `visual resource ${entry}`,
    );
    if (info.size <= 0) fail(`visual resource is empty: ${entry}`);
  }
  const profile = path.join(resources, "native-shell-profile.json");
  regularFile(profile, "native shell identity marker");
  if (fs.readFileSync(profile, "utf8") !== EXPECTED_PROFILE_CONTENT) {
    fail("native shell identity marker is invalid");
  }

  verifyLinuxX64Elf(path.join(packageRoot, "echo-os-desktop"));
  const asarEntries = verifyAsar(path.join(resources, "app.asar"), sourceRoot);
  return {
    schema: "echo.native_shell_package.v1",
    architecture: "x86_64",
    asarEntries,
    resources: resourceEntries,
  };
}

if (require.main === module) {
  if (process.argv.length !== 3) {
    console.error(
      "usage: verify-native-shell-package.cjs ABSOLUTE_LINUX_UNPACKED_ROOT",
    );
    process.exit(2);
  }
  try {
    console.log(JSON.stringify(verifyNativeShellPackage(process.argv[2])));
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}

module.exports = { verifyNativeShellPackage };
