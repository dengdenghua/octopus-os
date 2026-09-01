/**
 * Echo desktop · Electron main process.
 *
 * Responsibilities:
 *   1. Single-instance lock that forwards URLs/files to the running instance.
 *   2. Main window and platform menus for macOS, Windows, and Linux.
 *   3. Backend child process management for the packaged PyInstaller binary.
 *   4. <webview> bridge for CDP emulation, JS injection, navigation, and filtering.
 *   5. Native dialogs used in place of a Tauri dialog plugin.
 *   6. Auto-update through electron-updater and GitHub releases.
 *   7. Local crash reporting for renderer crashes.
 *   8. echo:// deep-link protocol handling.
 */

const {
  app,
  BrowserWindow,
  Menu,
  MenuItem,
  ipcMain,
  webContents,
  dialog,
  shell,
  clipboard,
  crashReporter,
  protocol,
  session,
} = require("electron");
const path = require("path");
const fs = require("fs");
const http = require("http");
const crypto = require("crypto");
const net = require("net");
const os = require("os");
const { spawn } = require("child_process");
const { pathToFileURL } = require("url");

// Some Windows GPU drivers can open the Electron window without rendering it.
// Disable hardware acceleration by default to favor visibility and stability.
app.disableHardwareAcceleration();
if (process.platform === "win32") {
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-compositing");
}

// Configure the app icon before taking the single-instance lock.
// Windows taskbar identity needs this before app startup.
const iconFile =
  process.platform === "win32"
    ? "icon.ico"
    : process.platform === "darwin"
      ? "icon.icns"
      : "icon.png";
const iconPath = path.join(__dirname, "..", "build", iconFile);
if (fs.existsSync(iconPath)) {
  app.setAppUserModelId("ai.echo.desktop");
}

// ─── Single-instance lock ─────────────────────────────
// A second binary launch returns false here; the first instance handles args
// and deep links through the second-instance event.
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
  process.exit(0);
}

// ─── Crash reporter ───────────────────────────────────
// uploadToServer=false keeps crash data local.
crashReporter.start({
  productName: "Echo",
  companyName: "Echo AI",
  submitURL: "",
  uploadToServer: false,
});

const isDev =
  process.env.NODE_ENV === "development" ||
  process.env.ELECTRON_ENV === "development" ||
  (!app.isPackaged && process.env.FORCE_PROD !== "1");
const APP_PROTOCOL = "echo";
// Use process.resourcesPath so packaged builds find dist inside or outside asar.
const indexPath = isDev
  ? path.join(__dirname, "..", "dist", "index.html")
  : path.join(process.resourcesPath, "app.asar", "dist", "index.html");
const DESKTOP_CONTEXT_MENU_KEY =
  "HKCU\\Software\\Classes\\Directory\\Background\\shell\\EchoOrganizeDesktop";

function rendererRoute(hashPath, customIndexPath) {
  const hash = hashPath.startsWith("#") ? hashPath : `#${hashPath}`;
  if (isDev) {
    return `${process.env.FRONTEND_URL || "http://localhost:3000"}${hash}`;
  }
  // Use the custom index path when provided, otherwise use the default indexPath.
  const targetPath = customIndexPath || indexPath;
  const params = new URLSearchParams({ echoBackend: backendBaseURL });
  return `${pathToFileURL(targetPath).toString()}?${params.toString()}${hash}`;
}

function openDesktopOrganizerFromShell() {
  if (!mainWindow) return;
  mainWindow.loadURL(rendererRoute("#/desktop")).finally(() => {
    setTimeout(() => {
      mainWindow?.webContents.send("desktop:organize-now");
    }, 700);
  });
}

function handleAppDeepLink(deepLink) {
  if (!deepLink?.startsWith(`${APP_PROTOCOL}://`)) return false;
  if (deepLink.startsWith(`${APP_PROTOCOL}://desktop-organize`)) {
    openDesktopOrganizerFromShell();
    return true;
  }
  return false;
}

// Register the echo:// deep link handler.
if (process.defaultApp) {
  if (process.argv.length >= 2) {
    app.setAsDefaultProtocolClient(APP_PROTOCOL, process.execPath, [
      path.resolve(process.argv[1]),
    ]);
  }
} else {
  app.setAsDefaultProtocolClient(APP_PROTOCOL);
}

// Device profiles that mirror Chrome DevTools Device Toolbar presets.
//
// userAgentMetadata must match userAgent. Some sites reload in a loop when the
// UA claims mobile but Sec-CH-UA client hints still look desktop-like.
const DEVICE_PROFILES = {
  desktop: null, // null clears the override.
  tablet: {
    width: 768,
    height: 1024,
    deviceScaleFactor: 2,
    mobile: false,
    userAgent:
      "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) " +
      "AppleWebKit/605.1.15 (KHTML, like Gecko) " +
      "Version/17.0 Mobile/15E148 Safari/604.1",
    userAgentMetadata: {
      brands: [
        { brand: "Not_A Brand", version: "8" },
        { brand: "Chromium", version: "120" },
        { brand: "Google Chrome", version: "120" },
      ],
      fullVersion: "120.0.0.0",
      platform: "iPadOS",
      platformVersion: "17.0",
      architecture: "",
      model: "iPad",
      mobile: false,
    },
  },
  mobile: {
    width: 375,
    height: 812,
    deviceScaleFactor: 3,
    mobile: true,
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) " +
      "AppleWebKit/605.1.15 (KHTML, like Gecko) " +
      "Version/17.0 Mobile/15E148 Safari/604.1",
    userAgentMetadata: {
      brands: [
        { brand: "Not_A Brand", version: "8" },
        { brand: "Chromium", version: "120" },
        { brand: "Google Chrome", version: "120" },
      ],
      fullVersion: "120.0.0.0",
      platform: "iOS",
      platformVersion: "17.0",
      architecture: "",
      model: "iPhone",
      mobile: true,
    },
  },
};

let mainWindow = null;
let backendProcess = null;
let backendRestartCount = 0;
const BACKEND_MAX_RESTARTS = 3;
const BACKEND_DEFAULT_PORT = Number(process.env.ECHO_BACKEND_PORT || 8000);
let backendPort = BACKEND_DEFAULT_PORT;
let backendBaseURL = `http://127.0.0.1:${backendPort}`;

// Browser-IPC capability gate.
//
// All ``browser:*`` handlers operate on a webContents id supplied by the
// renderer. Without verification, a compromised webview (e.g. malicious
// page loaded into the in-app browser tab) can pass the main window's
// own webContents id and execute arbitrary JS in the privileged main
// renderer — which has the entire preload IPC surface (dialog, backend
// restart, extension install, downloads, ...).
//
// We require:
//   1. The IPC originated from the main window's webContents (so a
//      child webview can't proxy calls).
//   2. The target webContents is either the main window itself
//      (allowed for a small whitelist of read-only ops) or a child
//      webview hosted *by* the main window.
function _resolveBrowserWebContents(event, rawId, { allowMain = false } = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    throw new Error("browser ipc: no main window");
  }
  if (!event || event.sender !== mainWindow.webContents) {
    throw new Error("browser ipc: caller is not the main renderer");
  }
  const id = Number(rawId);
  if (!Number.isFinite(id)) {
    throw new Error("browser ipc: webContentsId required");
  }
  const wc = webContents.fromId(id);
  if (!wc || wc.isDestroyed()) {
    throw new Error(`browser ipc: webContents ${id} not found`);
  }
  if (wc === mainWindow.webContents) {
    if (!allowMain) {
      throw new Error("browser ipc: target is the main renderer (forbidden)");
    }
    return wc;
  }
  if (wc.hostWebContents !== mainWindow.webContents) {
    throw new Error("browser ipc: target is not a webview of the main window");
  }
  return wc;
}

const extensionRegistry = new Map();
const knownExtensionSessions = new Set();
const loadedExtensionIdsBySession = new WeakMap();
const downloadListenerSessions = new WeakSet();
const browserDownloads = new Map();

function extensionsStatePath() {
  return path.join(app.getPath("userData"), "browser-extensions.json");
}

