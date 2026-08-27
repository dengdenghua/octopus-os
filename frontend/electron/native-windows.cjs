/**
 * Echo OS · native window-manager bridge.
 *
 * Target C makes the compositor/window manager the source of truth for native
 * application windows.  The first provider uses the freedesktop EWMH contract
 * through `wmctrl`, so the KWin X11 bring-up session can be exercised now.  The
 * The Wayland provider consumes compositor-owned UUID snapshots from the
 * session-private KWin script bridge. Both providers implement one renderer
 * contract, so the Dock does not parse X11 or D-Bus data itself.
 *
 * Security boundary: renderer input is restricted to an X11 id or canonical
 * KWin UUID and a fixed action. It can never supply a command, executable path,
 * D-Bus destination, or arguments.
 */

"use strict";

const fs = require("fs");
const net = require("net");
const path = require("path");
const { execFile } = require("child_process");

const WMCTRL_PATHS = ["/usr/bin/wmctrl", "/bin/wmctrl"];
const XPROP_PATHS = ["/usr/bin/xprop", "/bin/xprop"];
const WINDOW_ID = /^0x[0-9a-f]+$/i;
const KWIN_WINDOW_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_BRIDGE_MESSAGE_BYTES = 1024 * 1024;

function firstExecutable(candidates, exists = fs.existsSync) {
  return candidates.find((candidate) => exists(candidate)) || null;
}

function normalizeWindowId(value) {
  const id = String(value || "")
    .trim()
    .toLowerCase();
  if (!WINDOW_ID.test(id)) throw new Error("invalid native window id");
  return `0x${BigInt(id).toString(16)}`;
}

function normalizeKWinWindowId(value) {
  const id = String(value || "")
    .trim()
    .replace(/^\{/, "")
    .replace(/\}$/, "")
    .toLowerCase();
  if (!KWIN_WINDOW_ID.test(id)) throw new Error("invalid KWin window UUID");
  return id;
}

function normalizeBridgeText(value, maximum, field) {
  const text = String(value || "");
  if (text.includes("\0") || text.length > maximum) {
    throw new Error(`invalid KWin ${field}`);
  }
  return text;
}

function parseKWinWindowList(value) {
  if (!Array.isArray(value) || value.length > 4096) {
    throw new Error("invalid KWin window snapshot");
  }
  return value.map((window) => {
    if (!window || typeof window !== "object") {
      throw new Error("invalid KWin window entry");
    }
    const desktop = window.desktop;
    const pid = window.pid;
    if (
      !Number.isInteger(desktop) ||
      desktop < -1 ||
      desktop > 10000 ||
      !Number.isInteger(pid) ||
      pid < 0 ||
      pid > 2 ** 31 - 1 ||
      typeof window.active !== "boolean" ||
      typeof window.minimized !== "boolean" ||
      window.provider !== "kwin-wayland"
    ) {
      throw new Error("invalid KWin window state");
    }
    return {
      id: normalizeKWinWindowId(window.id),
      desktop,
      pid,
      host: normalizeBridgeText(window.host, 255, "host"),
      wmClass: normalizeBridgeText(window.wmClass, 512, "window class"),
      title: normalizeBridgeText(window.title, 1024, "window title"),
      active: window.active,
      minimized: window.minimized,
      provider: "kwin-wayland",
    };
  });
}

/** Parse `wmctrl -l -x -p` output. */
function parseWmctrlWindowList(output, activeWindowId = null) {
  const active = activeWindowId ? normalizeWindowId(activeWindowId) : null;
  return String(output || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => {
      // id desktop pid host wmClass title (title may contain arbitrary spaces)
      const match = line.match(
        /^(0x[0-9a-f]+)\s+(-?\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s*(.*)$/i,
      );
      if (!match) return [];
      const [, rawId, rawDesktop, rawPid, host, wmClass, title] = match;
      const id = normalizeWindowId(rawId);
      return [
        {
          id,
          desktop: Number(rawDesktop),
          pid: Number(rawPid),
          host,
          wmClass,
          title: title || wmClass,
          active: id === active,
          minimized: null,
          provider: "ewmh-x11",
        },
      ];
    });
}

function parseActiveWindow(output) {
  const match = String(output || "").match(/0x[0-9a-f]+/i);
  if (!match || /^0x0+$/i.test(match[0])) return null;
  return normalizeWindowId(match[0]);
}

function runFile(file, args, options = {}) {
  const execFileImpl = options.execFileImpl || execFile;
  return new Promise((resolve, reject) => {
    execFileImpl(
      file,
      args,
      { timeout: 3000, maxBuffer: 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error) {
          error.stderr = stderr;
          reject(error);
          return;
        }
        resolve(String(stdout || ""));
      },
    );
  });
}

