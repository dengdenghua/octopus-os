/**
 * Echo OS 原生会话和电源动作。
 *
 * 只有 `ECHO_NATIVE_SHELL=1`（兼容环境变量）或 session shell 模式下的
 * Linux 进程能调用 loginctl/systemctl。网页预览、普通 Electron 开发窗口和其他平台
 * 都只会返回 unsupported，不会触碰宿主系统。
 */
"use strict";

const fs = require("fs");
const { execFile } = require("child_process");

const SYSTEMCTL_CANDIDATES = ["/usr/bin/systemctl", "/bin/systemctl"];
const LOGINCTL_CANDIDATES = ["/usr/bin/loginctl", "/bin/loginctl"];
const ACTION_SPECS = Object.freeze({
  lock: { tool: "loginctl", args: ["lock-session", "self"] },
  logout: { tool: "loginctl", args: ["terminate-session", "self"] },
  suspend: { tool: "systemctl", args: ["suspend", "--no-block"] },
  restart: { tool: "systemctl", args: ["reboot", "--no-block"] },
  shutdown: { tool: "systemctl", args: ["poweroff", "--no-block"] },
});
const ACTION_ARGUMENTS = Object.freeze(
  Object.fromEntries(
    Object.entries(ACTION_SPECS).map(([action, spec]) => [action, spec.args]),
  ),
);

function resolveSystemctlPath(existsSync = fs.existsSync) {
  return (
    SYSTEMCTL_CANDIDATES.find((candidate) => existsSync(candidate)) || null
  );
}

function resolveLoginctlPath(existsSync = fs.existsSync) {
  return LOGINCTL_CANDIDATES.find((candidate) => existsSync(candidate)) || null;
}

function getSystemActionCapabilities({
  platform = process.platform,
  nativeShell = false,
  systemctlPath = resolveSystemctlPath(),
  loginctlPath = resolveLoginctlPath(),
  lockScreenReady = process.env.ECHO_LOCK_SCREEN_READY === "1",
} = {}) {
  const sessionShell = nativeShell && platform === "linux";
  const powerActions = sessionShell && Boolean(systemctlPath);
  const sessionActions = sessionShell && Boolean(loginctlPath);
  return {
    nativeShell: sessionShell,
    lock: sessionActions && lockScreenReady,
    logout: sessionActions,
    suspend: powerActions,
    restart: powerActions,
    shutdown: powerActions,
    reason: !sessionShell
      ? "system actions require the native Linux session shell"
      : undefined,
  };
}

function runSystemAction(
  action,
  {
    platform = process.platform,
    nativeShell = false,
    systemctlPath = resolveSystemctlPath(),
    loginctlPath = resolveLoginctlPath(),
    lockScreenReady = process.env.ECHO_LOCK_SCREEN_READY === "1",
    execFileImpl = execFile,
  } = {},
) {
  const spec = ACTION_SPECS[action];
  if (!spec) {
    return Promise.resolve({
      ok: false,
      action,
      error: "unknown system action",
    });
  }

  const capabilities = getSystemActionCapabilities({
    platform,
    nativeShell,
    systemctlPath,
    loginctlPath,
    lockScreenReady,
  });
  if (!capabilities[action]) {
    return Promise.resolve({
      ok: false,
      action,
      error: capabilities.reason || "system action is unavailable",
    });
  }

  const executable = spec.tool === "loginctl" ? loginctlPath : systemctlPath;
  if (!executable) {
    return Promise.resolve({
      ok: false,
      action,
      error: `${spec.tool} is unavailable`,
    });
  }

  return new Promise((resolve) => {
    execFileImpl(
      executable,
      spec.args,
      { timeout: 15_000, windowsHide: true },
      (error, _stdout, stderr) => {
        if (!error) {
          resolve({ ok: true, action });
          return;
        }
        resolve({
          ok: false,
          action,
          error: String(stderr || error.message || error).trim(),
        });
      },
    );
  });
}

module.exports = {
  ACTION_ARGUMENTS,
  getSystemActionCapabilities,
  resolveLoginctlPath,
  resolveSystemctlPath,
  runSystemAction,
};