function readExtensionManifest(extensionPath) {
  const manifestPath = path.join(extensionPath, "manifest.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error("manifest.json not found");
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  if (!manifest || typeof manifest.name !== "string") {
    throw new Error("invalid extension manifest");
  }
  const manifestVersion = Number(manifest.manifest_version || 0);
  if (![2, 3].includes(manifestVersion)) {
    throw new Error("unsupported manifest version");
  }
  return {
    name: manifest.name,
    version: String(manifest.version || "0.0.0"),
    description: String(manifest.description || ""),
    manifestVersion,
  };
}

function loadExtensionRegistry() {
  extensionRegistry.clear();
  try {
    const file = extensionsStatePath();
    if (!fs.existsSync(file)) return;
    const items = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!Array.isArray(items)) return;
    for (const item of items) {
      if (!item?.id || !item?.path) continue;
      extensionRegistry.set(item.id, {
        id: String(item.id),
        name: String(item.name || item.id),
        version: String(item.version || ""),
        description: String(item.description || ""),
        manifestVersion: Number(item.manifestVersion || 0),
        path: String(item.path),
        enabled: item.enabled !== false,
        installedAt: String(item.installedAt || new Date().toISOString()),
      });
    }
  } catch (e) {
    console.warn("extension registry read failed:", e?.message || e);
  }
}

function saveExtensionRegistry() {
  const file = extensionsStatePath();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(
    file,
    JSON.stringify([...extensionRegistry.values()], null, 2),
  );
}

function extensionList() {
  return [...extensionRegistry.values()].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
}

function isPathInside(parent, target) {
  const relative = path.relative(parent, target);
  return !!relative && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function getDesktopItems() {
  const desktopPath = app.getPath("desktop");
  const entries = fs.readdirSync(desktopPath, { withFileTypes: true });
  return entries
    .filter((entry) => !entry.name.startsWith("."))
    .slice(0, 120)
    .map((entry) => {
      const fullPath = path.join(desktopPath, entry.name);
      const ext = path.extname(entry.name).toLowerCase();
      const name = path.basename(entry.name, ext);
      const kind = entry.isDirectory()
        ? "folder"
        : ext === ".lnk" || ext === ".url" || ext === ".app"
          ? "app"
          : "file";
      return {
        id: crypto.createHash("sha1").update(fullPath).digest("hex"),
        name: name || entry.name,
        subtitle: entry.isDirectory() ? "Folder" : ext ? ext.slice(1).toUpperCase() : "File",
        path: fullPath,
        kind,
        extension: ext.replace(/^\./, ""),
      };
    });
}

async function loadExtensionIntoSession(ses, item) {
  if (!ses || !item?.enabled) return null;
  if (!fs.existsSync(item.path)) {
    return { ok: false, id: item.id, error: "extension path not found" };
  }
  let loadedIds = loadedExtensionIdsBySession.get(ses);
  if (!loadedIds) {
    loadedIds = new Set();
    loadedExtensionIdsBySession.set(ses, loadedIds);
  }
  if (loadedIds.has(item.id)) return { ok: true, id: item.id, alreadyLoaded: true };
  try {
    const loaded = await ses.loadExtension(item.path, {
      allowFileAccess: true,
    });
    loadedIds.add(loaded.id);
    if (loaded.id !== item.id && extensionRegistry.has(item.id)) {
      extensionRegistry.delete(item.id);
      item.id = loaded.id;
      extensionRegistry.set(item.id, item);
      saveExtensionRegistry();
    }
    return { ok: true, id: loaded.id };
  } catch (e) {
    return { ok: false, id: item.id, error: String(e?.message || e) };
  }
}

async function loadEnabledExtensionsIntoSession(ses) {
  if (!ses) return [];
  knownExtensionSessions.add(ses);
  const results = [];
  for (const item of extensionRegistry.values()) {
    if (item.enabled) {
      results.push(await loadExtensionIntoSession(ses, item));
    }
  }
  return results;
}

function removeExtensionFromKnownSessions(id) {
  for (const ses of knownExtensionSessions) {
    try {
      ses.removeExtension(id);
      loadedExtensionIdsBySession.get(ses)?.delete(id);
    } catch {
      /* extension may not be loaded in this session */
    }
  }
}

function sendBrowserDownloadEvent(payload) {
  const target = mainWindow || BrowserWindow.getAllWindows()[0];
  if (target && !target.isDestroyed()) {
    target.webContents.send("browser:download-event", payload);
  }
}

function openBrowserUrlInApp(url) {
  if (!url || url === "about:blank") return;
  const target = mainWindow || BrowserWindow.getAllWindows()[0];
  if (target && !target.isDestroyed()) {
    target.webContents.send("browser:open-tab", { url });
  }
}

function attachDownloadListener(ses) {
  if (!ses || downloadListenerSessions.has(ses)) return;
  downloadListenerSessions.add(ses);
  ses.on("will-download", (_event, item) => {
    const id =
      typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : crypto.randomBytes(12).toString("hex");
    const createdAt = Date.now();
    const filename = item.getFilename();
    const url = item.getURL();
    browserDownloads.set(id, { id, filename, url, path: "", state: "progressing" });

    const snapshot = (state) => {
      const pathValue = item.getSavePath?.() || "";
      const payload = {
        id,
        filename,
        url,
        state,
        receivedBytes: item.getReceivedBytes(),
        totalBytes: item.getTotalBytes(),
        savePath: pathValue,
        createdAt,
      };
      browserDownloads.set(id, {
        id,
        filename,
        url,
        path: pathValue,
        state,
      });
      sendBrowserDownloadEvent(payload);
    };

    snapshot("progressing");
    item.on("updated", (_event, state) => {
      snapshot(state === "interrupted" ? "interrupted" : "progressing");
    });
    item.once("done", (_event, state) => {
      snapshot(state);
    });
  });
}

// ─── Backend lifecycle ────────────────────────────────
function backendBinaryPath() {
  const ext = process.platform === "win32" ? ".exe" : "";
  return path.join(
    process.resourcesPath,
    "backend",
    `echo-backend${ext}`
  );
}

function desktopDataDir() {
  if (process.env.ECHO_DATA_DIR) {
    return process.env.ECHO_DATA_DIR;
  }
  if (isDev) {
    return path.resolve(__dirname, "..", "..", "data");
  }
  return path.join(app.getPath("userData"), "data");
}

function bundledAgentsRoot() {
  if (process.env.ECHO_AGENTS_ROOT) {
    return process.env.ECHO_AGENTS_ROOT;
  }
  if (isDev) {
    return path.resolve(__dirname, "..", "..", "agents");
  }
  return path.join(process.resourcesPath, "agents");
}

function backendConfigTemplatePath() {
  if (isDev) {
    return path.resolve(__dirname, "..", "..", "packaging", "desktop", "config.desktop.yaml");
  }
  return path.join(process.resourcesPath, "config.desktop.yaml");
}

function backendConfigPath() {
  if (process.env.ECHO_CONFIG_PATH) {
    return process.env.ECHO_CONFIG_PATH;
  }
  if (isDev) {
    return path.resolve(__dirname, "..", "..", "config.local.yaml");
  }
  return path.join(app.getPath("userData"), "config.yaml");
}

function backendRuntimeStatePath() {
  return path.join(app.getPath("userData"), "backend-runtime.json");
}

function readBackendRuntimeState() {
  try {
    const file = backendRuntimeStatePath();
    if (!fs.existsSync(file)) return null;
    const state = JSON.parse(fs.readFileSync(file, "utf8"));
    return state && typeof state === "object" ? state : null;
  } catch {
    return null;
  }
}

function writeBackendRuntimeState(state) {
  try {
    fs.mkdirSync(path.dirname(backendRuntimeStatePath()), { recursive: true });
    fs.writeFileSync(backendRuntimeStatePath(), JSON.stringify(state, null, 2));
  } catch (e) {
    console.warn("[backend] failed to write runtime state:", e?.message || e);
  }
}

function removeBackendRuntimeState() {
  try {
    fs.unlinkSync(backendRuntimeStatePath());
  } catch {
    /* file may not exist */
  }
}

function isPidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function stopStaleBackendFromState() {
  const state = readBackendRuntimeState();
  const stalePid = Number(state?.pid || 0);
  if (!stalePid || !isPidAlive(stalePid)) {
    removeBackendRuntimeState();
    return;
  }
  try {
    console.warn(`[backend] stopping stale desktop backend pid=${stalePid}`);
    process.kill(stalePid);
  } catch (e) {
    console.warn("[backend] stale backend kill failed:", e?.message || e);
  } finally {
    removeBackendRuntimeState();
  }
}

function prepareDesktopRuntime() {
  const dataDir = desktopDataDir();
  const agentsRoot = bundledAgentsRoot();
  fs.mkdirSync(dataDir, { recursive: true });
  process.env.ECHO_DATA_DIR = dataDir;
  process.env.ECHO_AGENTS_ROOT = agentsRoot;

  const configPath = backendConfigPath();
  if (!fs.existsSync(configPath)) {
    const templatePath = backendConfigTemplatePath();
    if (!fs.existsSync(templatePath)) {
      throw new Error(`desktop backend config template not found: ${templatePath}`);
    }
    fs.copyFileSync(templatePath, configPath);
  }
  return { configPath, dataDir, agentsRoot };
}

function isPortAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, "127.0.0.1");
  });
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.once("listening", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : BACKEND_DEFAULT_PORT;
      server.close(() => resolve(port));
    });
    server.listen(0, "127.0.0.1");
  });
}

