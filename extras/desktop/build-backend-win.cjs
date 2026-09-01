const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { assertBuildPython } = require("./assert-build-python.cjs");

// This script lives in extras/desktop/ — repo root is two levels up, and
// electron-builder resolves "build/backend" relative to THIS package root.
const desktopRoot = __dirname;
const repoRoot = path.resolve(desktopRoot, "..", "..");
const buildRoot = path.join(desktopRoot, "build");
const backendOut = path.join(buildRoot, "backend");
const workPath = path.join(buildRoot, "pyinstaller-work");
const specPath = path.join(
  repoRoot,
  "packaging",
  "windows",
  "echo-backend.spec",
);
const expectedExe = path.join(backendOut, "echo-backend.exe");
const expectedLockedPython = path.join(
  repoRoot,
  ".venv",
  "Scripts",
  "python.exe",
);
const configuredPython = process.env.PYTHON_EXE;

function assertInside(parent, child) {
  const rel = path.relative(parent, child);
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new Error(`refusing to operate outside ${parent}: ${child}`);
  }
}

if (!configuredPython) {
  throw new Error(
    "PYTHON_EXE must point to the uv-locked Windows build environment",
  );
}

const lockedPython = path.resolve(configuredPython);
if (lockedPython.toLowerCase() !== expectedLockedPython.toLowerCase()) {
  throw new Error(`PYTHON_EXE must resolve to ${expectedLockedPython}`);
}
if (!fs.existsSync(lockedPython) || !fs.statSync(lockedPython).isFile()) {
  throw new Error(
    `locked Windows build interpreter is missing: ${lockedPython}; ` +
      "run uv sync --locked --python 3.11.9 " +
      "--extra desktop-core --extra desktop-build",
  );
}
assertBuildPython(lockedPython, {
  platform: "win32",
  architecture: "x64",
});

for (const target of [backendOut, workPath]) {
  assertInside(desktopRoot, target);
  fs.rmSync(target, { recursive: true, force: true });
}
fs.mkdirSync(backendOut, { recursive: true });
fs.mkdirSync(workPath, { recursive: true });

const result = spawnSync(
  lockedPython,
  [
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath",
    backendOut,
    "--workpath",
    workPath,
    specPath,
  ],
  {
    cwd: repoRoot,
    stdio: "inherit",
  },
);

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

if (!fs.existsSync(expectedExe)) {
  throw new Error(`PyInstaller finished but ${expectedExe} was not created`);
}

console.log(`[backend] built ${expectedExe}`);
