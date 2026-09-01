"use strict";

const fs = require("fs");
const path = require("path");

const SMOKE_APP_ID = "org.kde.kcalc";
const ROOT_WAYLAND_REQUEST_PATH = "/etc/echo-os/wayland-native-app-ipc";
const ROOT_WAYLAND_REQUEST_CONTENT = "schema=1 app=org.kde.kcalc\n";
const READY_CONTENT =
  "app=org.kde.kcalc path=preload-ipc-gio result=zero-exit\n";
const IPC_TIMEOUT_MS = 15_000;
const IPC_SCRIPT = `(async () => {
  const bridge = window.echo && window.echo.apps;
  if (!bridge) return { ok: false, error: "apps bridge unavailable" };
  const apps = await bridge.list();
  if (!Array.isArray(apps) || !apps.some((app) => app && app.id === "org.kde.kcalc")) {
    return { ok: false, error: "kcalc is not enumerated" };
  }
  return bridge.launch("org.kde.kcalc");
})()`;

function boundedError(error) {
  return String(error && error.message ? error.message : error || "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 256);
}

function hasSystemCredential(environment, currentUid) {
  const credentialDirectory = environment.CREDENTIALS_DIRECTORY || "";
  if (credentialDirectory !== "/run/credentials/echo-desktop.service") {
    return false;
  }
  try {
    const directory = fs.lstatSync(credentialDirectory);
    const credential = fs.lstatSync(
      path.join(credentialDirectory, "echo.os.ci-session"),
    );
    const ownerAllowed = (metadata) =>
      metadata.uid === 0 || metadata.uid === currentUid;
    return (
      directory.isDirectory() &&
      !directory.isSymbolicLink() &&
      ownerAllowed(directory) &&
      (directory.mode & 0o022) === 0 &&
      credential.isFile() &&
      !credential.isSymbolicLink() &&
      ownerAllowed(credential) &&
      (credential.mode & 0o022) === 0 &&
      credential.size > 0 &&
      credential.size <= 64
    );
  } catch {
    return false;
  }
}

function hasRootWaylandRequest(
  requestPath = ROOT_WAYLAND_REQUEST_PATH,
  requiredUid = 0,
) {
  let descriptor = null;
  try {
    const parent = fs.lstatSync(path.dirname(requestPath));
    if (
      !parent.isDirectory() ||
      parent.isSymbolicLink() ||
      parent.uid !== requiredUid ||
      (parent.mode & 0o022) !== 0
    ) {
      return false;
    }
    descriptor = fs.openSync(
      requestPath,
      fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0),
    );
    const request = fs.fstatSync(descriptor);
    const permissions = request.mode & 0o777;
    if (
      !request.isFile() ||
      request.uid !== requiredUid ||
      permissions !== 0o444 ||
      request.size !== Buffer.byteLength(ROOT_WAYLAND_REQUEST_CONTENT)
    ) {
      return false;
    }
    return fs.readFileSync(descriptor, "utf8") === ROOT_WAYLAND_REQUEST_CONTENT;
  } catch {
    return false;
  } finally {
    if (descriptor !== null) fs.closeSync(descriptor);
  }
}

function validateReadyFilePath(environment, currentUid) {
  const runtimeRoot = environment.XDG_RUNTIME_DIR || "";
  const configuredPath = environment.ECHO_NATIVE_APP_IPC_READY_FILE || "";
  if (!path.isAbsolute(runtimeRoot)) {
    throw new Error("native-app IPC smoke requires an absolute runtime root");
  }
  const expectedPath = path.join(
    runtimeRoot,
    "echo-os",
    "native-app-ipc-ready",
  );
  if (configuredPath !== expectedPath) {
    throw new Error("native-app IPC smoke readiness path is not canonical");
  }
  const parentPath = path.dirname(expectedPath);
  const parent = fs.lstatSync(parentPath);
  if (
    !parent.isDirectory() ||
    parent.isSymbolicLink() ||
    parent.uid !== currentUid ||
    (parent.mode & 0o077) !== 0
  ) {
    throw new Error("native-app IPC smoke runtime directory is unsafe");
  }
  return { expectedPath, parentPath };
}

function publishReadyFile(environment, currentUid, processId, now) {
  const { expectedPath, parentPath } = validateReadyFilePath(
    environment,
    currentUid,
  );
  const temporaryPath = path.join(
    parentPath,
    `.native-app-ipc-ready-${processId}-${now()}`,
  );
  try {
    // Re-check the parent immediately before the atomic write so an earlier
    // preflight cannot be used across a replaced runtime directory.
    validateReadyFilePath(environment, currentUid);
    fs.writeFileSync(temporaryPath, READY_CONTENT, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    fs.renameSync(temporaryPath, expectedPath);
  } catch (error) {
    try {
      fs.unlinkSync(temporaryPath);
    } catch {
      // The temporary file may not have been created.
    }
    throw error;
  }
  return expectedPath;
}

async function executeWithTimeout(webContents, setTimer, clearTimer) {
  let timer = null;
  try {
    return await Promise.race([
      webContents.executeJavaScript(IPC_SCRIPT, true),
      new Promise((_, reject) => {
        timer = setTimer(
          () => reject(new Error("native-app IPC smoke timed out")),
          IPC_TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    if (timer !== null) clearTimer(timer);
  }
}

async function runNativeAppIpcSmoke(options = {}) {
  const environment = options.environment || process.env;
  const requestedApp = environment.ECHO_NATIVE_APP_SMOKE_ID || "";
  if (!requestedApp) return { ok: false, skipped: true };
  const platform = options.platform || process.platform;
  const desktopSession = options.desktopSession === true;
  const currentUid =
    options.currentUid ??
    (typeof process.getuid === "function" ? process.getuid() : -1);
  const standaloneSmoke = environment.ECHO_SMOKE === "1";
  const rootWaylandRequest =
    environment.XDG_SESSION_TYPE === "wayland" &&
    (options.rootWaylandRequestAuthorizer
      ? options.rootWaylandRequestAuthorizer()
      : hasRootWaylandRequest());
  if (
    platform !== "linux" ||
    !desktopSession ||
    requestedApp !== SMOKE_APP_ID ||
    (!standaloneSmoke &&
      !hasSystemCredential(environment, currentUid) &&
      !rootWaylandRequest)
  ) {
    return { ok: false, error: "native-app IPC smoke is not authorized" };
  }
  if (!options.webContents) {
    return { ok: false, error: "native-app IPC smoke has no renderer" };
  }
  try {
    validateReadyFilePath(environment, currentUid);
    const launchResult = await executeWithTimeout(
      options.webContents,
      options.setTimer || setTimeout,
      options.clearTimer || clearTimeout,
    );
    if (!launchResult || launchResult.ok !== true) {
      return { ok: false, error: "native-app IPC launch did not succeed" };
    }
    const readyPath = publishReadyFile(
      environment,
      currentUid,
      options.processId ?? process.pid,
      options.now || Date.now,
    );
    return {
      ok: true,
      path: readyPath,
      marker:
        "ECHO_NATIVE_APP_IPC_ACCEPTED app=org.kde.kcalc " +
        "path=preload-ipc-gio result=zero-exit",
    };
  } catch (error) {
    return {
      ok: false,
      error: boundedError(error) || "native-app IPC smoke failed",
    };
  }
}

module.exports = {
  IPC_SCRIPT,
  READY_CONTENT,
  ROOT_WAYLAND_REQUEST_CONTENT,
  ROOT_WAYLAND_REQUEST_PATH,
  hasRootWaylandRequest,
  runNativeAppIpcSmoke,
};
