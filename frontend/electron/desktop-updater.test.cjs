"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const {
  boundedErrorMessage,
  boundedVersionLabel,
  configureDesktopUpdater,
  shouldEnableDesktopUpdater,
} = require("./desktop-updater.cjs");

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

class FakeUpdater extends EventEmitter {
  constructor() {
    super();
    this.checks = 0;
    this.downloads = 0;
    this.installs = 0;
  }

  async checkForUpdates() {
    this.checks += 1;
  }

  async downloadUpdate() {
    this.downloads += 1;
  }

  quitAndInstall() {
    this.installs += 1;
  }
}

function controllerFixture(responses = []) {
  const updater = new FakeUpdater();
  const prompts = [];
  const warnings = [];
  let gracefulInstalls = 0;
  let initialCallback = null;
  let intervalCallback = null;
  const controller = configureDesktopUpdater({
    autoUpdater: updater,
    dialog: {
      async showMessageBox(options) {
        prompts.push(options);
        return { response: responses.shift() ?? 1 };
      },
    },
    logger: {
      debug() {},
      info() {},
      warn(message) {
        warnings.push(message);
      },
      error() {},
    },
    requestQuitAndInstall() {
      gracefulInstalls += 1;
    },
    setTimeoutImpl(callback) {
      initialCallback = callback;
      return { unref() {} };
    },
    clearTimeoutImpl() {},
    setIntervalImpl(callback) {
      intervalCallback = callback;
      return { unref() {} };
    },
    clearIntervalImpl() {},
  });
  return {
    controller,
    initial: () => initialCallback(),
    interval: () => intervalCallback(),
    prompts,
    updater,
    warnings,
    get gracefulInstalls() {
      return gracefulInstalls;
    },
  };
}

async function run() {
  assert.equal(
    shouldEnableDesktopUpdater({
      isPackaged: true,
      nativeShell: false,
      smoke: false,
      disabled: false,
      platform: "darwin",
    }),
    true,
  );
  for (const context of [
    { isPackaged: false, platform: "darwin" },
    { isPackaged: true, nativeShell: true, platform: "darwin" },
    { isPackaged: true, smoke: true, platform: "win32" },
    { isPackaged: true, disabled: true, platform: "win32" },
    { isPackaged: true, platform: "linux", isAppImage: false },
  ]) {
    assert.equal(
      shouldEnableDesktopUpdater({
        nativeShell: false,
        smoke: false,
        disabled: false,
        ...context,
      }),
      false,
    );
  }

  const fixture = controllerFixture([0, 0]);
  assert.equal(fixture.updater.autoDownload, false);
  assert.equal(fixture.updater.autoInstallOnAppQuit, true);
  assert.equal(fixture.updater.allowPrerelease, false);
  assert.equal(fixture.updater.allowDowngrade, false);
  fixture.initial();
  await flush();
  assert.equal(fixture.updater.checks, 1);
  fixture.interval();
  await flush();
  assert.equal(fixture.updater.checks, 2);

  fixture.updater.emit("update-available", { version: "0.3.0" });
  await flush();
  await flush();
  assert.equal(fixture.updater.downloads, 1);
  assert.match(fixture.prompts[0].message, /0\.3\.0/);

  fixture.updater.emit("update-downloaded", { version: "0.3.0" });
  await flush();
  await flush();
  assert.equal(fixture.gracefulInstalls, 1);
  assert.equal(fixture.updater.installs, 0);
  assert.match(fixture.prompts[1].detail, /Agent/);

  fixture.controller.dispose();
  fixture.updater.emit("update-available", { version: "9.9.9" });
  await flush();
  assert.equal(fixture.prompts.length, 2);

  const sanitized = boundedErrorMessage(new Error("bad\nsecret\tvalue"));
  assert.equal(sanitized, "bad secret value");
  assert.ok(boundedErrorMessage("x".repeat(900)).length <= 500);
  assert.equal(boundedVersionLabel(" 0.3.0\nforged "), "0.3.0 forged");
  assert.equal(boundedVersionLabel(""), "新版本");
  assert.equal(boundedVersionLabel("v".repeat(100)).length, 64);
  console.log("Echo desktop updater tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