async function chooseBackendPort() {
  if (await isPortAvailable(BACKEND_DEFAULT_PORT)) {
    backendPort = BACKEND_DEFAULT_PORT;
  } else {
    backendPort = await findFreePort();
    console.warn(
      `[backend] port ${BACKEND_DEFAULT_PORT} is busy; using ${backendPort} for desktop backend`,
    );
  }
  backendBaseURL = `http://127.0.0.1:${backendPort}`;
  return backendPort;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function checkBackendHealth(url) {
  return new Promise((resolve) => {
    const req = http.get(`${url}/api/health`, { timeout: 1500 }, (res) => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 500);
    });
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

async function waitForBackendReady(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await checkBackendHealth(url)) return true;
    await wait(350);
  }
  return false;
}

async function startBackend() {
  if (isDev) return;
  const bin = backendBinaryPath();
  if (!fs.existsSync(bin)) {
    console.warn(`backend binary not found: ${bin} · falling back to system`);
    return;
  }
  stopStaleBackendFromState();
  const { configPath, dataDir, agentsRoot } = prepareDesktopRuntime();
  const port = await chooseBackendPort();
  backendProcess = spawn(bin, [
    "serve",
    "--config",
    configPath,
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
  ], {
    cwd: path.dirname(bin),
    env: {
      ...process.env,
      ECHO_DESKTOP: "1",
      ECHO_DATA_DIR: dataDir,
      ECHO_AGENTS_ROOT: agentsRoot,
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  writeBackendRuntimeState({
    pid: backendProcess.pid,
    port,
    baseURL: backendBaseURL,
    bin,
    startedAt: new Date().toISOString(),
  });
  backendProcess.stdout.on("data", (chunk) => {
    process.stdout.write(`[backend] ${chunk}`);
  });
  backendProcess.stderr.on("data", (chunk) => {
    process.stderr.write(`[backend] ${chunk}`);
  });
  backendProcess.on("exit", (code, signal) => {
    console.warn(`backend exited code=${code} signal=${signal}`);
    backendProcess = null;
    removeBackendRuntimeState();
    if (
      !app.isQuitting &&
      backendRestartCount < BACKEND_MAX_RESTARTS &&
      code !== 0
    ) {
      backendRestartCount += 1;
      setTimeout(() => {
        void startBackend().catch((e) => {
          console.warn("[backend] restart failed:", e?.message || e);
        });
      }, 2000);
    }
  });
  const ready = await waitForBackendReady(backendBaseURL);
  if (!ready) {
    console.warn(`[backend] health check timed out for ${backendBaseURL}`);
  }
}

function stopBackend() {
  if (!backendProcess) return;
  try {
    backendProcess.kill();
  } catch (e) {
    console.warn("backend kill failed:", e?.message || e);
  }
  backendProcess = null;
  removeBackendRuntimeState();
}

// ─── Main window ──────────────────────────────────────
function createMainWindow() {
  const iconFile =
    process.platform === "win32"
      ? "icon.ico"
      : process.platform === "darwin"
        ? "icon.icns"
        : "icon.png";
  const iconPath = path.join(__dirname, "..", "build", iconFile);

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    backgroundColor: "#071018",
    transparent: false,
    fullscreenable: false,
    show: false,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    titleBarStyle: "default",
    trafficLightPosition: { x: 12, y: 11 },
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [
        `--echo-backend-base-url=${backendBaseURL}`,
      ],
      webviewTag: true,
      sandbox: false,
      spellcheck: true,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    mainWindow.focus();
    mainWindow.center();
  });

  mainWindow.on("enter-full-screen", () => {
    mainWindow.setFullScreen(false);
    if (!mainWindow.isMaximized()) {
      mainWindow.maximize();
    }
  });

  console.log("[electron] isDev:", isDev);
  console.log("[electron] __dirname:", __dirname);
  console.log("[electron] process.resourcesPath:", process.resourcesPath);
  console.log("[electron] indexPath:", indexPath);
  console.log("[electron] index.html exists:", fs.existsSync(indexPath));

  if (isDev) {
    const devUrl = process.env.FRONTEND_URL || "http://localhost:3000";
    mainWindow.loadURL(devUrl);
  } else {
    if (!fs.existsSync(indexPath)) {
      console.error("[electron] CRITICAL: index.html not found at:", indexPath);
      const fallbackPaths = [
        path.join(process.resourcesPath, "app", "dist", "index.html"),
        path.join(__dirname, "..", "dist", "index.html"),
        path.join(__dirname, "dist", "index.html"),
      ];
      let found = false;
      for (const p of fallbackPaths) {
        console.log("[electron] checking fallback:", p, "exists:", fs.existsSync(p));
        if (fs.existsSync(p)) {
          console.log("[electron] using fallback path:", p);
          mainWindow.loadURL(rendererRoute("#/login", p));
          found = true;
          break;
        }
      }
      if (!found) {
        console.error("[electron] FATAL: Could not find index.html in any location");
        dialog.showErrorBox(
          "启动错误",
          "无法找到应用程序文件。请重新安装 Echo。"
        );
        app.quit();
      }
    } else {
      mainWindow.loadURL(rendererRoute("#/login"));
    }
  }
  if (isDev || process.env.ECHO_OPEN_DEVTOOLS === "1") {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }

  mainWindow.webContents.on("did-finish-load", () => {
    console.log("renderer did-finish-load:", mainWindow.webContents.getURL());
  });
  const initialDeepLink = process.argv.find((a) => a.startsWith(`${APP_PROTOCOL}://`));
  if (initialDeepLink) {
    mainWindow.webContents.once("did-finish-load", () => {
      handleAppDeepLink(initialDeepLink);
    });
  }
  mainWindow.webContents.on(
    "did-fail-load",
    (_event, errorCode, errorDescription, validatedURL) => {
      console.error("renderer did-fail-load:", {
        errorCode,
        errorDescription,
        validatedURL,
      });
    },
  );
  mainWindow.webContents.on(
    "console-message",
    (_event, level, message, line, sourceId) => {
      console.log("[renderer-console]", { level, message, line, sourceId });
    },
  );

  // F12 / Ctrl+Shift+I (Win/Linux) / Cmd+Opt+I (mac) toggle DevTools ·
  mainWindow.webContents.on("context-menu", (_event, params) => {
    const pageUrl = mainWindow?.webContents.getURL() || "";
    const isDesktopOrganizer =
      pageUrl.includes("#/desktop") || pageUrl.includes("#/workspace/desktop-organizer");
    if (!isDesktopOrganizer) return;

    const template = [
      {
        label: "一键整理桌面",
        click: () => mainWindow?.webContents.send("desktop:organize-now"),
      },
      {
        label: "打开桌面整理设置",
        click: () => {
          if (!mainWindow) return;
          mainWindow.loadURL(rendererRoute("#/workspace/desktop-organizer"));
        },
      },
    ];
    if (params.isEditable) {
      template.push({ type: "separator" });
      template.push({ role: "copy" });
      template.push({ role: "paste" });
    }
    Menu.buildFromTemplate(template).popup({ window: mainWindow });
  });

  mainWindow.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    const isMac = process.platform === "darwin";
    const k = (input.key || "").toLowerCase();
    if (k === "f12") {
      mainWindow.webContents.toggleDevTools();
      event.preventDefault();
      return;
    }
    const mainMod = isMac ? input.meta : input.control;
    if (!mainMod) return;
    if (k === "i" && (input.shift || (isMac && input.alt))) {
      mainWindow.webContents.toggleDevTools();
      event.preventDefault();
      return;
    }
    // Ctrl+R / Cmd+R reload
    if (k === "r") {
      mainWindow.webContents.reload();
      event.preventDefault();
      return;
    }
    if (k === "r" && input.shift) {
      mainWindow.webContents.reloadIgnoringCache();
      event.preventDefault();
      return;
    }
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith("file://") && !url.includes("index.html")) {
      event.preventDefault();
      const hashPath = url.replace(/^file:\/\/\/[A-Z]:/, "");
      mainWindow.loadURL(rendererRoute(hashPath));
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url || url === "about:blank") {
      return { action: "deny" };
    }
    if (/^https?:\/\//.test(url)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  mainWindow.webContents.on("render-process-gone", (_e, details) => {
    console.error("renderer gone:", details);
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// ─── Menu ─────────────────────────────────────────────
function buildMenu() {
  if (process.platform !== "darwin") {
    Menu.setApplicationMenu(null);
    return;
  }
  const template = [
    {
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ─── Auto-update ──────────────────────────────────────
function setupAutoUpdater() {
  if (isDev) return;
  let autoUpdater;
  try {
    autoUpdater = require("electron-updater").autoUpdater;
  } catch (e) {
    console.warn("electron-updater not installed · skipping auto-update");
    return;
  }
  autoUpdater.logger = console;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.on("error", (err) => {
    console.warn("auto-update error:", err?.message || err);
  });
  autoUpdater.on("update-available", (info) => {
    console.log("update available:", info?.version);
  });
  autoUpdater.on("update-downloaded", (info) => {
    if (mainWindow) {
      mainWindow.webContents.send("app:update-downloaded", info);
    }
  });
  setTimeout(() => autoUpdater.checkForUpdatesAndNotify().catch(() => {}), 60_000);
  setInterval(
    () => autoUpdater.checkForUpdatesAndNotify().catch(() => {}),
    4 * 60 * 60 * 1000
  );
}

// ─── Bridge HTTP server · backend agent → frontend webview RPC ────
//
//
const BRIDGE_TOKEN = crypto.randomBytes(16).toString("hex");
let bridgeServer = null;
let bridgePort = 0;
let activeWebContentsId = null;

function bridgeStatePath() {
  const root = desktopDataDir();
  return path.join(root, "bridge.json");
}

function writeBridgeState() {
  try {
    const file = bridgeStatePath();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(
      file,
      JSON.stringify(
        { port: bridgePort, token: BRIDGE_TOKEN, pid: process.pid },
        null,
        2,
      ),
    );
  } catch (e) {
    console.warn("bridge state write failed:", e?.message || e);
  }
}

function startBridgeServer() {
  bridgeServer = http.createServer((req, res) => {
    // Auth
    const auth = req.headers.authorization || "";
    if (auth !== `Bearer ${BRIDGE_TOKEN}`) {
      res.writeHead(401, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: "unauthorized" }));
      return;
    }
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      try {
        const data = body ? JSON.parse(body) : {};
        const wcId = data.webContentsId ?? activeWebContentsId;
        if (wcId == null && req.url !== "/status") {
          res.writeHead(503, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({ ok: false, error: "no active tab in browser shell" }),
          );
          return;
        }
        const result = await handleBridgeAction(req.url, wcId, data);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(result));
      } catch (e) {
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            ok: false,
            error: e instanceof Error ? e.message : String(e),
          }),
        );
      }
    });
  });
  bridgeServer.listen(0, "127.0.0.1", () => {
    bridgePort = bridgeServer.address().port;
    writeBridgeState();
    console.log(
      `bridge listening 127.0.0.1:${bridgePort} · token written to ${bridgeStatePath()}`,
    );
  });
}

