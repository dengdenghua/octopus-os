"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  NATIVE_SHELL_EXECUTABLE,
  NATIVE_SHELL_PROFILE,
  NATIVE_SHELL_PROFILE_CONTENT,
  isPackagedNativeShell,
} = require("./shell-profile.cjs");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-shell-profile-"));
try {
  assert.equal(NATIVE_SHELL_EXECUTABLE, "echo-os-desktop");
  assert.equal(NATIVE_SHELL_PROFILE, "native-shell-profile.json");

  assert.equal(
    isPackagedNativeShell({
      isPackaged: true,
      platform: "linux",
      execPath: "/opt/Echo/echo",
      resourcesPath: root,
    }),
    false,
  );
  assert.throws(
    () =>
      isPackagedNativeShell({
        isPackaged: true,
        platform: "linux",
        execPath: `/opt/Echo/${NATIVE_SHELL_EXECUTABLE}`,
        resourcesPath: root,
      }),
    /identity marker is missing/,
  );

  const profile = path.join(root, NATIVE_SHELL_PROFILE);
  fs.writeFileSync(profile, NATIVE_SHELL_PROFILE_CONTENT);
  assert.equal(
    isPackagedNativeShell({
      isPackaged: true,
      platform: "linux",
      execPath: "/renamed/system-shell",
      resourcesPath: root,
    }),
    true,
  );

  fs.writeFileSync(profile, '{"schema":"wrong"}\n');
  assert.throws(
    () =>
      isPackagedNativeShell({
        isPackaged: true,
        platform: "linux",
        execPath: "/renamed/system-shell",
        resourcesPath: root,
      }),
    /identity marker is invalid/,
  );

  fs.rmSync(profile);
  fs.symlinkSync("missing-profile", profile);
  assert.throws(
    () =>
      isPackagedNativeShell({
        isPackaged: true,
        platform: "linux",
        execPath: "/renamed/system-shell",
        resourcesPath: root,
      }),
    /not a regular file/,
  );

  for (const candidate of [
    {
      isPackaged: false,
      platform: "linux",
      execPath: `/tmp/${NATIVE_SHELL_EXECUTABLE}`,
      resourcesPath: root,
    },
    {
      isPackaged: true,
      platform: "darwin",
      execPath: "/Applications/Echo",
      resourcesPath: root,
    },
    {
      isPackaged: true,
      platform: "win32",
      execPath: "C:\\Echo.exe",
      resourcesPath: root,
    },
  ]) {
    assert.equal(isPackagedNativeShell(candidate), false);
  }
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

console.log("Echo shell profile tests passed");
