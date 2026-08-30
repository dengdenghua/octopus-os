/**
 * Echo desktop shell — main process.
 *
 * Rebuilt 2026-06-13 against the contract in src/types/electron.d.ts
 * (the original electron/ directory was never committed and was lost).
 * See electron/README.md for what is fully implemented vs. stubbed.
 */
const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  net,
  protocol,
  safeStorage,
  shell,
  session,
  systemPreferences,
  webContents,
} = require("electron");
const fs = require("fs");
const fsp = require("fs/promises");
const os = require("os");
const path = require("path");
const { pathToFileURL } = require("url");
const { spawnBackend, killBackend } = require("./backend-runtime.cjs");
const desktopUpdater = require("./desktop-updater.cjs");
const desktopProtocol = require("./desktop-protocol.cjs");
const shellProfile = require("./shell-profile.cjs");
const {
  ensureDesktopConfigFile,
  ensureDesktopResources,
} = require("./desktop-config.cjs");

// Existing installations may still launch Echo with the former environment
// prefix. Promote those values before any module reads process.env.
const legacyEnvironmentPrefix = "OCTO" + "PUS_";
for (const [name, value] of Object.entries(process.env)) {
  if (name.startsWith(legacyEnvironmentPrefix) && value !== undefined) {
    process.env[`ECHO_${name.slice(legacyEnvironmentPrefix.length)}`] ??= value;
  }
}

// 原生 shell(A 路线)系统手层:枚举/启动本地已装应用(freedesktop .desktop)。
const systemShell = require("./system-shell.cjs");
const systemActions = require("./system-actions.cjs");
const systemUpdate = require("./system-update.cjs");
const systemControls = require("./system-controls.cjs");
const systemNotifications = require("./system-notifications.cjs");
const agentService = require("./agent-service.cjs");
const nativeWindows = require("./native-windows.cjs");
const rendererReadiness = require("./renderer-readiness.cjs");
const nativeAppIpcSmoke = require("./native-app-ipc-smoke.cjs");
const {
  NativeLiquidGlassController,
  shouldUseTransparentWindow,
} = require("./native-liquid-glass.cjs");
const { resolveDevURL } = require("./dev-url.cjs");
// `session` 是旧 Cage/kiosk 会话；`desktop` 是目标 C 的 KWin 通用桌面会话。
const SHELL_MODE = process.env.ECHO_SHELL_MODE || "";
const DESKTOP_SESSION = SHELL_MODE === "desktop";
// The Echo OS directory package has a distinct immutable executable identity.
// Bind production behavior to that identity so a missing session environment
// variable can never make the resource-minimal OS shell try to start the
// standalone desktop Agent that it intentionally does not contain.
const PACKAGED_NATIVE_SHELL = shellProfile.isPackagedNativeShell({
  isPackaged: app.isPackaged,
  platform: process.platform,
  execPath: process.execPath,
  resourcesPath: process.resourcesPath,
});
const NATIVE_SHELL =
  PACKAGED_NATIVE_SHELL ||
  process.env.ECHO_NATIVE_SHELL === "1" ||
  SHELL_MODE === "session" ||
  DESKTOP_SESSION;
const KIOSK_SHELL = NATIVE_SHELL && !DESKTOP_SESSION;

const DEV_URL = resolveDevURL();
const DESKTOP_DIR = path.join(os.homedir(), "Desktop");
const SMOKE_TEST_BACKEND = process.argv.includes("--smoke-test-backend");
const BUILT_RENDERER_SMOKE =
  process.argv.includes("--smoke-test") || SMOKE_TEST_BACKEND;

protocol.registerSchemesAsPrivileged([
  {
    scheme: desktopProtocol.DESKTOP_APP_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      stream: true,
      codeCache: true,
    },
  },
]);

if (process.platform === "win32") {
  app.setAppUserModelId("ai.echo.desktop");
}

let mainWindow = null;
let nativeLiquidGlassController = null;
let nativeLiquidGlassSmokeLogged = false;
const BROWSER_PARTITION = "persist:echo-browser";

function browserProfileSession() {
  return session.fromPartition(BROWSER_PARTITION);
}

// ── backend URL ────────────────────────────────────────────────
function resolveBackendBaseURL() {
  return desktopProtocol.normalizeLoopbackBackendBaseURL(
    process.env.ECHO_BACKEND_URL || "http://127.0.0.1:8000",
  );
}

