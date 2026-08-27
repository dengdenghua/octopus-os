/**
 * Fixed Echo OS system-update bridge.
 *
 * The renderer can read one bounded root-owned status file and request exactly
 * one PolicyKit action. It cannot choose an executable, bundle path or argv.
 */
"use strict";

const fs = require("fs");
const { execFile } = require("child_process");

const PKEXEC_CANDIDATES = ["/usr/bin/pkexec", "/bin/pkexec"];
const APPLY_INTERPRETER = "/bin/bash";
const APPLY_HELPER = "/usr/lib/echo-os/echo-os-update-apply";
const STATUS_ROOT = "/var/lib/echo-os-update";
const STATUS_PATH = `${STATUS_ROOT}/status.json`;
const MAX_STATUS_BYTES = 4096;
const STATES = new Set([
  "checking",
  "ready",
  "installing",
  "reboot-required",
  "failed",
]);
const PHASES = new Set(["fetch", "apply"]);
const VERSION = /^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$/;
const SHA256 = /^[0-9a-f]{64}$/;

function resolvePkexecPath(existsSync = fs.existsSync) {
  return PKEXEC_CANDIDATES.find((candidate) => existsSync(candidate)) || null;
}

function isSafeOwnedMetadata(metadata, kind) {
  const expectedKind =
    kind === "directory" ? metadata.isDirectory() : metadata.isFile();
  return expectedKind && metadata.uid === 0 && (metadata.mode & 0o022) === 0;
}

function validateStatusRecord(record) {
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    throw new Error("system update status is not an object");
  }
  const allowed = new Set([
    "schema",
    "state",
    "phase",
    "version",
    "manifestSha256",
    "updatedAt",
    "errorCode",
  ]);
  if (Object.keys(record).some((key) => !allowed.has(key))) {
    throw new Error("system update status contains unknown fields");
  }
  if (
    record.schema !== 1 ||
    !STATES.has(record.state) ||
    !PHASES.has(record.phase) ||
    !Number.isInteger(record.updatedAt) ||
    record.updatedAt < 1 ||
    record.updatedAt > 4_102_444_800
  ) {
    throw new Error("system update status schema is invalid");
  }
  if (record.version !== undefined && !VERSION.test(record.version)) {
    throw new Error("system update version is invalid");
  }
  if (
    record.manifestSha256 !== undefined &&
    !SHA256.test(record.manifestSha256)
  ) {
    throw new Error("system update manifest digest is invalid");
  }
  if (
    ["ready", "installing", "reboot-required"].includes(record.state) &&
    (!record.version || !record.manifestSha256)
  ) {
    throw new Error("authenticated system update status is incomplete");
  }
  if (
    record.state === "failed" &&
    (!Number.isInteger(record.errorCode) ||
      record.errorCode < 1 ||
      record.errorCode > 255)
  ) {
    throw new Error("failed system update status has no bounded code");
  }
  if (record.state !== "failed" && record.errorCode !== undefined) {
    throw new Error("non-failed system update status contains an error code");
  }
  return record;
}