async function handleBridgeAction(url, wcId, data) {
  const op = (url || "").replace(/^\//, "");
  if (op === "status") {
    return { ok: true, activeWebContentsId: activeWebContentsId, pid: process.pid };
  }
  const wc = wcId == null ? null : webContents.fromId(Number(wcId));
  if (!wc) return { ok: false, error: "webContents not found" };

  switch (op) {
    case "click": {
      const sel = JSON.stringify(data.selector || "");
      return wc.executeJavaScript(
        `(()=>{const el=document.querySelector(${sel});if(!el)return{ok:false,error:'not_found'};el.scrollIntoView({behavior:'instant',block:'center'});const r=el.getBoundingClientRect();if(r.width===0||r.height===0)return{ok:false,error:'invisible'};el.click();return{ok:true,tag:el.tagName,text:(el.innerText||el.value||'').slice(0,80)};})()`,
        true,
      );
    }
    case "type": {
      const sel = JSON.stringify(data.selector || "");
      const txt = JSON.stringify(data.text || "");
      const clear = !!data.clear;
      return wc.executeJavaScript(
        `(()=>{const el=document.querySelector(${sel});if(!el)return{ok:false,error:'not_found'};el.focus();const proto=el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;const cur=${clear}?'':(el.value||'');const next=cur+${txt};if(setter)setter.call(el,next);else el.value=next;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return{ok:true,value:el.value};})()`,
        true,
      );
    }
    case "scroll": {
      if (data.selector) {
        const sel = JSON.stringify(data.selector);
        return wc.executeJavaScript(
          `(()=>{const el=document.querySelector(${sel});if(!el)return{ok:false,error:'not_found'};el.scrollIntoView({behavior:'smooth',block:'center'});return{ok:true};})()`,
          true,
        );
      }
      const dy = Number(data.deltaY || 0);
      return wc.executeJavaScript(
        `window.scrollBy({top:${dy},behavior:'smooth'});({ok:true,y:window.scrollY})`,
        true,
      );
    }
    case "wait": {
      const sel = JSON.stringify(data.selector || "");
      const ms = Number(data.timeout || 10_000);
      return wc.executeJavaScript(
        `(async()=>{const start=Date.now();while(Date.now()-start<${ms}){const el=document.querySelector(${sel});if(el&&el.getBoundingClientRect().width>0)return{ok:true,elapsed:Date.now()-start};await new Promise(r=>setTimeout(r,200));}return{ok:false,error:'timeout'};})()`,
        true,
      );
    }
    case "navigate": {
      const url = String(data.url || "");
      if (!url) return { ok: false, error: "missing url" };
      try {
        await wc.loadURL(url);
        return { ok: true, url };
      } catch (e) {
        return { ok: false, error: e?.message || String(e) };
      }
    }
    case "extract": {
      return wc.executeJavaScript(
        `(()=>{const root=document.querySelector('main, article')||document.body;const clone=root.cloneNode(true);clone.querySelectorAll('script, style, noscript, iframe').forEach(el=>el.remove());const text=(clone.innerText||clone.textContent||'').trim();return{ok:true,url:location.href,title:document.title,text:text.slice(0,20000),truncated:text.length>20000,textLength:text.length};})()`,
        true,
      );
    }
    case "screenshot": {
      const image = await wc.capturePage();
      const png = image.toPNG();
      return {
        ok: true,
        dataUrl: `data:image/png;base64,${png.toString("base64")}`,
        width: image.getSize().width,
        height: image.getSize().height,
      };
    }
    case "execute-js": {
      const code = String(data.code || "");
      try {
        const r = await wc.executeJavaScript(code, true);
        return { ok: true, result: r };
      } catch (e) {
        return { ok: false, error: e?.message || String(e) };
      }
    }
    case "current-url":
      return { ok: true, url: wc.getURL(), title: wc.getTitle() };
    default:
      return { ok: false, error: `unknown action: ${op}` };
  }
}

ipcMain.on("bridge:set-active-tab", (_event, args) => {
  activeWebContentsId = args?.webContentsId ?? null;
});

// ─── App lifecycle ────────────────────────────────────
app.whenReady().then(async () => {
  prepareDesktopRuntime();
  loadExtensionRegistry();
  attachDownloadListener(session.defaultSession);
  void loadEnabledExtensionsIntoSession(session.defaultSession);
  buildMenu();
  await startBackend();
  createMainWindow();
  setupAutoUpdater();
  startBridgeServer();
});

app.on("before-quit", () => {
  app.isQuitting = true;
  stopBackend();
  try {
    fs.unlinkSync(bridgeStatePath());
  } catch {
    /* file may not exist */
  }
  if (bridgeServer) {
    try {
      bridgeServer.close();
    } catch {
      /* ignore */
    }
  }
});

app.on("second-instance", (_event, argv) => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
  const deepLink = argv.find((a) => a.startsWith(`${APP_PROTOCOL}://`));
  if (deepLink && mainWindow) {
    if (!handleAppDeepLink(deepLink)) {
      mainWindow.webContents.send("app:deep-link", deepLink);
    }
  }
});

