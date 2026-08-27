/** Private bridge from the Echo renderer to the session notification daemon. */
"use strict";

const fs = require("fs");
const net = require("net");
const path = require("path");

const MAX_RESPONSE_BYTES = 1024 * 1024;
const REQUEST_TIMEOUT_MS = 1500;

function resolveNotificationSocket(options = {}) {
  const platform = options.platform || process.platform;
  const nativeShell = options.nativeShell === true;
  if (platform !== "linux" || !nativeShell) {
    return { ok: false, reason: "native Linux session unavailable" };
  }
  const runtimeDir = options.runtimeDir || process.env.XDG_RUNTIME_DIR || "";
  const socketPath =
    options.socketPath || process.env.ECHO_NOTIFICATION_SOCKET || "";
  if (!path.isAbsolute(runtimeDir) || !path.isAbsolute(socketPath)) {
    return { ok: false, reason: "notification service unavailable" };
  }
  const expected = path.join(runtimeDir, "echo-os", "notifications.sock");
  if (path.resolve(socketPath) !== expected) {
    return { ok: false, reason: "notification socket path rejected" };
  }
  try {
    const stat = (options.lstatSync || fs.lstatSync)(socketPath);
    const currentUid = options.uid ?? process.getuid?.();
    if (
      !stat.isSocket() ||
      (currentUid !== undefined && stat.uid !== currentUid) ||
      (stat.mode & 0o077) !== 0
    ) {
      return { ok: false, reason: "notification socket permissions rejected" };
    }
  } catch {
    return { ok: false, reason: "notification service unavailable" };
  }
  return { ok: true, socketPath };
}

function requestNotificationService(request, options = {}) {
  const resolved = resolveNotificationSocket(options);
  if (!resolved.ok) return Promise.resolve(resolved);
  const connect = options.createConnection || net.createConnection;
  const maxBytes = options.maxResponseBytes || MAX_RESPONSE_BYTES;
  const timeoutMs = options.timeoutMs || REQUEST_TIMEOUT_MS;

  return new Promise((resolve) => {
    let finished = false;
    let received = Buffer.alloc(0);
    let client;
    const finish = (result) => {
      if (finished) return;
      finished = true;
      if (client && !client.destroyed) client.destroy();
      resolve(result);
    };
    try {
      client = connect({ path: resolved.socketPath }, () => {
        client.write(`${JSON.stringify(request)}\n`);
      });
    } catch {
      finish({ ok: false, error: "notification service connection failed" });
      return;
    }
    client.setTimeout(timeoutMs);
    client.on("timeout", () =>
      finish({ ok: false, error: "notification service timed out" }),
    );
    client.on("error", () =>
      finish({ ok: false, error: "notification service connection failed" }),
    );
    client.on("data", (chunk) => {
      received = Buffer.concat([received, chunk]);
      if (received.length > maxBytes) {
        finish({ ok: false, error: "notification response too large" });
        return;
      }
      const newline = received.indexOf(0x0a);
      if (newline < 0) return;
      try {
        const response = JSON.parse(received.subarray(0, newline).toString("utf8"));
        finish(
          response && typeof response === "object"
            ? response
            : { ok: false, error: "invalid notification response" },
        );
      } catch {
        finish({ ok: false, error: "invalid notification response" });
      }
    });
    client.on("end", () => {
      if (!finished) finish({ ok: false, error: "incomplete notification response" });
    });
  });
}

function normalizeNotification(value) {
  if (!value || typeof value !== "object") return null;
  const id = Number(value.id);
  const createdAt = Number(value.createdAt);
  const updatedAt = Number(value.updatedAt);
  if (
    !Number.isInteger(id) ||
    id < 1 ||
    id > 0xffffffff ||
    !Number.isFinite(createdAt) ||
    !Number.isFinite(updatedAt)
  ) {
    return null;
  }
  const bounded = (text, max) => String(text || "").replace(/\0/g, "").slice(0, max);
  return {
    id,
    appName: bounded(value.appName, 128) || "应用",
    summary: bounded(value.summary, 512),
    body: bounded(value.body, 4096),
    createdAt,
    updatedAt,
  };
}

async function getNotificationCapabilities(options = {}) {
  const response = await requestNotificationService(
    { op: "capabilities" },
    options,
  );
  return response.ok
    ? {
        nativeShell: true,
        provider: "echo-native",
        list: response.list === true,
        close: response.close === true,
        clear: response.clear === true,
      }
    : {
        nativeShell: false,
        provider: null,
        list: false,
        close: false,
        clear: false,
        reason: response.reason || response.error || "notification service unavailable",
      };
}

async function listNotifications(options = {}) {
  const response = await requestNotificationService({ op: "list" }, options);
  if (!response.ok || !Array.isArray(response.notifications)) {
    return {
      ok: false,
      provider: null,
      notifications: [],
      error: response.reason || response.error || "notification service unavailable",
    };
  }
  return {
    ok: true,
    provider: "echo-native",
    notifications: response.notifications
      .slice(0, 100)
      .map(normalizeNotification)
      .filter(Boolean),
  };
}

function closeNotification(notificationId, options = {}) {
  const id = Number(notificationId);
  if (!Number.isInteger(id) || id < 1 || id > 0xffffffff) {
    return Promise.resolve({ ok: false, error: "invalid notification id" });
  }
  return requestNotificationService({ op: "close", id }, options);
}

function clearNotifications(options = {}) {
  return requestNotificationService({ op: "clear" }, options);
}

module.exports = {
  clearNotifications,
  closeNotification,
  getNotificationCapabilities,
  listNotifications,
  normalizeNotification,
  requestNotificationService,
  resolveNotificationSocket,
};
