"use strict";

const fs = require("node:fs");
const path = require("node:path");

const NATIVE_SHELL_EXECUTABLE = "echo-os-desktop";
const NATIVE_SHELL_PROFILE = "native-shell-profile.json";
const NATIVE_SHELL_PROFILE_CONTENT =
  '{ "schema": "echo.native_shell_profile.v1" }\n';

function isPackagedNativeShell({
  isPackaged,
  platform,
  execPath,
  resourcesPath,
}) {
  if (isPackaged !== true || platform !== "linux") return false;
  const expectedExecutable =
    typeof execPath === "string" &&
    path.basename(execPath) === NATIVE_SHELL_EXECUTABLE;
  if (typeof resourcesPath !== "string" || !path.isAbsolute(resourcesPath)) {
    if (expectedExecutable) {
      throw new Error("Echo OS native shell resources path is invalid");
    }
    return false;
  }
  const profilePath = path.join(resourcesPath, NATIVE_SHELL_PROFILE);
  let metadata;
  try {
    metadata = fs.lstatSync(profilePath);
  } catch (error) {
    if (error?.code === "ENOENT" && !expectedExecutable) return false;
    throw new Error("Echo OS native shell identity marker is missing");
  }
  if (!metadata.isFile() || metadata.isSymbolicLink()) {
    throw new Error(
      "Echo OS native shell identity marker is not a regular file",
    );
  }
  let content;
  try {
    content = fs.readFileSync(profilePath, "utf8");
  } catch {
    throw new Error("Echo OS native shell identity marker cannot be read");
  }
  if (content !== NATIVE_SHELL_PROFILE_CONTENT) {
    throw new Error("Echo OS native shell identity marker is invalid");
  }
  return true;
}

module.exports = {
  NATIVE_SHELL_EXECUTABLE,
  NATIVE_SHELL_PROFILE,
  NATIVE_SHELL_PROFILE_CONTENT,
  isPackagedNativeShell,
};