app.on("open-url", (event, url) => {
  event.preventDefault();
  if (mainWindow) {
    if (!handleAppDeepLink(url)) {
      mainWindow.webContents.send("app:deep-link", url);
    }
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.isQuitting = true;
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
});

app.on("web-contents-created", (_event, wc) => {
  knownExtensionSessions.add(wc.session);
  attachDownloadListener(wc.session);
  void loadEnabledExtensionsIntoSession(wc.session);

  const isMainWindowContents =
    mainWindow && wc.id === mainWindow.webContents.id;
  if (!isMainWindowContents) {
    wc.setWindowOpenHandler(({ url }) => {
      if (/^(https?:|view-source:)/.test(url)) {
        openBrowserUrlInApp(url);
      }
      return { action: "deny" };
    });
    wc.on("did-create-window", (childWindow, details) => {
      const url = details?.url || "";
      if (/^(https?:|view-source:)/.test(url)) {
        openBrowserUrlInApp(url);
      }
      if (!childWindow.isDestroyed()) childWindow.close();
    });
  }

  wc.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    const isMac = process.platform === "darwin";
    const mod = isMac ? input.meta : input.control;
    if (!mod) return;
    const key = (input.key || "").toLowerCase();
    const interesting =
      key === "t" || key === "w" || key === "l" || /^[1-9]$/.test(key) ||
      input.key === "Tab";
    if (!interesting) return;
    const main = BrowserWindow.getAllWindows()[0];
    if (main && !main.isDestroyed()) {
      main.webContents.send("browser:keyboard-shortcut", {
        key: input.key,
        shift: input.shift,
        alt: input.alt,
        meta: input.meta,
        control: input.control,
      });
    }
    event.preventDefault();
  });

  wc.on("context-menu", (_e, params) => {
    const isLink = !!params.linkURL;
    const isImage = params.mediaType === "image";
    const isText = !!params.selectionText;
    const isEditable = params.isEditable;

    const template = [];

    if (isLink) {
      template.push(
        {
          label: "在新标签页打开",
          click: () => {
            const main = BrowserWindow.getAllWindows()[0];
            if (main) {
              main.webContents.send("browser:open-tab", { url: params.linkURL });
            }
          },
        },
        {
          label: "复制链接",
          click: () => clipboard.writeText(params.linkURL),
        },
        { type: "separator" },
      );
    }

    if (isImage) {
      template.push(
        {
          label: "复制图片",
          click: () => wc.copyImageAt(params.x, params.y),
        },
        {
          label: "复制图片地址",
          click: () => clipboard.writeText(params.srcURL),
        },
        { type: "separator" },
      );
    }

    if (isText) {
      template.push(
        { role: "copy", label: "复制" },
        {
          label: "用 Google 搜索",
          click: () => {
            const q = encodeURIComponent(params.selectionText);
            const main = BrowserWindow.getAllWindows()[0];
            if (main) {
              main.webContents.send("browser:open-tab", {
                url: `https://www.google.com/search?q=${q}`,
              });
            }
          },
        },
        { type: "separator" },
      );
    }

    if (isEditable) {
      template.push(
        { role: "cut", label: "剪切" },
        { role: "copy", label: "复制" },
        { role: "paste", label: "粘贴" },
        { role: "selectAll", label: "全选" },
        { type: "separator" },
      );
    }

    template.push(
      {
        label: "后退",
        enabled: wc.navigationHistory?.canGoBack() ?? wc.canGoBack(),
        click: () => wc.goBack(),
      },
      {
        label: "前进",
        enabled: wc.navigationHistory?.canGoForward() ?? wc.canGoForward(),
        click: () => wc.goForward(),
      },
      { label: "刷新", click: () => wc.reload() },
      { type: "separator" },
      {
        label: "查看页面源代码",
        click: () => {
          const url = `view-source:${wc.getURL()}`;
          const main = BrowserWindow.getAllWindows()[0];
          if (main) main.webContents.send("browser:open-tab", { url });
        },
      },
      {
        label: "检查元素",
        click: () => {
          if (wc.isDevToolsOpened()) wc.devToolsWebContents?.focus();
          else wc.openDevTools({ mode: "detach" });
          wc.inspectElement(params.x, params.y);
        },
      },
    );

    Menu.buildFromTemplate(template).popup({
      window: BrowserWindow.fromWebContents(wc) || undefined,
    });
  });
});

ipcMain.handle("browser:set-device", async (event, args) => {
  const { webContentsId, mode } = args || {};
  const wc = _resolveBrowserWebContents(event, webContentsId);

  const profile = DEVICE_PROFILES[mode];

  if (profile) wc.setUserAgent(profile.userAgent);
  else wc.setUserAgent(wc.session.getUserAgent());

  if (!wc.debugger.isAttached()) {
    try {
      wc.debugger.attach("1.3");
    } catch (e) {
      console.warn("debugger.attach failed:", e?.message || e);
    }
  }

  if (profile) {
    await wc.debugger.sendCommand("Emulation.setDeviceMetricsOverride", {
      width: profile.width,
      height: profile.height,
      deviceScaleFactor: profile.deviceScaleFactor,
      mobile: profile.mobile,
    });
    await wc.debugger.sendCommand("Emulation.setTouchEmulationEnabled", {
      enabled: profile.mobile,
    });
    await wc.debugger.sendCommand("Emulation.setUserAgentOverride", {
      userAgent: profile.userAgent,
      userAgentMetadata: profile.userAgentMetadata,
      platform: profile.userAgentMetadata?.platform,
    });
  } else {
    await wc.debugger
      .sendCommand("Emulation.clearDeviceMetricsOverride")
      .catch(() => {});
    await wc.debugger
      .sendCommand("Emulation.setTouchEmulationEnabled", { enabled: false })
      .catch(() => {});
    await wc.debugger
      .sendCommand("Emulation.setUserAgentOverride", { userAgent: "" })
      .catch(() => {});
  }

  return { ok: true, mode };
});

ipcMain.handle("browser:execute-js", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  return wc.executeJavaScript(args.code, true);
});


ipcMain.handle("browser:click", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  const sel = JSON.stringify(args.selector);
  const code = `
    (() => {
      const el = document.querySelector(${sel});
      if (!el) return { ok: false, error: 'not_found' };
      el.scrollIntoView({ behavior: 'instant', block: 'center' });
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return { ok: false, error: 'invisible' };
      el.click();
      return { ok: true, tag: el.tagName, text: (el.innerText || el.value || '').slice(0, 80) };
    })()
  `;
  return wc.executeJavaScript(code, true);
});

ipcMain.handle("browser:type", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  const sel = JSON.stringify(args.selector);
  const text = JSON.stringify(args.text || "");
  const clear = !!args.clear;
  const code = `
    (() => {
      const el = document.querySelector(${sel});
      if (!el) return { ok: false, error: 'not_found' };
      el.focus();
      const proto = el.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      const cur = ${clear} ? '' : (el.value || '');
      const next = cur + ${text};
      if (setter) setter.call(el, next); else el.value = next;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true, value: el.value };
    })()
  `;
  return wc.executeJavaScript(code, true);
});

