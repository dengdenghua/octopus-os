"use strict";

const assert = require("assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  IPC_SCRIPT,
  READY_CONTENT,
  ROOT_WAYLAND_REQUEST_CONTENT,
  hasRootWaylandRequest,
  runNativeAppIpcSmoke,
} = require("./native-app-ipc-smoke.cjs");

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-native-ipc-"));
  const runtime = path.join(root, "runtime");
  const privateDirectory = path.join(runtime, "echo-os");
  fs.mkdirSync(privateDirectory, { recursive: true, mode: 0o700 });
  fs.chmodSync(privateDirectory, 0o700);
  return {
    root,
    runtime,
    privateDirectory,
    readyPath: path.join(privateDirectory, "native-app-ipc-ready"),
  };
}

function environment(paths, overrides = {}) {
  return {
    OCTOPUS_SMOKE: "1",
    OCTOPUS_NATIVE_APP_SMOKE_ID: "org.kde.kcalc",
    XDG_RUNTIME_DIR: paths.runtime,
    ECHO_NATIVE_APP_IPC_READY_FILE: paths.readyPath,
    ...overrides,
  };
}

async function run() {
  let passed = 0;
  {
    const paths = fixture();
    try {
      let executed = "";
      const result = await runNativeAppIpcSmoke({
        platform: "linux",
        desktopSession: true,
        environment: environment(paths),
        currentUid: fs.lstatSync(paths.privateDirectory).uid,
        processId: 4242,
        now: () => 1700000000000,
        webContents: {
          async executeJavaScript(script, userGesture) {
            executed = script;
            assert.equal(userGesture, true);
            return { ok: true };
          },
        },
      });
      assert.equal(result.ok, true);
      assert.match(executed, /window\.octopus.*apps/s);
      assert.match(executed, /bridge\.list\(\)/);
      assert.match(executed, /bridge\.launch\("org\.kde\.kcalc"\)/);
      assert.equal(executed, IPC_SCRIPT);
      assert.equal(fs.readFileSync(paths.readyPath, "utf8"), READY_CONTENT);
      assert.equal(fs.statSync(paths.readyPath).mode & 0o777, 0o600);
      assert.deepEqual(fs.readdirSync(paths.privateDirectory), [
        "native-app-ipc-ready",
      ]);
      passed += 1;
      console.log(
        "  ✓ preload list/launch IPC publishes one private ready marker",
      );
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  {
    const paths = fixture();
    try {
      const result = await runNativeAppIpcSmoke({
        platform: "linux",
        desktopSession: true,
        environment: environment(paths),
        currentUid: fs.lstatSync(paths.privateDirectory).uid,
        webContents: {
          async executeJavaScript() {
            return { ok: false };
          },
        },
      });
      assert.deepEqual(result, {
        ok: false,
        error: "native-app IPC launch did not succeed",
      });
      assert.equal(fs.existsSync(paths.readyPath), false);
      passed += 1;
      console.log("  ✓ renderer/GIO failure cannot publish readiness");
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  {
    const paths = fixture();
    try {
      let called = false;
      const result = await runNativeAppIpcSmoke({
        platform: "linux",
        desktopSession: true,
        environment: environment(paths, {
          ECHO_NATIVE_APP_IPC_READY_FILE: path.join(paths.runtime, "outside"),
        }),
        currentUid: fs.lstatSync(paths.privateDirectory).uid,
        webContents: {
          async executeJavaScript() {
            called = true;
            return { ok: true };
          },
        },
      });
      assert.equal(result.ok, false);
      assert.equal(called, false);
      assert.match(result.error, /not canonical/);
      assert.equal(fs.existsSync(path.join(paths.runtime, "outside")), false);
      passed += 1;
      console.log("  ✓ non-canonical readiness output is rejected");
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  {
    const paths = fixture();
    try {
      let called = false;
      const result = await runNativeAppIpcSmoke({
        platform: "linux",
        desktopSession: true,
        environment: environment(paths, {
          OCTOPUS_NATIVE_APP_SMOKE_ID: "org.kde.konsole",
        }),
        currentUid: fs.lstatSync(paths.privateDirectory).uid,
        webContents: {
          async executeJavaScript() {
            called = true;
            return { ok: true };
          },
        },
      });
      assert.equal(result.ok, false);
      assert.equal(called, false);
      assert.equal(fs.existsSync(paths.readyPath), false);
      passed += 1;
      console.log("  ✓ renderer/environment cannot select another application");
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  {
    const result = await runNativeAppIpcSmoke({ environment: {} });
    assert.deepEqual(result, { ok: false, skipped: true });
    passed += 1;
    console.log("  ✓ ordinary sessions skip the IPC diagnostic");
  }

  {
    const paths = fixture();
    try {
      let called = false;
      const result = await runNativeAppIpcSmoke({
        platform: "linux",
        desktopSession: true,
        environment: environment(paths, { OCTOPUS_SMOKE: "0" }),
        currentUid: fs.lstatSync(paths.privateDirectory).uid,
        webContents: {
          async executeJavaScript() {
            called = true;
            return { ok: true };
          },
        },
      });
      assert.deepEqual(result, {
        ok: false,
        error: "native-app IPC smoke is not authorized",
      });
      assert.equal(called, false);
      assert.equal(fs.existsSync(paths.readyPath), false);
      passed += 1;
      console.log(
        "  ✓ an uncredentialed desktop cannot request the diagnostic",
      );
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  {
    const paths = fixture();
    try {
      let timerCleared = false;
      const result = await runNativeAppIpcSmoke({
        platform: "linux",
        desktopSession: true,
        environment: environment(paths),
        currentUid: fs.lstatSync(paths.privateDirectory).uid,
        setTimer(callback) {
          callback();
          return 17;
        },
        clearTimer(timer) {
          assert.equal(timer, 17);
          timerCleared = true;
        },
        webContents: {
          executeJavaScript() {
            return new Promise(() => {});
          },
        },
      });
      assert.equal(result.ok, false);
      assert.match(result.error, /timed out/);
      assert.equal(timerCleared, true);
      assert.equal(fs.existsSync(paths.readyPath), false);
      passed += 1;
      console.log(
        "  ✓ a stalled renderer times out without publishing readiness",
      );
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  {
    const paths = fixture();
    try {
      const result = await runNativeAppIpcSmoke({
        platform: "linux",
        desktopSession: true,
        environment: environment(paths, {
          OCTOPUS_SMOKE: "0",
          XDG_SESSION_TYPE: "wayland",
        }),
        currentUid: fs.lstatSync(paths.privateDirectory).uid,
        rootWaylandRequestAuthorizer: () => true,
        webContents: {
          async executeJavaScript() {
            return { ok: true };
          },
        },
      });
      assert.equal(result.ok, true);
      assert.equal(fs.readFileSync(paths.readyPath, "utf8"), READY_CONTENT);
      passed += 1;
      console.log(
        "  ✓ a root-authorized Wayland candidate can request the fixed diagnostic",
      );
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  {
    const paths = fixture();
    try {
      let called = false;
      const result = await runNativeAppIpcSmoke({
        platform: "linux",
        desktopSession: true,
        environment: environment(paths, {
          OCTOPUS_SMOKE: "0",
          XDG_SESSION_TYPE: "x11",
        }),
        currentUid: fs.lstatSync(paths.privateDirectory).uid,
        rootWaylandRequestAuthorizer: () => true,
        webContents: {
          async executeJavaScript() {
            called = true;
            return { ok: true };
          },
        },
      });
      assert.deepEqual(result, {
        ok: false,
        error: "native-app IPC smoke is not authorized",
      });
      assert.equal(called, false);
      passed += 1;
      console.log("  ✓ the root request cannot authorize a non-Wayland session");
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  {
    const paths = fixture();
    const requestPath = path.join(paths.root, "request", "wayland-ipc");
    const requestParent = path.dirname(requestPath);
    fs.mkdirSync(requestParent, { mode: 0o700 });
    fs.writeFileSync(requestPath, ROOT_WAYLAND_REQUEST_CONTENT, { mode: 0o444 });
    const requiredUid = fs.statSync(requestPath).uid;
    try {
      assert.equal(hasRootWaylandRequest(requestPath, requiredUid), true);
      fs.chmodSync(requestPath, 0o644);
      assert.equal(hasRootWaylandRequest(requestPath, requiredUid), false);
      fs.writeFileSync(requestPath, "schema=1 app=org.kde.konsole\n");
      fs.chmodSync(requestPath, 0o444);
      assert.equal(hasRootWaylandRequest(requestPath, requiredUid), false);
      fs.chmodSync(requestPath, 0o644);
      fs.writeFileSync(requestPath, ROOT_WAYLAND_REQUEST_CONTENT);
      fs.chmodSync(requestPath, 0o444);
      const targetPath = path.join(requestParent, "request-target");
      fs.renameSync(requestPath, targetPath);
      fs.symlinkSync(targetPath, requestPath);
      assert.equal(hasRootWaylandRequest(requestPath, requiredUid), false);
      passed += 1;
      console.log(
        "  ✓ the root request requires an exact read-only regular file in a safe directory",
      );
    } finally {
      fs.rmSync(paths.root, { recursive: true, force: true });
    }
  }

  console.log(`\nNative application IPC smoke tests: ${passed} passed`);
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