function requestKWinBridge(socketPath, request, options = {}) {
  const createConnection = options.createConnection || net.createConnection;
  const timeout = options.timeout || 3500;
  return new Promise((resolve, reject) => {
    const socket = createConnection({ path: socketPath });
    let settled = false;
    let received = Buffer.alloc(0);
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      if (error) reject(error);
      else resolve(value);
    };
    socket.setTimeout(timeout, () =>
      finish(new Error("KWin window bridge timed out")),
    );
    socket.on("error", (error) => finish(error));
    socket.on("connect", () => {
      const encoded = Buffer.from(`${JSON.stringify(request)}\n`, "utf8");
      if (encoded.length > MAX_BRIDGE_MESSAGE_BYTES) {
        finish(new Error("KWin window bridge request is too large"));
        return;
      }
      socket.write(encoded);
    });
    socket.on("data", (chunk) => {
      received = Buffer.concat([received, chunk]);
      if (received.length > MAX_BRIDGE_MESSAGE_BYTES) {
        finish(new Error("KWin window bridge response is too large"));
        return;
      }
      const newline = received.indexOf(0x0a);
      if (newline < 0) return;
      try {
        const response = JSON.parse(
          received.subarray(0, newline).toString("utf8"),
        );
        if (
          !response ||
          typeof response !== "object" ||
          Array.isArray(response)
        ) {
          throw new Error("invalid KWin window bridge response");
        }
        finish(null, response);
      } catch (error) {
        finish(error);
      }
    });
    socket.on("end", () => {
      if (!settled)
        finish(new Error("KWin window bridge closed without a response"));
    });
  });
}