async function waitForSmokeBackendReady(timeoutMilliseconds = 75000) {
  const deadline = Date.now() + timeoutMilliseconds;
  const readyURL = `${resolveBackendBaseURL()}/readyz`;
  let lastError = null;
  while (Date.now() < deadline) {
    const controller = new AbortController();
    const requestTimer = setTimeout(() => controller.abort(), 2000);
    try {
      const response = await net.fetch(readyURL, { signal: controller.signal });
      if (response.ok) {
        await response.text();
        return;
      }
      lastError = new Error(
        `backend readiness returned HTTP ${response.status}`,
      );
    } catch (error) {
      lastError = error;
    } finally {
      clearTimeout(requestTimer);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(
    `desktop backend did not reach /readyz: ${lastError?.message || "timeout"}`,
  );
}

function installDesktopRendererProtocol() {
  const distRoot = path.join(__dirname, "..", "dist");
  if (!desktopProtocol.resolveDesktopAssetPath(distRoot, "/index.html")) {
    throw new Error(`desktop renderer entry is missing from ${distRoot}`);
  }
  const handler = desktopProtocol.createDesktopProtocolHandler({
    distRoot,
    backendBaseURL: resolveBackendBaseURL(),
    fetchImpl: (url, init) => net.fetch(url, init),
    onProxyError: (error) =>
      console.warn("[echo] desktop backend proxy unavailable:", error.message),
  });
  protocol.handle(desktopProtocol.DESKTOP_APP_SCHEME, handler);
}

function backendConfigPath() {
  return path.join(app.getPath("userData"), "config.yaml");
}

function backendProgress({ stage, message }) {
  mainWindow?.webContents.send("backend:bootstrap-progress", {
    stage,
    message,
  });
}

// ── first-launch config (packaging/desktop/config.desktop.yaml) ──
function ensureDesktopConfig() {
  const bundled = app.isPackaged
    ? path.join(process.resourcesPath, "config.desktop.yaml")
    : path.join(
        __dirname,
        "..",
        "..",
        "packaging",
        "desktop",
        "config.desktop.yaml",
      );
  return ensureDesktopConfigFile({
    bundledPath: bundled,
    targetPath: backendConfigPath(),
  });
}

function ensurePackagedResources() {
  return ensureDesktopResources({
    bundledRoot: app.isPackaged
      ? process.resourcesPath
      : path.join(__dirname, "..", ".."),
    targetRoot: path.join(app.getPath("userData"), "resources"),
  });
}

// ── desktop organizer (the 桌面助手 backend) ───────────────────
const journalFile = () =>
  path.join(app.getPath("userData"), "desktop-organizer-journal.json");

function readJournal() {
  try {
    return JSON.parse(fs.readFileSync(journalFile(), "utf8"));
  } catch {
    return [];
  }
}

function writeJournal(entries) {
  fs.writeFileSync(journalFile(), JSON.stringify(entries, null, 2));
}

async function listDesktopItems() {
  const names = await fsp.readdir(DESKTOP_DIR);
  const items = [];
  for (const name of names) {
    if (name.startsWith(".")) continue;
    const p = path.join(DESKTOP_DIR, name);
    let st;
    try {
      st = await fsp.stat(p);
    } catch {
      continue;
    }
    const ext = path.extname(name).replace(/^\./, "").toLowerCase();
    const kind = st.isDirectory()
      ? name.endsWith(".app")
        ? "app"
        : "folder"
      : "file";
    const subtitle = st.isDirectory()
      ? new Date(st.mtimeMs).toLocaleDateString()
      : `${(st.size / 1024).toFixed(0)} KB · ${new Date(st.mtimeMs).toLocaleDateString()}`;
    items.push({ id: p, name, subtitle, path: p, kind, extension: ext });
  }
  return items;
}

async function moveDesktopItem(srcPath, destDir) {
  const dest = path.isAbsolute(destDir)
    ? destDir
    : path.join(DESKTOP_DIR, destDir);
  await fsp.mkdir(dest, { recursive: true });
  const target = path.join(dest, path.basename(srcPath));
  if (fs.existsSync(target)) return { ok: true, skipped: true };
  await fsp.rename(srcPath, target);
  const journal = readJournal();
  journal.push({ from: srcPath, to: target, ts: Date.now() });
  writeJournal(journal);
  return { ok: true, destPath: target };
}

function sampleSystemInfo() {
  const cpus = os.cpus();
  const load = os.loadavg()[0];
  const total = os.totalmem();
  const free = os.freemem();
  return {
    ok: true,
    cpu: {
      model: cpus[0]?.model || "unknown",
      cores: cpus.length,
      usage: Math.min(100, Math.round((load / Math.max(1, cpus.length)) * 100)),
    },
    memory: {
      total,
      used: total - free,
      percent: Math.round(((total - free) / total) * 100),
    },
    uptime: os.uptime(),
    platform: process.platform,
  };
}

// ── browser bridge (embedded <webview> automation) ─────────────
function wc(webContentsId) {
  const target = webContents.fromId(webContentsId);
  if (!target || target.isDestroyed())
    throw new Error(`webContents ${webContentsId} not found`);
  if (target.getType() !== "webview") {
    throw new Error(`webContents ${webContentsId} is not a webview`);
  }
  return target;
}

const js = {
  click: (sel) => `(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return { ok: false, error: "selector not found" };
    el.scrollIntoView({ block: "center" });
    el.click();
    return { ok: true, tag: el.tagName.toLowerCase(), text: (el.textContent || "").trim().slice(0, 120) };
  })()`,
  type: (sel, text, clear) => `(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return { ok: false, error: "selector not found" };
    el.focus();
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement : HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(proto.prototype, "value")?.set;
    const next = ${JSON.stringify(!!clear)} ? ${JSON.stringify(text)} : (el.value || "") + ${JSON.stringify(text)};
    if (setter) setter.call(el, next); else el.value = next;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, value: el.value };
  })()`,
  hover: (sel) => `(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return { ok: false, error: "selector not found" };
    el.scrollIntoView({ block: "center" });
    for (const t of ["pointerover", "mouseover", "mouseenter"])
      el.dispatchEvent(new MouseEvent(t, { bubbles: true }));
    return { ok: true };
  })()`,
  scroll: (opts) => `(() => {
    const o = ${JSON.stringify(opts)};
    const el = o.selector ? document.querySelector(o.selector) : null;
    if (o.selector && !el) return { ok: false, error: "selector not found" };
    (el || window).scrollBy({ left: o.deltaX || 0, top: o.deltaY || 0, behavior: "instant" });
    return { ok: true, y: el ? el.scrollTop : window.scrollY };
  })()`,
  waitFor: (sel, timeout) => `new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      if (document.querySelector(${JSON.stringify(sel)}))
        return resolve({ ok: true, elapsed: Date.now() - t0 });
      if (Date.now() - t0 > ${Number(timeout) || 10000})
        return resolve({ ok: false, error: "timeout", elapsed: Date.now() - t0 });
      setTimeout(tick, 100);
    };
    tick();
  })`,
  extractText: `(() => {
    const text = document.body ? document.body.innerText : "";
    const max = 200000;
    return {
      url: location.href,
      title: document.title,
      text: text.slice(0, max),
      truncated: text.length > max,
      textLength: text.length,
    };
  })()`,
};

const DEVICE_PRESETS = {
  mobile: {
    width: 390,
    height: 844,
    dpr: 3,
    mobile: true,
    ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  },
  tablet: {
    width: 820,
    height: 1180,
    dpr: 2,
    mobile: true,
    ua: "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  },
};

async function getAriaTree(target, opts) {
  const attachedHere = !target.debugger.isAttached();
  if (attachedHere) target.debugger.attach("1.3");
  try {
    const { nodes } = await target.debugger.sendCommand(
      "Accessibility.getFullAXTree",
      {
        max_depth: opts?.maxDepth,
      },
    );
    return {
      ok: true,
      nodes: nodes.map((n) => ({
        id: String(n.nodeId),
        role: n.role?.value ?? "",
        name: n.name?.value ?? "",
        value: n.value?.value ?? "",
        backendDOMNodeId: n.backendDOMNodeId,
        childIds: (n.childIds || []).map(String),
        ignored: !!n.ignored,
      })),
    };
  } finally {
    if (attachedHere) {
      try {
        target.debugger.detach();
      } catch {
        /* already detached */
      }
    }
  }
}

const passwordVaultFile = () =>
  path.join(app.getPath("userData"), "browser-passwords.enc.json");

function normalizeHttpOrigin(value) {
  const parsed = new URL(String(value || ""));
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("only http(s) origins are supported");
  }
  return parsed.origin;
}

function passwordVaultAvailable() {
  return safeStorage.isEncryptionAvailable();
}

function readPasswordVault() {
  if (!passwordVaultAvailable()) return [];
  try {
    const envelope = JSON.parse(fs.readFileSync(passwordVaultFile(), "utf8"));
    if (envelope?.version !== 1 || typeof envelope.payload !== "string") {
      return [];
    }
    const clear = safeStorage.decryptString(
      Buffer.from(envelope.payload, "base64"),
    );
    const entries = JSON.parse(clear);
    return Array.isArray(entries) ? entries : [];
  } catch (error) {
    if (error?.code !== "ENOENT") {
      console.warn("[echo] password vault could not be read:", error.message);
    }
    return [];
  }
}

function writePasswordVault(entries) {
  if (!passwordVaultAvailable()) {
    throw new Error("system encryption is unavailable");
  }
  const target = passwordVaultFile();
  const temporary = `${target}.tmp`;
  const payload = safeStorage
    .encryptString(JSON.stringify(entries))
    .toString("base64");
  fs.writeFileSync(temporary, JSON.stringify({ version: 1, payload }), {
    mode: 0o600,
  });
  fs.renameSync(temporary, target);
}

function publicPasswordEntry(entry) {
  return {
    id: entry.id,
    origin: entry.origin,
    username: entry.username,
    updatedAt: entry.updatedAt,
  };
}

const sitePermissionsFile = () =>
  path.join(app.getPath("userData"), "browser-site-permissions.json");

function readSitePermissions() {
  try {
    const entries = JSON.parse(fs.readFileSync(sitePermissionsFile(), "utf8"));
    return Array.isArray(entries) ? entries : [];
  } catch (error) {
    if (error?.code !== "ENOENT") {
      console.warn("[echo] site permissions could not be read:", error.message);
    }
    return [];
  }
}

function writeSitePermissions(entries) {
  fs.writeFileSync(sitePermissionsFile(), JSON.stringify(entries, null, 2), {
    mode: 0o600,
  });
}

function permissionKey(permission, details = {}) {
  if (permission === "geolocation") return "location";
  if (permission === "notifications") return "notifications";
  if (permission === "clipboard-read") return "clipboard";
  if (permission !== "media") return null;
  const mediaTypes = Array.isArray(details.mediaTypes)
    ? details.mediaTypes
    : [];
  const audio = mediaTypes.includes("audio");
  const video = mediaTypes.includes("video");
  if (audio && video) return "camera-microphone";
  if (video) return "camera";
  if (audio) return "microphone";
  return null;
}

const permissionLabels = {
  camera: "摄像头",
  microphone: "麦克风",
  "camera-microphone": "摄像头和麦克风",
  location: "位置信息",
  notifications: "通知",
  clipboard: "剪贴板读取",
};

function savedSitePermission(origin, permission) {
  return readSitePermissions().find(
    (entry) => entry.origin === origin && entry.permission === permission,
  );
}

function saveSitePermission(origin, permission, decision) {
  const entries = readSitePermissions().filter(
    (entry) => !(entry.origin === origin && entry.permission === permission),
  );
  if (decision !== "ask") {
    entries.unshift({ origin, permission, decision, updatedAt: Date.now() });
  }
  writeSitePermissions(entries);
}

function configureBrowserPermissionRequests(sess) {
  sess.setPermissionRequestHandler(
    (contents, permission, callback, details) => {
      if (contents.getType() !== "webview") {
        callback(false);
        return;
      }
      const key = permissionKey(permission, details);
      if (!key) {
        callback(false);
        return;
      }
      let origin;
      try {
        origin = normalizeHttpOrigin(
          details?.requestingUrl ||
            details?.securityOrigin ||
            contents.getURL(),
        );
      } catch {
        callback(false);
        return;
      }
      const saved = savedSitePermission(origin, key);
      if (saved) {
        callback(saved.decision === "allow");
        return;
      }
      void dialog
        .showMessageBox(mainWindow, {
          type: "question",
          title: "网站权限请求",
          message: `${origin} 想要使用${permissionLabels[key]}`,
          detail:
            "只在确认网站可信且当前功能确实需要时允许。你可以稍后在“浏览器数据与隐私”中撤销。",
          buttons: ["允许", "阻止"],
          defaultId: 1,
          cancelId: 1,
          checkboxLabel: "记住此网站的选择",
          checkboxChecked: false,
          noLink: true,
        })
        .then(({ response, checkboxChecked }) => {
          const allow = response === 0;
          if (checkboxChecked) {
            saveSitePermission(origin, key, allow ? "allow" : "block");
          }
          callback(allow);
        })
        .catch(() => callback(false));
    },
  );
}

function downloadRisk(filename) {
  const lower = String(filename || "").toLowerCase();
  if (
    /\.(exe|msi|msp|bat|cmd|com|scr|ps1|vbs|vbe|js|jse|wsf|wsh|reg|app|dmg|pkg|deb|rpm|apk)$/i.test(
      lower,
    )
  ) {
    return "high";
  }
  if (/\.(zip|rar|7z|tar|gz|bz2|xz|iso)$/i.test(lower)) return "medium";
  return "low";
}

// ── downloads ──────────────────────────────────────────────────
const downloads = new Map();
let downloadSeq = 0;

function trackDownloads(sess) {
  sess.on("will-download", (_event, item, sourceContents) => {
    const id = `dl-${++downloadSeq}`;
    const createdAt = Date.now();
    const filename = item.getFilename();
    const risk = downloadRisk(filename);
    const url = item.getURL();
    let sourceOrigin = "";
    try {
      sourceOrigin = normalizeHttpOrigin(sourceContents?.getURL() || url);
    } catch {
      sourceOrigin = "unknown";
    }
    const send = (state) => {
      downloads.set(id, {
        item,
        path: item.getSavePath(),
        url,
        filename,
        risk,
        sourceOrigin,
        createdAt,
        send,
        sourceContents,
      });
      mainWindow?.webContents.send("browser:download-event", {
        id,
        filename,
        url,
        sourceOrigin,
        risk,
        state: state === "started" ? "progressing" : state,
        paused: item.isPaused(),
        canResume: item.canResume(),
        receivedBytes: item.getReceivedBytes(),
        totalBytes: item.getTotalBytes(),
        createdAt,
      });
    };
    send("started");
    item.on("updated", (_e, state) => send(state));
    item.once("done", (_e, state) => send(state));
    if (risk === "high") {
      item.pause();
      send("progressing");
      void dialog
        .showMessageBox(mainWindow, {
          type: "warning",
          title: "高风险下载",
          message: `是否保留 ${filename}？`,
          detail: `此文件可能运行程序或修改设备。来源：${sourceOrigin}`,
          buttons: ["继续下载", "取消下载"],
          defaultId: 1,
          cancelId: 1,
          noLink: true,
        })
        .then(({ response }) => {
          if (response === 0) item.resume();
          else item.cancel();
        })
        .catch(() => item.cancel());
    }
  });
}

// ── extensions ─────────────────────────────────────────────────
const extensionsFile = () =>
  path.join(app.getPath("userData"), "extensions.json");

function readExtensionRegistry() {
  try {
    return JSON.parse(fs.readFileSync(extensionsFile(), "utf8"));
  } catch {
    return [];
  }
}

function writeExtensionRegistry(list) {
  fs.writeFileSync(extensionsFile(), JSON.stringify(list, null, 2));
}

async function loadEnabledExtensions() {
  for (const ext of readExtensionRegistry()) {
    if (!ext.enabled) continue;
    try {
      await session.defaultSession.loadExtension(ext.path);
    } catch (err) {
      console.warn(`[echo] extension ${ext.name} failed to load:`, err.message);
    }
  }
}

// ── IPC wiring ─────────────────────────────────────────────────
function registerIpc() {
  const handle = (channel, fn) =>
    ipcMain.handle(channel, async (_event, ...args) => fn(...args));

  // app
  handle("app:getVersion", () => app.getVersion());
  handle("app:openExternal", (url) => shell.openExternal(url));
  handle("app:getPlatform", () => process.platform);

  // macOS 26+: only bounded surface geometry crosses this bridge. The renderer
  // cannot select native classes, filesystem paths or private material flags.
  ipcMain.handle("nativeGlass:getCapabilities", (event) => {
    if (event.sender !== mainWindow?.webContents) {
      return {
        supported: false,
        reason: "untrusted-renderer",
        material: null,
        backend: null,
      };
    }
    return (
      nativeLiquidGlassController?.getCapabilities() || {
        supported: false,
        reason: "window-unavailable",
        material: null,
        backend: null,
      }
    );
  });
  ipcMain.handle("nativeGlass:sync", async (event, payload) => {
    if (event.sender !== mainWindow?.webContents) {
      return {
        active: false,
        supported: false,
        reason: "untrusted-renderer",
        material: null,
        backend: null,
        surfaceCount: 0,
      };
    }
    const result = (await nativeLiquidGlassController?.sync(payload)) || {
      active: false,
      supported: false,
      reason: "window-unavailable",
      material: null,
      backend: null,
      surfaceCount: 0,
    };
    if (
      result.active &&
      !nativeLiquidGlassSmokeLogged &&
      process.env.ECHO_NATIVE_GLASS_SMOKE === "1"
    ) {
      nativeLiquidGlassSmokeLogged = true;
      console.log(
        `ECHO_NATIVE_LIQUID_GLASS_READY ${result.material} ${result.surfaceCount}`,
      );
      console.log(
        "ECHO_NATIVE_LIQUID_GLASS_SCENE",
        JSON.stringify(nativeLiquidGlassController?.diagnostics || {}),
      );
    }
    return result;
  });
  ipcMain.handle("nativeGlass:deactivate", (event) => {
    if (event.sender !== mainWindow?.webContents) return { active: false };
    return nativeLiquidGlassController?.deactivate() || { active: false };
  });

  // Echo OS 电源动作：双重限制为原生 Linux 会话 + 固定 systemctl 白名单。
  handle("system:getCapabilities", () =>
    systemActions.getSystemActionCapabilities({
      nativeShell: NATIVE_SHELL,
    }),
  );
  handle("system:runAction", (action) =>
    systemActions.runSystemAction(action, { nativeShell: NATIVE_SHELL }),
  );

  // Signed OS updates: read one bounded public state and invoke one fixed
  // PolicyKit helper. No renderer-selected command, path or argument crosses.
  handle("updates:getCapabilities", () =>
    systemUpdate.getSystemUpdateCapabilities({ nativeShell: NATIVE_SHELL }),
  );
  handle("updates:getStatus", () =>
    systemUpdate.getSystemUpdateStatus({ nativeShell: NATIVE_SHELL }),
  );
  handle("updates:apply", () =>
    systemUpdate.applySystemUpdate({ nativeShell: NATIVE_SHELL }),
  );

  // Real NetworkManager/BlueZ/PipeWire/backlight state for the control center.
  handle("controls:getState", () =>
    systemControls.getSystemControlState({ nativeShell: NATIVE_SHELL }),
  );
  handle("controls:setWifiEnabled", (enabled) =>
    systemControls.setWifiEnabled(enabled, { nativeShell: NATIVE_SHELL }),
  );
  handle("controls:setBluetoothEnabled", (enabled) =>
    systemControls.setBluetoothEnabled(enabled, { nativeShell: NATIVE_SHELL }),
  );
  handle("controls:setAudioVolume", (percentage) =>
    systemControls.setAudioVolume(percentage, { nativeShell: NATIVE_SHELL }),
  );
  handle("controls:setDisplayBrightness", (percentage) =>
    systemControls.setDisplayBrightness(percentage, {
      nativeShell: NATIVE_SHELL,
    }),
  );

  // org.freedesktop.Notifications is owned by the supervised Echo session
  // daemon. The renderer sees only bounded JSON over its private 0600 socket.
  handle("notifications:getCapabilities", () =>
    systemNotifications.getNotificationCapabilities({
      nativeShell: NATIVE_SHELL,
    }),
  );
  handle("notifications:list", () =>
    systemNotifications.listNotifications({ nativeShell: NATIVE_SHELL }),
  );
  handle("notifications:close", (notificationId) =>
    systemNotifications.closeNotification(notificationId, {
      nativeShell: NATIVE_SHELL,
    }),
  );
  handle("notifications:clear", () =>
    systemNotifications.clearNotifications({ nativeShell: NATIVE_SHELL }),
  );

  // 目标 C:窗口管理器是真实窗口状态源；IPC 本身与 KWin/EWMH provider 解耦。
  nativeWindows.registerNativeWindowsIpc(ipcMain, {
    nativeShell: NATIVE_SHELL,
  });

  // 原生 shell:本地已装应用 枚举/启动(window.echo.apps.*)
  systemShell.registerSystemShellIpc(ipcMain);

  // dialog
  handle("dialog:open", (options) =>
    dialog.showOpenDialog(mainWindow, options),
  );
  handle("dialog:save", (options) =>
    dialog.showSaveDialog(mainWindow, options),
  );

  // backend
  handle("backend:getBaseURL", () => resolveBackendBaseURL());
  ipcMain.on("backend:getBaseURLSync", (event) => {
    event.returnValue = resolveBackendBaseURL();
  });
  handle("backend:restart", async () => {
    if (NATIVE_SHELL || !app.isPackaged) {
      return agentService.restartAgentService({ nativeShell: NATIVE_SHELL });
    }
    await killBackend();
    await spawnBackend(backendConfigPath(), backendProgress);
    return { ok: true };
  });

  // window
  handle("window:setDeviceBounds", (mode, width, height) => {
    if (!mainWindow) return { ok: false, reason: "no window" };
    if (width && height)
      mainWindow.setContentSize(Math.round(width), Math.round(height));
    return { ok: true, mode };
  });
  handle("window:setTitleBarOverlay", (opts) => {
    try {
      if (process.platform === "win32" && mainWindow?.setTitleBarOverlay) {
        mainWindow.setTitleBarOverlay({
          color: opts.color,
          symbolColor: opts.symbolColor,
          height: 36,
        });
      }
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("window:setMousePassthrough", (enabled) => {
    if (!mainWindow) return { ok: false, error: "no window" };
    mainWindow.setIgnoreMouseEvents(!!enabled, { forward: true });
    return { ok: true, enabled: !!enabled };
  });
  handle("window:openDevTools", () => {
    mainWindow?.webContents.openDevTools({ mode: "detach" });
    return { ok: true };
  });
  handle("window:isFullScreen", () => ({
    ok: true,
    fullScreen: mainWindow?.isFullScreen() ?? false,
  }));

  // desktop organizer
  handle("desktop:getAutomationPermissions", () => {
    if (process.platform !== "darwin") {
      return {
        supported: false,
        platform: process.platform,
        screenRecording: "unknown",
        accessibility: "unknown",
      };
    }
    const screenRecording = systemPreferences.getMediaAccessStatus("screen");
    return {
      supported: true,
      platform: process.platform,
      screenRecording: ["granted", "denied", "restricted"].includes(
        screenRecording,
      )
        ? screenRecording
        : "unknown",
      accessibility: systemPreferences.isTrustedAccessibilityClient(false)
        ? "granted"
        : "denied",
    };
  });
  handle("desktop:openAutomationPermission", async (permission) => {
    if (process.platform !== "darwin") {
      return { ok: false, error: "macOS only" };
    }
    const pane =
      permission === "screen-recording"
        ? "Privacy_ScreenCapture"
        : permission === "accessibility"
          ? "Privacy_Accessibility"
          : "";
    if (!pane) return { ok: false, error: "unknown permission" };
    try {
      await shell.openExternal(
        `x-apple.systempreferences:com.apple.preference.security?${pane}`,
      );
      return { ok: true };
    } catch (error) {
      return {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  });
  handle("desktop:listItems", async () => {
    try {
      return {
        ok: true,
        desktopPath: DESKTOP_DIR,
        items: await listDesktopItems(),
      };
    } catch (err) {
      return { ok: false, items: [], error: err.message };
    }
  });
  handle("desktop:openItem", async (p) => {
    const error = await shell.openPath(p);
    return error ? { ok: false, error } : { ok: true };
  });
  handle("desktop:moveItem", async (srcPath, destDir) => {
    try {
      return await moveDesktopItem(srcPath, destDir);
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("desktop:moveItemsBatch", async (items) => {
    let moved = 0;
    let skipped = 0;
    try {
      for (const { srcPath, category } of items) {
        const res = await moveDesktopItem(srcPath, category);
        if (res.skipped) skipped += 1;
        else moved += 1;
      }
      return { ok: true, moved, skipped };
    } catch (err) {
      return { ok: false, moved, skipped, error: err.message };
    }
  });
  handle("desktop:undoMoves", async () => {
    const journal = readJournal();
    let undone = 0;
    for (const entry of journal.reverse()) {
      try {
        if (fs.existsSync(entry.to) && !fs.existsSync(entry.from)) {
          await fsp.rename(entry.to, entry.from);
          undone += 1;
        }
      } catch {
        /* keep undoing the rest */
      }
    }
    writeJournal([]);
    return { ok: true, undone };
  });
  handle("desktop:getSystemInfo", () => sampleSystemInfo());
  handle("desktop:installContextMenu", () => ({
    ok: false,
    error:
      "right-click menu integration was lost with the original shell; not reimplemented yet (Windows-only feature)",
  }));
  handle("desktop:removeContextMenu", () => ({ ok: true }));

  // embedded browser
  handle("browser:setDevice", async (id, mode) => {
    const target = wc(id);
    const preset = DEVICE_PRESETS[mode];
    if (!preset) {
      target.disableDeviceEmulation();
      target.setUserAgent(target.session.getUserAgent());
      return { ok: true, mode };
    }
    target.enableDeviceEmulation({
      screenPosition: "mobile",
      screenSize: { width: preset.width, height: preset.height },
      viewSize: { width: preset.width, height: preset.height },
      deviceScaleFactor: preset.dpr,
    });
    target.setUserAgent(preset.ua);
    return { ok: true, mode };
  });
  handle("browser:executeJS", (id, code) =>
    wc(id).executeJavaScript(code, true),
  );
  handle("browser:reload", (id) => wc(id).reload());
  handle("browser:goBack", (id) => wc(id).navigationHistory.goBack());
  handle("browser:goForward", (id) => wc(id).navigationHistory.goForward());
  handle("browser:openDevTools", (id) => {
    try {
      wc(id).openDevTools({ mode: "detach" });
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:capturePage", async (id) => {
    const image = await wc(id).capturePage();
    const size = image.getSize();
    return {
      dataUrl: image.toDataURL(),
      width: size.width,
      height: size.height,
    };
  });
  handle("browser:extractText", (id) =>
    wc(id).executeJavaScript(js.extractText, true),
  );
  handle("browser:click", (id, sel) =>
    wc(id).executeJavaScript(js.click(sel), true),
  );
  handle("browser:type", (id, sel, text, opts) =>
    wc(id).executeJavaScript(js.type(sel, text, opts?.clear), true),
  );
  handle("browser:hover", (id, sel) =>
    wc(id).executeJavaScript(js.hover(sel), true),
  );
  handle("browser:scroll", (id, opts) =>
    wc(id).executeJavaScript(js.scroll(opts), true),
  );
  handle("browser:waitFor", (id, sel, timeout) =>
    wc(id).executeJavaScript(js.waitFor(sel, timeout), true),
  );
  handle("browser:pressKey", (id, key) => {
    const target = wc(id);
    target.focus();
    target.sendInputEvent({ type: "keyDown", keyCode: key });
    if (key.length === 1) target.sendInputEvent({ type: "char", keyCode: key });
    target.sendInputEvent({ type: "keyUp", keyCode: key });
    return { ok: true, key };
  });
  handle("browser:getAriaTree", async (id, opts) => {
    try {
      return await getAriaTree(wc(id), opts);
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:getCurrentUrl", (id) => {
    const target = wc(id);
    return { ok: true, url: target.getURL(), title: target.getTitle() };
  });
  handle("browser:clearSiteData", async (id) => {
    try {
      const target = wc(id);
      const origin = new URL(target.getURL()).origin;
      await target.session.clearStorageData({ origin });
      return { ok: true, origin };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:clearBrowsingData", async () => {
    try {
      const sessions = [session.defaultSession, browserProfileSession()];
      await Promise.all(
        sessions.flatMap((browserSession) => [
          browserSession.clearStorageData(),
          browserSession.clearCache(),
          browserSession.clearAuthCache(),
        ]),
      );
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:listPasswords", (origin) => {
    try {
      const normalized = origin ? normalizeHttpOrigin(origin) : null;
      const entries = readPasswordVault()
        .filter((entry) => !normalized || entry.origin === normalized)
        .map(publicPasswordEntry)
        .sort((a, b) => b.updatedAt - a.updatedAt);
      return { ok: true, available: passwordVaultAvailable(), entries };
    } catch (err) {
      return {
        ok: false,
        available: passwordVaultAvailable(),
        entries: [],
        error: err.message,
      };
    }
  });
  handle("browser:savePassword", (entry) => {
    try {
      const origin = normalizeHttpOrigin(entry?.origin);
      const username = String(entry?.username || "").trim();
      const password = String(entry?.password || "");
      if (!username || !password)
        throw new Error("username and password required");
      const entries = readPasswordVault();
      const existing = entries.find(
        (item) => item.origin === origin && item.username === username,
      );
      const next = {
        id:
          existing?.id ||
          `pwd-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`,
        origin,
        username,
        password,
        updatedAt: Date.now(),
      };
      writePasswordVault([
        next,
        ...entries.filter((item) => item.id !== next.id),
      ]);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:deletePassword", (id) => {
    try {
      const entries = readPasswordVault();
      const next = entries.filter((entry) => entry.id !== id);
      if (next.length === entries.length) {
        return { ok: false, error: "password entry not found" };
      }
      writePasswordVault(next);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:fillPassword", async (id, entryId) => {
    try {
      const target = wc(id);
      const currentOrigin = normalizeHttpOrigin(target.getURL());
      const entry = readPasswordVault().find((item) => item.id === entryId);
      if (!entry) return { ok: false, error: "password entry not found" };
      if (entry.origin !== currentOrigin) {
        return {
          ok: false,
          error: "current site does not match password origin",
        };
      }
      const result = await target.executeJavaScript(
        `(() => {
          const username = ${JSON.stringify(entry.username)};
          const password = ${JSON.stringify(entry.password)};
          const passwordInput = document.querySelector('input[type="password"]');
          if (!(passwordInput instanceof HTMLInputElement)) {
            return { ok: false, error: "password field not found" };
          }
          const form = passwordInput.form;
          const scope = form || document;
          const usernameInput = scope.querySelector(
            'input[autocomplete="username"], input[type="email"], input[name*="user" i], input[name*="email" i], input[type="text"]'
          );
          const setValue = (input, value) => {
            const setter = Object.getOwnPropertyDescriptor(
              HTMLInputElement.prototype, "value"
            )?.set;
            if (setter) setter.call(input, value); else input.value = value;
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
          };
          if (usernameInput instanceof HTMLInputElement) setValue(usernameInput, username);
          setValue(passwordInput, password);
          passwordInput.focus();
          return { ok: true, usernameFilled: usernameInput instanceof HTMLInputElement };
        })()`,
        true,
      );
      return result?.ok ? { ok: true } : result;
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:listSitePermissions", () => ({
    ok: true,
    entries: readSitePermissions().sort((a, b) => b.updatedAt - a.updatedAt),
  }));
  handle("browser:setSitePermission", (origin, permission, decision) => {
    try {
      const normalized = normalizeHttpOrigin(origin);
      if (!Object.hasOwn(permissionLabels, permission)) {
        throw new Error("unsupported site permission");
      }
      if (!["ask", "allow", "block"].includes(decision)) {
        throw new Error("unsupported permission decision");
      }
      saveSitePermission(normalized, permission, decision);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  handle("browser:showDownloadInFolder", (id) => {
    const dl = downloads.get(id);
    if (!dl?.path) return { ok: false, error: "download not found" };
    shell.showItemInFolder(dl.path);
    return { ok: true };
  });
  handle("browser:openDownload", async (id) => {
    const dl = downloads.get(id);
    if (!dl?.path) return { ok: false, error: "download not found" };
    const error = await shell.openPath(dl.path);
    return error ? { ok: false, error } : { ok: true };
  });

  handle("browser:pauseDownload", (id) => {
    const dl = downloads.get(id);
    if (!dl?.item) return { ok: false, error: "download not found" };
    try {
      dl.item.pause();
      dl.send?.("progressing");
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:resumeDownload", (id) => {
    const dl = downloads.get(id);
    if (!dl?.item) return { ok: false, error: "download not found" };
    try {
      if (!dl.item.canResume()) {
        return { ok: false, error: "download cannot be resumed" };
      }
      dl.item.resume();
      dl.send?.("progressing");
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:cancelDownload", (id) => {
    const dl = downloads.get(id);
    if (!dl?.item) return { ok: false, error: "download not found" };
    try {
      dl.item.cancel();
      dl.send?.("cancelled");
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("browser:retryDownload", (id) => {
    const dl = downloads.get(id);
    if (!dl?.url) return { ok: false, error: "download not found" };
    try {
      const source =
        dl.sourceContents && !dl.sourceContents.isDestroyed()
          ? dl.sourceContents
          : mainWindow.webContents;
      source.downloadURL(dl.url);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // extensions
  handle("extensions:list", () => ({
    ok: true,
    extensions: readExtensionRegistry(),
  }));
  handle("extensions:installFromFolder", async () => {
    const picked = await dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"],
    });
    if (picked.canceled || !picked.filePaths[0])
      return { ok: false, canceled: true };
    const dir = picked.filePaths[0];
    try {
      const manifest = JSON.parse(
        await fsp.readFile(path.join(dir, "manifest.json"), "utf8"),
      );
      const loaded = await session.defaultSession.loadExtension(dir);
      const info = {
        id: loaded.id,
        name: manifest.name || path.basename(dir),
        version: manifest.version || "0.0.0",
        description: manifest.description || "",
        manifestVersion: manifest.manifest_version || 3,
        path: dir,
        enabled: true,
        installedAt: new Date().toISOString(),
      };
      const registry = readExtensionRegistry().filter((e) => e.path !== dir);
      registry.push(info);
      writeExtensionRegistry(registry);
      return { ok: true, extension: info };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("extensions:setEnabled", async (id, enabled) => {
    const registry = readExtensionRegistry();
    const ext = registry.find((e) => e.id === id);
    if (!ext) return { ok: false, error: "extension not found" };
    try {
      if (enabled) {
        const loaded = await session.defaultSession.loadExtension(ext.path);
        ext.id = loaded.id;
      } else {
        session.defaultSession.removeExtension(id);
      }
      ext.enabled = enabled;
      writeExtensionRegistry(registry);
      return { ok: true, extension: ext };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
  handle("extensions:remove", (id) => {
    try {
      session.defaultSession.removeExtension(id);
    } catch {
      /* may not be loaded */
    }
    writeExtensionRegistry(readExtensionRegistry().filter((e) => e.id !== id));
    return { ok: true };
  });

  // active-tab bridge (fire-and-forget from renderer)
  ipcMain.on("bridge:setActiveTab", () => {
    /* reserved for tab-aware main-process features */
  });
}

// ── window ─────────────────────────────────────────────────────
async function createLinuxGlassBackgroundScene(hostWindow, wallpaperPath) {
  const backgroundWindow = new BrowserWindow({
    ...hostWindow.getBounds(),
    title: "Echo Liquid Glass Background",
    show: false,
    frame: false,
    focusable: false,
    skipTaskbar: true,
    autoHideMenuBar: true,
    hasShadow: false,
    backgroundColor: "#0b1027",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  backgroundWindow.setIgnoreMouseEvents(true);
  backgroundWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));

  const syncBounds = () => {
    if (!hostWindow.isDestroyed() && !backgroundWindow.isDestroyed()) {
      backgroundWindow.setBounds(hostWindow.getBounds(), false);
    }
  };
  hostWindow.on("move", syncBounds);
  hostWindow.on("resize", syncBounds);

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    hostWindow.removeListener("move", syncBounds);
    hostWindow.removeListener("resize", syncBounds);
    if (!backgroundWindow.isDestroyed()) backgroundWindow.destroy();
  };
  hostWindow.once("closed", close);

  try {
    await backgroundWindow.loadURL(pathToFileURL(wallpaperPath).href);
    await backgroundWindow.webContents.insertCSS(`
      html, body {
        width: 100%; height: 100%; margin: 0; overflow: hidden;
        background: #0b1027 !important;
      }
      img {
        display: block !important; width: 100vw !important;
        height: 100vh !important; max-width: none !important;
        max-height: none !important; object-fit: cover !important;
        object-position: center center !important;
      }
    `);
    syncBounds();
    backgroundWindow.showInactive();
    try {
      hostWindow.moveAbove(backgroundWindow.getMediaSourceId());
    } catch {
      // KWin's below rule still keeps both Echo desktop windows under apps.
    }
    return {
      visible: backgroundWindow.isVisible(),
      close,
    };
  } catch (error) {
    close();
    throw error;
  }
}

function createMainWindow() {
  const nativeTransparency = shouldUseTransparentWindow();
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    show: false,
    title: "Echo Desktop",
    ...(nativeTransparency
      ? { transparent: true, backgroundColor: "#00000000" }
      : {}),
    // 旧会话 shell 仍可 kiosk 独占。目标 C 的 desktop 会话必须允许 KWin
    // 在它上方管理真实应用窗口，因此只做无框最大化，不进入 kiosk/fullscreen。
    ...(KIOSK_SHELL
      ? { fullscreen: true, frame: false, kiosk: true, autoHideMenuBar: true }
      : {}),
    ...(DESKTOP_SESSION ? { frame: false, autoHideMenuBar: true } : {}),
    ...(process.platform === "win32" && !KIOSK_SHELL
      ? { titleBarStyle: "hidden", titleBarOverlay: { height: 36 } }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
    },
  });

  if (nativeTransparency) win.setBackgroundColor("#00000000");
  nativeLiquidGlassController = new NativeLiquidGlassController({
    window: win,
    packaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    createLinuxScene: (wallpaperPath) =>
      createLinuxGlassBackgroundScene(win, wallpaperPath),
  });
  win.once("closed", () => {
    nativeLiquidGlassController?.deactivate();
    nativeLiquidGlassController = null;
  });

  win.once("ready-to-show", () => {
    if (DESKTOP_SESSION) {
      win.maximize();
      win.setSkipTaskbar(true);
    }
    win.show();
  });
  win.on("enter-full-screen", () => {
    win.webContents.send("window:fullscreen-changed", { fullScreen: true });
  });
  win.on("leave-full-screen", () => {
    win.webContents.send("window:fullscreen-changed", { fullScreen: false });
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  if (DESKTOP_SESSION) {
    win.webContents.once("did-finish-load", () => {
      console.log("ECHO_RENDERER_READY", win.webContents.getURL());
      rendererReadiness.publishRendererReadyFile({
        desktopSession: DESKTOP_SESSION,
      });
      void systemControls
        .getSystemControlState({ nativeShell: NATIVE_SHELL })
        .then((state) => {
          const marker = systemControls.formatSystemControlsReadyMarker(state);
          if (marker) console.log(marker);
        })
        .catch(() => {
          console.error("[echo] native system-control readiness probe failed");
        });
    });
  }

  // Packaged/native sessions use a fixed secure origin. API traffic is
  // proxied narrowly to the loopback backend by desktop-protocol.cjs.
  const useBuiltRenderer =
    app.isPackaged || NATIVE_SHELL || BUILT_RENDERER_SMOKE;
  if (useBuiltRenderer) {
    const entry = DESKTOP_SESSION
      ? `${desktopProtocol.DESKTOP_APP_ENTRY_URL}#/desktop`
      : desktopProtocol.DESKTOP_APP_ENTRY_URL;
    win.loadURL(entry).catch((error) => {
      console.error("[echo] desktop renderer failed to load:", error);
    });
  } else {
    win.loadURL(DEV_URL);
    win.webContents.on("did-fail-load", (_e, code, desc) => {
      console.error(
        `[echo] dev server not reachable (${code} ${desc}); retrying in 1s…`,
      );
      setTimeout(() => win.loadURL(DEV_URL), 1000);
    });
  }
  return win;
}

function watchDesktop() {
  try {
    let timer = null;
    fs.watch(DESKTOP_DIR, () => {
      clearTimeout(timer);
      timer = setTimeout(
        () => mainWindow?.webContents.send("desktop:items-changed"),
        500,
      );
    });
  } catch (err) {
    console.warn("[echo] desktop watch unavailable:", err.message);
  }
}

// ── lifecycle ──────────────────────────────────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  let backendShutdownStarted = false;
  let backendShutdownComplete = false;
  let updaterController = null;
  const finishAfterBackendShutdown = (completion) => {
    if (backendShutdownComplete) {
      completion();
      return;
    }
    if (backendShutdownStarted) return;
    backendShutdownStarted = true;
    updaterController?.dispose();
    void killBackend().finally(() => {
      backendShutdownComplete = true;
      completion();
    });
  };
  app.on("before-quit", (event) => {
    if (backendShutdownComplete) return;
    event.preventDefault();
    finishAfterBackendShutdown(() => app.quit());
  });

  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.on("open-url", (_event, url) => {
    mainWindow?.webContents.send("app:deep-link", url);
  });

  app.on("web-contents-created", (_event, contents) => {
    if (contents.getType() !== "webview") return;
    contents.setWindowOpenHandler(({ url }) => {
      mainWindow?.webContents.send("browser:open-tab", url);
      return { action: "deny" };
    });
    contents.on("render-process-gone", (_e, details) => {
      mainWindow?.webContents.send("browser:tab-crashed", {
        webContentsId: contents.id,
        reason: details.reason,
      });
    });
  });

  app.whenReady().then(async () => {
    if (!app.isPackaged) app.setAsDefaultProtocolClient("echo");
    try {
      if (app.isPackaged || NATIVE_SHELL || BUILT_RENDERER_SMOKE) {
        installDesktopRendererProtocol();
      }
      // The standalone desktop owns its private bundled Agent configuration
      // and resources. A native Echo OS session connects to the immutable
      // image-baked Agent service and deliberately carries no duplicate Agent
      // resource tree inside the Electron shell package.
      if (!NATIVE_SHELL) {
        ensureDesktopConfig();
        ensurePackagedResources();
      }
    } catch (err) {
      const message = `无法安全初始化桌面应用：${err.message}`;
      console.error("[echo] desktop initialization failed:", err);
      dialog.showErrorBox("Echo 启动失败", message);
      app.exit(1);
      return;
    }
    registerIpc();
    configureBrowserPermissionRequests(session.defaultSession);
    configureBrowserPermissionRequests(browserProfileSession());
    trackDownloads(session.defaultSession);
    trackDownloads(browserProfileSession());
    await loadEnabledExtensions();
    mainWindow = createMainWindow();
    const standaloneSmoke = process.env.ECHO_SMOKE === "1";
    const nativeAppSmokeRequested = Boolean(
      process.env.ECHO_NATIVE_APP_SMOKE_ID,
    );
    // Attach before awaiting the packaged backend. The renderer can finish
    // loading while the fixed backend is still starting; registering this
    // listener afterwards made first-launch smoke runs miss the event and
    // wait until the 90-second timeout.
    const rendererSmokeReady =
      standaloneSmoke || nativeAppSmokeRequested
        ? new Promise((resolve, reject) => {
            mainWindow.webContents.once("did-finish-load", () => resolve());
            mainWindow.webContents.once(
              "did-fail-load",
              (_event, code, description) =>
                reject(
                  new Error(
                    `desktop renderer failed to load (${code} ${description})`,
                  ),
                ),
            );
          })
        : null;
    // Standalone desktop packages own their bundled Agent process. Echo OS
    // native sessions instead use the image-baked echo-agent.service; starting
    // a second packaged backend there would race the system service on port 8000.
    if ((app.isPackaged && !NATIVE_SHELL) || SMOKE_TEST_BACKEND) {
      try {
        await spawnBackend(backendConfigPath(), backendProgress);
      } catch (err) {
        const message = `无法启动随应用安装的后端：${err.message}`;
        console.error("[echo] bundled backend start failed:", err);
        dialog.showErrorBox("Echo 启动失败", message);
        app.exit(1);
        return;
      }
    }
    watchDesktop();
    const updaterContext = {
      isPackaged: app.isPackaged,
      nativeShell: NATIVE_SHELL,
      smoke: process.env.ECHO_SMOKE === "1",
      disabled: process.env.ECHO_DISABLE_AUTO_UPDATE === "1",
      platform: process.platform,
      isAppImage: Boolean(process.env.APPIMAGE),
    };
    if (desktopUpdater.shouldEnableDesktopUpdater(updaterContext)) {
      try {
        const { autoUpdater } = require("electron-updater");
        updaterController = desktopUpdater.configureDesktopUpdater({
          autoUpdater,
          dialog,
          requestQuitAndInstall: () =>
            finishAfterBackendShutdown(() =>
              autoUpdater.quitAndInstall(false, true),
            ),
        });
      } catch (error) {
        console.warn(
          "[echo] desktop updater unavailable:",
          desktopUpdater.boundedErrorMessage(error),
        );
      }
    }

    if (rendererSmokeReady) {
      void rendererSmokeReady
        .then(async () => {
          if (standaloneSmoke) {
            // Cold packaged backends can take longer than the renderer.
            // Publish success and begin the quit hold only after both halves
            // of the application are genuinely usable.
            await waitForSmokeBackendReady();
            console.log("SMOKE OK:", mainWindow.webContents.getURL());
          }
          if (nativeAppSmokeRequested) {
            const result = await nativeAppIpcSmoke.runNativeAppIpcSmoke({
              desktopSession: DESKTOP_SESSION,
              webContents: mainWindow.webContents,
            });
            if (!result.ok) {
              console.error(
                "NATIVE APP IPC SMOKE FAILED:",
                result.error || "unknown error",
              );
              app.exit(1);
              return;
            }
            console.log(result.marker);
          }
          if (standaloneSmoke) {
            const requestedHold = Number.parseInt(
              process.env.ECHO_SMOKE_HOLD_MS || "500",
              10,
            );
            const holdMilliseconds = Number.isFinite(requestedHold)
              ? Math.min(Math.max(requestedHold, 0), 60000)
              : 500;
            setTimeout(() => app.quit(), holdMilliseconds);
          }
        })
        .catch((error) => {
          console.error("SMOKE RENDERER FAILED:", error.message);
          app.exit(1);
        });
    }
    if (standaloneSmoke) {
      setTimeout(() => {
        console.error("SMOKE TIMEOUT");
        app.exit(1);
      }, 90000);
    }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0)
        mainWindow = createMainWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
