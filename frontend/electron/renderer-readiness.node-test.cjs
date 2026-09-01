const assert = require("assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  READY_CONTENT,
  publishRendererReadyFile,
} = require("./renderer-readiness.cjs");

const silentLogger = { warn() {}, error() {} };

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-renderer-ready-"));
  const runtime = path.join(root, "runtime");
  const privateDirectory = path.join(runtime, "echo-os");
  fs.mkdirSync(privateDirectory, { recursive: true, mode: 0o700 });
  fs.chmodSync(privateDirectory, 0o700);
  const readyPath = path.join(privateDirectory, "renderer-ready");
  return { root, runtime, privateDirectory, readyPath };
}

function publish(paths, overrides = {}) {
  return publishRendererReadyFile({
    desktopSession: true,
    platform: "linux",
    environment: {
      XDG_RUNTIME_DIR: paths.runtime,
      ECHO_RENDERER_READY_FILE: paths.readyPath,
    },
    currentUid: fs.lstatSync(paths.privateDirectory).uid,
    processId: 4242,
    now: () => 1700000000000,
    logger: silentLogger,
    ...overrides,
  });
}

{
  const paths = fixture();
  try {
    const result = publish(paths);
    assert.equal(result.ok, true);
    assert.equal(fs.readFileSync(paths.readyPath, "utf8"), READY_CONTENT);
    assert.equal(fs.statSync(paths.readyPath).mode & 0o777, 0o600);
    assert.deepEqual(fs.readdirSync(paths.privateDirectory).sort(), [
      "renderer-ready",
    ]);
    console.log(
      "  ✓ canonical private path is published atomically with mode 0600",
    );
  } finally {
    fs.rmSync(paths.root, { recursive: true, force: true });
  }
}

{
  const paths = fixture();
  try {
    const outside = path.join(paths.runtime, "renderer-controlled");
    const result = publish(paths, {
      environment: {
        XDG_RUNTIME_DIR: paths.runtime,
        ECHO_RENDERER_READY_FILE: outside,
      },
    });
    assert.equal(result.ok, false);
    assert.equal(fs.existsSync(outside), false);
    assert.equal(fs.existsSync(paths.readyPath), false);
    console.log("  ✓ renderer-controlled output path is rejected");
  } finally {
    fs.rmSync(paths.root, { recursive: true, force: true });
  }
}

{
  const paths = fixture();
  try {
    fs.chmodSync(paths.privateDirectory, 0o755);
    const result = publish(paths);
    assert.equal(result.ok, false);
    assert.equal(fs.existsSync(paths.readyPath), false);
    console.log("  ✓ group/world-accessible runtime directory is rejected");
  } finally {
    fs.rmSync(paths.root, { recursive: true, force: true });
  }
}

{
  const paths = fixture();
  const target = path.join(paths.root, "attacker-target");
  try {
    fs.rmSync(paths.privateDirectory, { recursive: true });
    fs.mkdirSync(target, { mode: 0o700 });
    fs.symlinkSync(target, paths.privateDirectory);
    const result = publish(paths, {
      currentUid: fs.lstatSync(target).uid,
    });
    assert.equal(result.ok, false);
    assert.equal(fs.existsSync(path.join(target, "renderer-ready")), false);
    console.log("  ✓ symlinked private runtime directory is rejected");
  } finally {
    fs.rmSync(paths.root, { recursive: true, force: true });
  }
}

console.log("Renderer readiness tests: 4 passed");