ipcMain.handle("browser:hover", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  const sel = JSON.stringify(args.selector);
  const code = `
    (() => {
      const el = document.querySelector(${sel});
      if (!el) return { ok: false, error: 'not_found' };
      el.scrollIntoView({ behavior: 'instant', block: 'center' });
      const r = el.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      ['mouseover', 'mouseenter', 'mousemove'].forEach(t => {
        el.dispatchEvent(new MouseEvent(t, { bubbles: true, clientX: cx, clientY: cy }));
      });
      return { ok: true };
    })()
  `;
  return wc.executeJavaScript(code, true);
});

ipcMain.handle("browser:scroll", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  if (args.selector) {
    const sel = JSON.stringify(args.selector);
    const code = `
      (() => {
        const el = document.querySelector(${sel});
        if (!el) return { ok: false, error: 'not_found' };
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return { ok: true };
      })()
    `;
    return wc.executeJavaScript(code, true);
  }
  const dy = Number(args.deltaY || 0);
  const dx = Number(args.deltaX || 0);
  const code = `window.scrollBy({ top: ${dy}, left: ${dx}, behavior: 'smooth' }); ({ ok: true, y: window.scrollY })`;
  return wc.executeJavaScript(code, true);
});

ipcMain.handle("browser:wait-for", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  const sel = JSON.stringify(args.selector);
  const timeoutMs = Number(args.timeout || 10_000);
  const code = `
    (async () => {
      const start = Date.now();
      while (Date.now() - start < ${timeoutMs}) {
        const el = document.querySelector(${sel});
        if (el && el.getBoundingClientRect().width > 0) {
          return { ok: true, elapsed: Date.now() - start };
        }
        await new Promise(r => setTimeout(r, 200));
      }
      return { ok: false, error: 'timeout' };
    })()
  `;
  return wc.executeJavaScript(code, true);
});

ipcMain.handle("browser:press-key", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  const key = String(args.key || "Enter");
  wc.sendInputEvent({ type: "keyDown", keyCode: key });
  wc.sendInputEvent({ type: "char", keyCode: key });
  wc.sendInputEvent({ type: "keyUp", keyCode: key });
  return { ok: true, key };
});

ipcMain.handle("browser:get-aria-tree", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  if (!wc.debugger.isAttached()) {
    try {
      wc.debugger.attach("1.3");
    } catch (e) {
      return { ok: false, error: `debugger attach failed: ${e?.message || e}` };
    }
  }
  try {
    await wc.debugger.sendCommand("Accessibility.enable");
    const result = await wc.debugger.sendCommand(
      "Accessibility.getFullAXTree",
      { max_depth: Number(args.maxDepth || 50) },
    );
    const nodes = (result.nodes || []).map((n) => ({
      id: n.nodeId,
      role: n.role?.value || "",
      name: n.name?.value || "",
      value: n.value?.value || "",
      backendDOMNodeId: n.backendDOMNodeId,
      childIds: n.childIds || [],
      ignored: !!n.ignored,
    }));
    return { ok: true, nodes };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
});

ipcMain.handle("browser:get-current-url", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  return { ok: true, url: wc.getURL(), title: wc.getTitle() };
});

ipcMain.handle("browser:clear-site-data", async (event, args) => {
  let wc;
  try {
    wc = _resolveBrowserWebContents(event, args?.webContentsId);
  } catch (e) {
    return { ok: false, error: e?.message || "webContents not found" };
  }
  const rawUrl = wc.getURL();
  let origin;
  try {
    const parsed = new URL(rawUrl);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return { ok: false, error: "current page has no site data origin" };
    }
    origin = parsed.origin;
  } catch {
    return { ok: false, error: "invalid current page url" };
  }

  await wc.session.clearStorageData({
    origin,
    storages: [
      "cookies",
      "filesystem",
      "indexdb",
      "localstorage",
      "serviceworkers",
      "cachestorage",
    ],
  });
  await wc.session.clearCache();
  return { ok: true, origin };
});

ipcMain.handle("browser:show-download-in-folder", async (_event, args) => {
  const item = browserDownloads.get(args?.id);
  if (!item?.path) return { ok: false, error: "download path not found" };
  shell.showItemInFolder(item.path);
  return { ok: true };
});

ipcMain.handle("browser:open-download", async (event, args) => {
  // Origin check: only the main renderer may trigger an Open. A
  // compromised webview otherwise has a one-call path to launch any
  // file that the renderer happens to know the id of (and the id is
  // visible to every script with preload access — it's pushed via
  // ``browser:download-event``).
  if (!mainWindow || mainWindow.isDestroyed()
      || !event || event.sender !== mainWindow.webContents) {
    return { ok: false, error: "browser:open-download forbidden" };
  }
  const item = browserDownloads.get(args?.id);
  if (!item?.path) return { ok: false, error: "download path not found" };
  // Require explicit user confirmation. ``shell.openPath`` launches
  // the registered handler for the file extension — for .exe/.bat/.ps1
  // that's arbitrary code execution. Always re-confirm at this seam,
  // even if the file came from a "trusted" download (the file might
  // have been swapped on disk between download and open).
  const choice = await dialog.showMessageBox(mainWindow, {
    type: "question",
    buttons: ["Open", "Cancel"],
    defaultId: 1,
    cancelId: 1,
    title: "Open downloaded file?",
    message: `Open file:\n${item.path}`,
    detail: "This will launch the system handler for this file type.",
  });
  if (choice.response !== 0) {
    return { ok: false, error: "cancelled" };
  }
  const error = await shell.openPath(item.path);
  return error ? { ok: false, error } : { ok: true };
});

ipcMain.handle("browser:capture-page", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  const image = await wc.capturePage();
  const png = image.toPNG();
  return {
    dataUrl: `data:image/png;base64,${png.toString("base64")}`,
    width: image.getSize().width,
    height: image.getSize().height,
  };
});

ipcMain.handle("browser:extract-text", async (event, args) => {
  const wc = _resolveBrowserWebContents(event, args?.webContentsId);
  const code = `
    (() => {
      const root = document.querySelector('main, article') || document.body;
      const clone = root.cloneNode(true);
      clone.querySelectorAll('script, style, noscript, iframe').forEach(el => el.remove());
      const text = (clone.innerText || clone.textContent || '').trim();
      return {
        url: location.href,
        title: document.title,
        text: text.slice(0, 20000),
        truncated: text.length > 20000,
        textLength: text.length,
      };
    })()
  `;
  return wc.executeJavaScript(code, true);
});

ipcMain.handle("browser:reload", async (event, args) => {
  try {
    const wc = _resolveBrowserWebContents(event, args?.webContentsId);
    wc.reload();
  } catch {
    // best effort: invalid target -> noop
  }
});
ipcMain.handle("browser:go-back", async (event, args) => {
  try {
    const wc = _resolveBrowserWebContents(event, args?.webContentsId);
    if (wc.canGoBack()) wc.goBack();
  } catch {
    // best effort
  }
});
ipcMain.handle("browser:go-forward", async (event, args) => {
  try {
    const wc = _resolveBrowserWebContents(event, args?.webContentsId);
    if (wc.canGoForward()) wc.goForward();
  } catch {
    // best effort
  }
});
ipcMain.handle("browser:open-devtools", async (event, args) => {
  let wc;
  try {
    wc = _resolveBrowserWebContents(event, args?.webContentsId);
  } catch (e) {
    return { ok: false, error: e?.message || "webContents not found" };
  }
  // ``detach`` so the devtools window floats free of the host iframe's
  // layout — matches Chrome DevTools default for a popped-out inspector.
  wc.openDevTools({ mode: "detach" });
  return { ok: true };
});

// Open DevTools for the host renderer itself (the React app + any
// sandboxed iframes inside it). Used by the live-preview panel's
// ``<>`` button so users can inspect runtime errors that originated in
// the preview iframe — those errors land in the host renderer's
// console because the iframe is same-origin srcdoc-rendered.
ipcMain.handle("window:open-devtools", async (event) => {
  const wc = event.sender;
  if (!wc) return { ok: false, error: "sender unavailable" };
  wc.openDevTools({ mode: "detach" });
  return { ok: true };
});