function readStatusFile({
  statusRoot = STATUS_ROOT,
  statusPath = STATUS_PATH,
  lstatSync = fs.lstatSync,
  openSync = fs.openSync,
  fstatSync = fs.fstatSync,
  readFileSync = fs.readFileSync,
  closeSync = fs.closeSync,
} = {}) {
  let rootMetadata;
  try {
    rootMetadata = lstatSync(statusRoot);
  } catch (error) {
    if (error?.code === "ENOENT") return { schema: 1, state: "idle" };
    throw error;
  }
  if (
    rootMetadata.isSymbolicLink() ||
    !isSafeOwnedMetadata(rootMetadata, "directory")
  ) {
    throw new Error("system update status directory is unsafe");
  }
  let descriptor;
  try {
    descriptor = openSync(
      statusPath,
      fs.constants.O_RDONLY |
        fs.constants.O_CLOEXEC |
        (fs.constants.O_NOFOLLOW || 0),
    );
  } catch (error) {
    if (error?.code === "ENOENT") return { schema: 1, state: "idle" };
    throw error;
  }
  try {
    const before = fstatSync(descriptor);
    if (
      !isSafeOwnedMetadata(before, "file") ||
      before.size < 1 ||
      before.size > MAX_STATUS_BYTES
    ) {
      throw new Error(
        "system update status file is empty, oversized or unsafe",
      );
    }
    const raw = readFileSync(descriptor, { encoding: "utf8" });
    const after = fstatSync(descriptor);
    if (
      Buffer.byteLength(raw) !== before.size ||
      after.dev !== before.dev ||
      after.ino !== before.ino ||
      after.size !== before.size ||
      after.mtimeMs !== before.mtimeMs ||
      after.ctimeMs !== before.ctimeMs
    ) {
      throw new Error("system update status changed while reading");
    }
    return validateStatusRecord(JSON.parse(raw));
  } finally {
    closeSync(descriptor);
  }
}

function getSystemUpdateCapabilities({
  platform = process.platform,
  nativeShell = false,
  pkexecPath = resolvePkexecPath(),
  applyInterpreter = APPLY_INTERPRETER,
  applyHelper = APPLY_HELPER,
  existsSync = fs.existsSync,
} = {}) {
  const sessionShell = nativeShell && platform === "linux";
  const apply =
    sessionShell &&
    Boolean(pkexecPath) &&
    existsSync(applyInterpreter) &&
    existsSync(applyHelper);
  return {
    nativeShell: sessionShell,
    status: sessionShell,
    apply,
    reason: !sessionShell
      ? "system updates require the native Linux session shell"
      : !apply
        ? "the PolicyKit update helper is unavailable"
        : undefined,
  };
}

function getSystemUpdateStatus({
  platform = process.platform,
  nativeShell = false,
  readStatusImpl = readStatusFile,
} = {}) {
  if (!nativeShell || platform !== "linux") {
    return {
      schema: 1,
      state: "unavailable",
      error: "system updates require the native Linux session shell",
    };
  }
  try {
    return readStatusImpl();
  } catch (error) {
    return {
      schema: 1,
      state: "unavailable",
      error: String(error?.message || error).slice(0, 240),
    };
  }
}

function applySystemUpdate({
  platform = process.platform,
  nativeShell = false,
  pkexecPath = resolvePkexecPath(),
  applyInterpreter = APPLY_INTERPRETER,
  applyHelper = APPLY_HELPER,
  existsSync = fs.existsSync,
  execFileImpl = execFile,
} = {}) {
  const capabilities = getSystemUpdateCapabilities({
    platform,
    nativeShell,
    pkexecPath,
    applyInterpreter,
    applyHelper,
    existsSync,
  });
  if (!capabilities.apply) {
    return Promise.resolve({ ok: false, error: capabilities.reason });
  }
  return new Promise((resolve) => {
    execFileImpl(
      pkexecPath,
      ["--disable-internal-agent", applyInterpreter, applyHelper],
      {
        timeout: 4 * 60 * 60 * 1000,
        windowsHide: true,
        maxBuffer: 64 * 1024,
      },
      (error, _stdout, stderr) => {
        if (!error) {
          resolve({ ok: true });
          return;
        }
        const cancelled = error.code === 126 || error.code === 127;
        resolve({
          ok: false,
          cancelled,
          error: cancelled
            ? "administrator authorization was cancelled"
            : String(stderr || error.message || error)
                .trim()
                .slice(0, 240),
        });
      },
    );
  });
}

module.exports = {
  APPLY_INTERPRETER,
  APPLY_HELPER,
  STATUS_PATH,
  applySystemUpdate,
  getSystemUpdateCapabilities,
  getSystemUpdateStatus,
  readStatusFile,
  resolvePkexecPath,
  validateStatusRecord,
};
