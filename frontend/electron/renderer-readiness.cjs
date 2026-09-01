const fs = require("fs");
const path = require("path");

const READY_CONTENT = "provider=electron-renderer status=ready mode=desktop\n";

function publishRendererReadyFile(options = {}) {
  const desktopSession = options.desktopSession === true;
  const platform = options.platform || process.platform;
  const environment = options.environment || process.env;
  const logger = options.logger || console;
  const processId = options.processId ?? process.pid;
  const currentUid =
    options.currentUid ??
    (typeof process.getuid === "function" ? process.getuid() : -1);
  const now = options.now || Date.now;

  if (!desktopSession || platform !== "linux") {
    return { ok: false, skipped: true };
  }
  const runtimeRoot = environment.XDG_RUNTIME_DIR || "";
  const configuredPath = environment.ECHO_RENDERER_READY_FILE || "";
  if (!path.isAbsolute(runtimeRoot)) {
    logger.warn("[echo] refusing renderer-ready file without XDG_RUNTIME_DIR");
    return { ok: false, error: "invalid runtime root" };
  }
  const expectedPath = path.join(runtimeRoot, "echo-os", "renderer-ready");
  if (configuredPath !== expectedPath) {
    logger.warn("[echo] refusing non-canonical renderer-ready path");
    return { ok: false, error: "non-canonical readiness path" };
  }

  const parentPath = path.dirname(expectedPath);
  const temporaryPath = path.join(
    parentPath,
    `.renderer-ready-${processId}-${now()}`,
  );
  try {
    const parent = fs.lstatSync(parentPath);
    if (
      !parent.isDirectory() ||
      parent.isSymbolicLink() ||
      parent.uid !== currentUid ||
      (parent.mode & 0o077) !== 0
    ) {
      throw new Error("private runtime directory ownership/mode mismatch");
    }
    fs.writeFileSync(temporaryPath, READY_CONTENT, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    fs.renameSync(temporaryPath, expectedPath);
    return { ok: true, path: expectedPath };
  } catch (error) {
    try {
      fs.unlinkSync(temporaryPath);
    } catch {
      // The temporary file may not have been created.
    }
    logger.error("[echo] renderer-ready file failed:", error.message);
    return { ok: false, error: error.message };
  }
}

module.exports = { READY_CONTENT, publishRendererReadyFile };