// Sanitise renderer-supplied dialog options. Without this, a
// compromised renderer can:
//   - Set defaultPath to C:\Windows\System32 (hostile suggestion)
//   - Pass arbitrary ``properties`` to widen the picker scope
//     (e.g. add "openDirectory" + "multiSelections" to a Save dialog)
//   - Inject ``message`` / ``title`` text designed to trick the user.
//
// Allow a strict subset and ignore anything else.
const _DIALOG_OPEN_PROPERTIES = new Set([
  "openFile",
  "openDirectory",
  "multiSelections",
  "showHiddenFiles",
  "createDirectory",
  "promptToCreate",
  "noResolveAliases",
  "treatPackageAsDirectory",
  "dontAddToRecent",
]);

function _sanitiseDialogOptions(raw, { allowProperties = true } = {}) {
  if (!raw || typeof raw !== "object") return {};
  const out = {};
  if (typeof raw.title === "string" && raw.title.length <= 200) {
    out.title = raw.title;
  }
  if (typeof raw.message === "string" && raw.message.length <= 500) {
    out.message = raw.message;
  }
  if (typeof raw.buttonLabel === "string" && raw.buttonLabel.length <= 60) {
    out.buttonLabel = raw.buttonLabel;
  }
  if (typeof raw.defaultPath === "string") {
    // Don't allow the renderer to suggest absolute system paths.
    // Keep only basename + safe relative segments.
    const p = String(raw.defaultPath);
    if (!path.isAbsolute(p) && !p.includes("..")) {
      out.defaultPath = p;
    } else {
      // Strip down to basename to preserve "this is the filename"
      // intent without honouring the directory.
      out.defaultPath = path.basename(p);
    }
  }
  if (Array.isArray(raw.filters)) {
    out.filters = raw.filters
      .filter(
        (f) =>
          f
          && typeof f === "object"
          && typeof f.name === "string"
          && Array.isArray(f.extensions),
      )
      .slice(0, 16)
      .map((f) => ({
        name: String(f.name).slice(0, 60),
        extensions: f.extensions
          .filter((e) => typeof e === "string" && /^[a-zA-Z0-9*]{1,16}$/.test(e))
          .slice(0, 16),
      }));
  }
  if (allowProperties && Array.isArray(raw.properties)) {
    out.properties = raw.properties.filter(
      (p) => typeof p === "string" && _DIALOG_OPEN_PROPERTIES.has(p),
    );
  }
  return out;
}

ipcMain.handle("dialog:open", async (event, options) => {
  if (!mainWindow || mainWindow.isDestroyed()
      || !event || event.sender !== mainWindow.webContents) {
    return { canceled: true, filePaths: [] };
  }
  return dialog.showOpenDialog(
    mainWindow,
    _sanitiseDialogOptions(options || {}, { allowProperties: true }),
  );
});
ipcMain.handle("dialog:save", async (event, options) => {
  if (!mainWindow || mainWindow.isDestroyed()
      || !event || event.sender !== mainWindow.webContents) {
    return { canceled: true, filePath: "" };
  }
  return dialog.showSaveDialog(
    mainWindow,
    _sanitiseDialogOptions(options || {}, { allowProperties: false }),
  );
});

// ─── IPC: Chromium extensions ───────────────────────────────────────────
ipcMain.handle("extensions:list", async () => {
  return { ok: true, extensions: extensionList() };
});

ipcMain.handle("extensions:install-from-folder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择拓展文件夹",
    properties: ["openDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) {
    return { ok: false, canceled: true };
  }

  const extensionPath = result.filePaths[0];
  try {
    const manifest = readExtensionManifest(extensionPath);
    const temp = {
      id: `pending-${crypto
        .createHash("sha1")
        .update(extensionPath)
        .digest("hex")
        .slice(0, 16)}`,
      ...manifest,
      path: extensionPath,
      enabled: true,
      installedAt: new Date().toISOString(),
    };
    extensionRegistry.set(temp.id, temp);
    const loadResult =
      (await loadExtensionIntoSession(session.defaultSession, temp)) || {};
    if (!loadResult.ok) {
      extensionRegistry.delete(temp.id);
      throw new Error(loadResult.error || "failed to load extension");
    }
    for (const ses of knownExtensionSessions) {
      if (ses !== session.defaultSession) {
        await loadExtensionIntoSession(ses, temp);
      }
    }
    saveExtensionRegistry();
    return { ok: true, extension: temp };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
});

ipcMain.handle("extensions:set-enabled", async (_event, args) => {
  const id = String(args?.id || "");
  const enabled = !!args?.enabled;
  const item = extensionRegistry.get(id);
  if (!item) return { ok: false, error: "extension not found" };

  item.enabled = enabled;
  if (enabled) {
    for (const ses of knownExtensionSessions) {
      await loadExtensionIntoSession(ses, item);
    }
    await loadExtensionIntoSession(session.defaultSession, item);
  } else {
    removeExtensionFromKnownSessions(id);
    try {
      session.defaultSession.removeExtension(id);
    } catch {
      /* ignore */
    }
  }
  saveExtensionRegistry();
  return { ok: true, extension: item };
});

ipcMain.handle("extensions:remove", async (_event, args) => {
  const id = String(args?.id || "");
  if (!extensionRegistry.has(id)) {
    return { ok: false, error: "extension not found" };
  }
  removeExtensionFromKnownSessions(id);
  try {
    session.defaultSession.removeExtension(id);
  } catch {
    /* ignore */
  }
  extensionRegistry.delete(id);
  saveExtensionRegistry();
  return { ok: true };
});

ipcMain.handle("app:get-version", () => app.getVersion());
ipcMain.handle("app:open-external", (_event, url) => shell.openExternal(url));
ipcMain.handle("app:get-platform", () => process.platform);

ipcMain.handle("desktop:list-items", async () => {
  try {
    return {
      ok: true,
      desktopPath: app.getPath("desktop"),
      items: getDesktopItems(),
    };
  } catch (e) {
    return { ok: false, error: String(e?.message || e), items: [] };
  }
});

ipcMain.handle("desktop:open-item", async (_event, args) => {
  try {
    const desktopPath = path.resolve(app.getPath("desktop"));
    const targetPath = path.resolve(String(args?.path || ""));
    if (!isPathInside(desktopPath, targetPath) && targetPath !== desktopPath) {
      return { ok: false, error: "path is outside desktop" };
    }
    if (!fs.existsSync(targetPath)) {
      return { ok: false, error: "path not found" };
    }
    const error = await shell.openPath(targetPath);
    return error ? { ok: false, error } : { ok: true };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
});

function runReg(args) {
  return new Promise((resolve) => {
    const child = spawn("reg", args, { windowsHide: true });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => resolve({ ok: false, error: error.message }));
    child.on("close", (code) => {
      resolve(code === 0 ? { ok: true } : { ok: false, error: stderr || `reg exited ${code}` });
    });
  });
}

ipcMain.handle("desktop:install-context-menu", async () => {
  if (process.platform !== "win32") {
    return { ok: false, error: "system context menu is only supported on Windows" };
  }
  const command = `"${process.execPath}" "${APP_PROTOCOL}://desktop-organize"`;
  const steps = [
    ["add", DESKTOP_CONTEXT_MENU_KEY, "/ve", "/d", "Echo 一键整理桌面", "/f"],
    ["add", DESKTOP_CONTEXT_MENU_KEY, "/v", "Icon", "/d", process.execPath, "/f"],
    ["add", `${DESKTOP_CONTEXT_MENU_KEY}\\command`, "/ve", "/d", command, "/f"],
  ];
  for (const step of steps) {
    const result = await runReg(step);
    if (!result.ok) return result;
  }
  return { ok: true };
});

ipcMain.handle("desktop:remove-context-menu", async () => {
  if (process.platform !== "win32") {
    return { ok: false, error: "system context menu is only supported on Windows" };
  }
  const result = await runReg(["delete", DESKTOP_CONTEXT_MENU_KEY, "/f"]);
  return result.ok || /unable to find|找不到|系统找不到/i.test(result.error || "")
    ? { ok: true }
    : result;
});

const _desktopMoveUndoStack = [];
const DESKTOP_ARCHIVE_FOLDERS = {
  image: "图片",
  document: "文档",
  package: "安装包",
  other: "其他",
};

