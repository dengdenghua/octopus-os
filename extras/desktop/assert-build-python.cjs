const { spawnSync } = require("node:child_process");

const LOCKED_PYTHON_VERSION = "3.11.9";

function normalizeArchitecture(value) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase();
  if (["amd64", "x64", "x86_64"].includes(normalized)) return "x64";
  if (["aarch64", "arm64"].includes(normalized)) return "arm64";
  return normalized;
}

function validateBuildPythonIdentity(identity, expected) {
  const actualVersion = String(identity.version || "");
  const actualPlatform = String(identity.platform || "");
  const actualArchitecture = normalizeArchitecture(identity.machine);
  const expectedArchitecture = normalizeArchitecture(expected.architecture);
  if (actualVersion !== LOCKED_PYTHON_VERSION) {
    throw new Error(
      `build Python version is ${actualVersion || "unknown"}; expected ${LOCKED_PYTHON_VERSION}`,
    );
  }
  if (actualPlatform !== expected.platform) {
    throw new Error(
      `build Python platform is ${actualPlatform || "unknown"}; expected ${expected.platform}`,
    );
  }
  if (actualArchitecture !== expectedArchitecture) {
    throw new Error(
      `build Python architecture is ${actualArchitecture || "unknown"}; expected ${expectedArchitecture}`,
    );
  }
}

function assertBuildPython(executable, expected) {
  const probe = spawnSync(
    executable,
    [
      "-c",
      "import json,platform,sys;print(json.dumps({'version':platform.python_version(),'platform':sys.platform,'machine':platform.machine()}))",
    ],
    { encoding: "utf8", windowsHide: true },
  );
  if (probe.status !== 0) {
    throw new Error(
      `unable to inspect locked build Python: ${(probe.stderr || probe.error?.message || "probe failed").trim()}`,
    );
  }
  let identity;
  try {
    identity = JSON.parse(probe.stdout);
  } catch {
    throw new Error("locked build Python returned an invalid identity probe");
  }
  validateBuildPythonIdentity(identity, expected);
  return identity;
}

module.exports = {
  LOCKED_PYTHON_VERSION,
  assertBuildPython,
  normalizeArchitecture,
  validateBuildPythonIdentity,
};
