/**
 * Echo OS native Agent service control.
 *
 * The renderer may request one product operation: restart the image-baked
 * echo-agent.service after a setting that requires a runtime restart changes.
 * It cannot choose a unit, executable, argument, or shell command. systemd's
 * normal Polkit policy remains the authorization boundary for the desktop
 * administrator.
 */
"use strict";

const fs = require("fs");
const { execFile } = require("child_process");

const AGENT_SERVICE = "echo-agent.service";
const SYSTEMCTL_CANDIDATES = ["/usr/bin/systemctl", "/bin/systemctl"];
const HEALTH_VERIFIER = "/usr/lib/echo-os/verify-native-agent-health";

function resolveSystemctlPath(existsSync = fs.existsSync) {
  return (
    SYSTEMCTL_CANDIDATES.find((candidate) => existsSync(candidate)) || null
  );
}

function resolveHealthVerifierPath(existsSync = fs.existsSync) {
  return existsSync(HEALTH_VERIFIER) ? HEALTH_VERIFIER : null;
}

function getAgentServiceCapabilities({
  platform = process.platform,
  nativeShell = false,
  systemctlPath = resolveSystemctlPath(),
  healthVerifierPath = resolveHealthVerifierPath(),
} = {}) {
  const restart =
    platform === "linux" &&
    nativeShell &&
    Boolean(systemctlPath) &&
    Boolean(healthVerifierPath);
  return {
    nativeShell: platform === "linux" && nativeShell,
    restart,
    reason: restart
      ? undefined
      : "Agent restart requires the native Linux desktop session",
  };
}

function boundedError(value) {
  return String(value || "Agent service restart failed").trim().slice(0, 512);
}

function restartAgentService({
  platform = process.platform,
  nativeShell = false,
  systemctlPath = resolveSystemctlPath(),
  healthVerifierPath = resolveHealthVerifierPath(),
  execFileImpl = execFile,
} = {}) {
  const capabilities = getAgentServiceCapabilities({
    platform,
    nativeShell,
    systemctlPath,
    healthVerifierPath,
  });
  if (!capabilities.restart || !systemctlPath || !healthVerifierPath) {
    return Promise.resolve({ ok: false, reason: capabilities.reason });
  }

  return new Promise((resolve) => {
    execFileImpl(
      systemctlPath,
      ["restart", AGENT_SERVICE],
      {
        timeout: 30_000,
        windowsHide: true,
        maxBuffer: 64 * 1024,
      },
      (error, _stdout, stderr) => {
        if (error) {
          resolve({
            ok: false,
            reason: boundedError(
              `systemd restart failed: ${stderr || error.message || error}`,
            ),
          });
          return;
        }
        execFileImpl(
          healthVerifierPath,
          [],
          {
            timeout: 135_000,
            windowsHide: true,
            maxBuffer: 64 * 1024,
          },
          (healthError, _healthStdout, healthStderr) => {
            if (!healthError) {
              resolve({ ok: true });
              return;
            }
            resolve({
              ok: false,
              reason: boundedError(
                `Agent health gate failed: ${
                  healthStderr || healthError.message || healthError
                }`,
              ),
            });
          },
        );
      },
    );
  });
}

module.exports = {
  AGENT_SERVICE,
  HEALTH_VERIFIER,
  getAgentServiceCapabilities,
  resolveHealthVerifierPath,
  resolveSystemctlPath,
  restartAgentService,
};