function resolveRuntime(options = {}) {
  const platform = options.platform || process.platform;
  const sessionType =
    options.sessionType === undefined
      ? process.env.XDG_SESSION_TYPE ||
        (process.env.WAYLAND_DISPLAY ? "wayland" : "x11")
      : options.sessionType;
  const display =
    options.display === undefined ? process.env.DISPLAY : options.display;
  const wmctrlPath =
    options.wmctrlPath === undefined
      ? firstExecutable(WMCTRL_PATHS, options.exists)
      : options.wmctrlPath;
  const xpropPath =
    options.xpropPath === undefined
      ? firstExecutable(XPROP_PATHS, options.exists)
      : options.xpropPath;
  const runtimeDirectory =
    options.runtimeDirectory === undefined
      ? process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid?.() || 0}`
      : options.runtimeDirectory;
  const waylandBridgePath =
    options.waylandBridgePath === undefined
      ? path.join(runtimeDirectory, "echo-os", "kwin-window-bridge.sock")
      : options.waylandBridgePath;
  const exists = options.exists || fs.existsSync;
  return {
    nativeShell: Boolean(options.nativeShell),
    platform,
    sessionType,
    display,
    wmctrlPath,
    xpropPath,
    waylandBridgePath,
    waylandBridgeReady:
      options.waylandBridgeReady === undefined
        ? Boolean(waylandBridgePath && exists(waylandBridgePath))
        : Boolean(options.waylandBridgeReady),
    waylandRequestImpl: options.waylandRequestImpl || requestKWinBridge,
    execFileImpl: options.execFileImpl,
  };
}

function getNativeWindowCapabilities(options = {}) {
  const runtime = resolveRuntime(options);
  if (!runtime.nativeShell) {
    return {
      nativeShell: false,
      provider: null,
      list: false,
      focus: false,
      minimize: false,
      close: false,
      reason: "native desktop session is not active",
    };
  }
  if (runtime.platform !== "linux") {
    return {
      nativeShell: true,
      provider: null,
      list: false,
      focus: false,
      minimize: false,
      close: false,
      reason: "native window control is currently available on Linux only",
    };
  }
  if (runtime.sessionType === "wayland") {
    if (!runtime.waylandBridgeReady) {
      return {
        nativeShell: true,
        provider: null,
        list: false,
        focus: false,
        minimize: false,
        close: false,
        reason: "KWin Wayland window bridge is unavailable",
      };
    }
    return {
      nativeShell: true,
      provider: "kwin-wayland",
      list: true,
      focus: true,
      minimize: true,
      close: true,
    };
  }
  if (!runtime.display) {
    return {
      nativeShell: true,
      provider: null,
      list: false,
      focus: false,
      minimize: false,
      close: false,
      reason: "DISPLAY is not set",
    };
  }
  if (!runtime.wmctrlPath) {
    return {
      nativeShell: true,
      provider: null,
      list: false,
      focus: false,
      minimize: false,
      close: false,
      reason: "wmctrl is not installed",
    };
  }
  return {
    nativeShell: true,
    provider: "ewmh-x11",
    list: true,
    focus: true,
    minimize: true,
    close: true,
  };
}

async function listNativeWindows(options = {}) {
  const runtime = resolveRuntime(options);
  const capabilities = getNativeWindowCapabilities(runtime);
  if (!capabilities.list) {
    return {
      ok: false,
      provider: null,
      windows: [],
      error: capabilities.reason,
    };
  }
  if (capabilities.provider === "kwin-wayland") {
    try {
      const response = await runtime.waylandRequestImpl(
        runtime.waylandBridgePath,
        { method: "list" },
      );
      if (!response.ok)
        throw new Error(response.error || "KWin bridge rejected list");
      return {
        ok: true,
        provider: "kwin-wayland",
        windows: parseKWinWindowList(response.windows),
      };
    } catch (error) {
      return {
        ok: false,
        provider: "kwin-wayland",
        windows: [],
        error: String(error && error.message),
      };
    }
  }
  try {
    const [windowOutput, activeOutput] = await Promise.all([
      runFile(runtime.wmctrlPath, ["-l", "-x", "-p"], runtime),
      runtime.xpropPath
        ? runFile(
            runtime.xpropPath,
            ["-root", "_NET_ACTIVE_WINDOW"],
            runtime,
          ).catch(() => "")
        : Promise.resolve(""),
    ]);
    const activeWindow = parseActiveWindow(activeOutput);
    return {
      ok: true,
      provider: "ewmh-x11",
      windows: parseWmctrlWindowList(windowOutput, activeWindow),
    };
  } catch (error) {
    return {
      ok: false,
      provider: "ewmh-x11",
      windows: [],
      error: String(error && error.message),
    };
  }
}

async function runNativeWindowAction(action, windowId, options = {}) {
  const runtime = resolveRuntime(options);
  const capabilities = getNativeWindowCapabilities(runtime);
  if (!capabilities[action]) {
    return {
      ok: false,
      action,
      error:
        capabilities.reason || `native window action unavailable: ${action}`,
    };
  }

  let id;
  try {
    id =
      capabilities.provider === "kwin-wayland"
        ? normalizeKWinWindowId(windowId)
        : normalizeWindowId(windowId);
  } catch (error) {
    return { ok: false, action, error: String(error && error.message) };
  }

  if (capabilities.provider === "kwin-wayland") {
    try {
      const response = await runtime.waylandRequestImpl(
        runtime.waylandBridgePath,
        { method: "action", action, windowId: id },
      );
      return {
        ok: Boolean(response.ok),
        action,
        windowId: id,
        provider: "kwin-wayland",
        ...(response.ok
          ? {}
          : { error: String(response.error || "KWin rejected the action") }),
      };
    } catch (error) {
      return {
        ok: false,
        action,
        windowId: id,
        provider: "kwin-wayland",
        error: String(error && error.message),
      };
    }
  }

  const argsByAction = {
    focus: [
      ["-ir", id, "-b", "remove,hidden"],
      ["-ia", id],
    ],
    minimize: [["-ir", id, "-b", "add,hidden"]],
    close: [["-ic", id]],
  };
  const commands = argsByAction[action];
  if (!commands) return { ok: false, action, error: "unknown window action" };

  try {
    for (const args of commands) {
      await runFile(runtime.wmctrlPath, args, runtime);
    }
    return { ok: true, action, windowId: id, provider: "ewmh-x11" };
  } catch (error) {
    return {
      ok: false,
      action,
      windowId: id,
      provider: "ewmh-x11",
      error: String(error && error.message),
    };
  }
}

function registerNativeWindowsIpc(ipcMain, options = {}) {
  const runtime = () => ({ ...options, nativeShell: options.nativeShell });
  ipcMain.handle("windows:getCapabilities", async () =>
    getNativeWindowCapabilities(runtime()),
  );
  ipcMain.handle("windows:list", async () => listNativeWindows(runtime()));
  for (const action of ["focus", "minimize", "close"]) {
    ipcMain.handle(`windows:${action}`, async (_event, windowId) =>
      runNativeWindowAction(action, windowId, runtime()),
    );
  }
}

module.exports = {
  firstExecutable,
  normalizeKWinWindowId,
  normalizeWindowId,
  parseKWinWindowList,
  parseWmctrlWindowList,
  parseActiveWindow,
  requestKWinBridge,
  getNativeWindowCapabilities,
  listNativeWindows,
  runNativeWindowAction,
  registerNativeWindowsIpc,
};