ipcMain.handle("desktop:move-item", async (_event, args) => {
  try {
    const srcPath = path.resolve(String(args?.srcPath || ""));
    const destDir = path.resolve(String(args?.destDir || ""));
    const desktopPath = path.resolve(app.getPath("desktop"));
    if (!isPathInside(desktopPath, srcPath) && srcPath !== desktopPath) {
      return { ok: false, error: "source path is outside desktop" };
    }
    if (!isPathInside(desktopPath, destDir) && destDir !== desktopPath) {
      return { ok: false, error: "destination path is outside desktop" };
    }
    if (!fs.existsSync(srcPath)) {
      return { ok: false, error: "source not found" };
    }
    fs.mkdirSync(destDir, { recursive: true });
    const basename = path.basename(srcPath);
    let finalDestPath = path.join(destDir, basename);
    if (fs.existsSync(finalDestPath) && finalDestPath !== srcPath) {
      const ext = path.extname(basename);
      const nameNoExt = path.basename(basename, ext);
      let counter = 1;
      while (fs.existsSync(finalDestPath)) {
        finalDestPath = path.join(destDir, `${nameNoExt} (${counter})${ext}`);
        counter++;
      }
    }
    if (finalDestPath === srcPath) {
      return { ok: true, destPath: finalDestPath, skipped: true };
    }
    fs.renameSync(srcPath, finalDestPath);
    _desktopMoveUndoStack.push({ from: srcPath, to: finalDestPath });
    return { ok: true, destPath: finalDestPath };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
});

ipcMain.handle("desktop:move-items-batch", async (_event, args) => {
  try {
    const items = Array.isArray(args?.items) ? args.items : [];
    const desktopPath = path.resolve(app.getPath("desktop"));
    const batch = [];
    for (const item of items) {
      const srcPath = path.resolve(String(item.srcPath || ""));
      const category = String(item.category || "other");
      if (!isPathInside(desktopPath, srcPath) && srcPath !== desktopPath) continue;
      if (!fs.existsSync(srcPath)) continue;
      const folderName = DESKTOP_ARCHIVE_FOLDERS[category] || DESKTOP_ARCHIVE_FOLDERS.other;
      const destDir = path.join(desktopPath, folderName);
      fs.mkdirSync(destDir, { recursive: true });
      const basename = path.basename(srcPath);
      let finalDestPath = path.join(destDir, basename);
      if (fs.existsSync(finalDestPath) && finalDestPath !== srcPath) {
        const ext = path.extname(basename);
        const nameNoExt = path.basename(basename, ext);
        let counter = 1;
        while (fs.existsSync(finalDestPath)) {
          finalDestPath = path.join(destDir, `${nameNoExt} (${counter})${ext}`);
          counter++;
        }
      }
      if (finalDestPath === srcPath) continue;
      try {
        fs.renameSync(srcPath, finalDestPath);
        batch.push({ from: srcPath, to: finalDestPath });
      } catch {
        continue;
      }
    }
    if (batch.length > 0) {
      _desktopMoveUndoStack.push(batch);
    }
    return { ok: true, moved: batch.length, skipped: items.length - batch.length };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
});

ipcMain.handle("desktop:undo-moves", async () => {
  try {
    const last = _desktopMoveUndoStack.pop();
    if (!last) return { ok: true, undone: 0 };
    const entries = Array.isArray(last) ? last : [last];
    let undone = 0;
    for (const entry of entries) {
      try {
        if (!fs.existsSync(entry.to)) continue;
        const destDir = path.dirname(entry.from);
        fs.mkdirSync(destDir, { recursive: true });
        let finalPath = entry.from;
        if (fs.existsSync(finalPath) && finalPath !== entry.to) {
          const ext = path.extname(entry.from);
          const nameNoExt = path.basename(entry.from, ext);
          let counter = 1;
          while (fs.existsSync(finalPath)) {
            finalPath = path.join(destDir, `${nameNoExt} (${counter})${ext}`);
            counter++;
          }
        }
        fs.renameSync(entry.to, finalPath);
        undone++;
      } catch {
        continue;
      }
    }
    return { ok: true, undone };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
});

ipcMain.handle("desktop:get-system-info", async () => {
  try {
    const cpus = os.cpus();
    const totalMem = os.totalmem();
    const freeMem = os.freemem();
    return {
      ok: true,
      cpu: {
        model: cpus[0]?.model || "Unknown",
        cores: cpus.length,
        usage: Math.round((1 - cpus.reduce((acc, cpu) => acc + cpu.times.idle, 0) / cpus.reduce((acc, cpu) => acc + Object.values(cpu.times).reduce((a, b) => a + b, 0), 0)) * 100),
      },
      memory: {
        total: Math.round(totalMem / 1024 / 1024 / 1024 * 100) / 100,
        used: Math.round((totalMem - freeMem) / 1024 / 1024 / 1024 * 100) / 100,
        percent: Math.round((1 - freeMem / totalMem) * 100),
      },
      uptime: Math.round(os.uptime() / 60),
      platform: os.platform(),
    };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
});

ipcMain.handle("window:set-title-bar-overlay", (_event, args) => {
  if (!mainWindow || process.platform !== "win32") return { ok: false };
  try {
    mainWindow.setTitleBarOverlay({
      color: args?.color || "#f1f1f3",
      symbolColor: args?.symbolColor || "#525252",
      height: 36,
    });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
});

ipcMain.handle("window:set-mouse-passthrough", (_event, args) => {
  if (!mainWindow) return { ok: false, error: "no main window" };
  const enabled = !!args?.enabled;
  mainWindow.setIgnoreMouseEvents(enabled, { forward: true });
  return { ok: true, enabled };
});

let savedDesktopBounds = null;

const CHROME_PADDING = { width: 80, height: 140 };

ipcMain.handle("window:set-device-bounds", async (_event, args) => {
  const { mode, width, height } = args || {};
  if (!mainWindow) return { ok: false, reason: "no main window" };

  if (mode === "desktop") {
    if (savedDesktopBounds) {
      mainWindow.setBounds(savedDesktopBounds);
      savedDesktopBounds = null;
    } else {
      mainWindow.setSize(1440, 900);
      mainWindow.center();
    }
    return { ok: true, mode };
  }

  if (!savedDesktopBounds) {
    savedDesktopBounds = mainWindow.getBounds();
  }
  const targetW = (Number(width) || 0) + CHROME_PADDING.width;
  const targetH = (Number(height) || 0) + CHROME_PADDING.height;
  if (targetW < 320 || targetH < 480) {
    return { ok: false, reason: "invalid dims" };
  }
  const cur = mainWindow.getBounds();
  mainWindow.setBounds({
    x: cur.x,
    y: cur.y,
    width: Math.round(targetW),
    height: Math.round(targetH),
  });
  return { ok: true, mode, width: targetW, height: targetH };
});

ipcMain.handle("backend:get-base-url", async () => {
  return backendBaseURL;
});

ipcMain.handle("backend:restart", async () => {
  if (isDev) {
    console.log("[backend] dev mode restart · requesting agent reload via HTTP");
    try {
      const http = require("http");
      const result = await new Promise((resolve, reject) => {
        const req = http.request(
          {
            hostname: "localhost",
            port: backendPort,
            path: "/api/agents/reload",
            method: "POST",
            headers: { "Content-Type": "application/json" },
            timeout: 5000,
          },
          (res) => {
            let data = "";
            res.on("data", (chunk) => { data += chunk; });
            res.on("end", () => {
              if (res.statusCode >= 200 && res.statusCode < 300) {
                resolve({ ok: true, mode: "dev-reload" });
              } else {
                reject(new Error(`HTTP ${res.statusCode}: ${data}`));
              }
            });
          }
        );
        req.on("error", reject);
        req.on("timeout", () => { req.destroy(); reject(new Error("timeout")); });
        req.end();
      });
      return result;
    } catch (e) {
      console.warn("[backend] dev reload failed:", e?.message || e);
      return { ok: false, reason: `dev reload failed: ${e?.message || e}` };
    }
  }
  if (!backendProcess) {
    return { ok: false, reason: "backend not running" };
  }
  console.log("[backend] manual restart requested via IPC");
  stopBackend();
  backendRestartCount = 0;
  setTimeout(() => {
    void startBackend().catch((e) => {
      console.warn("[backend] manual restart failed:", e?.message || e);
    });
  }, 500);
  return { ok: true };
});
