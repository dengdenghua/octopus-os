/* Implementation note. */

const { contextBridge, ipcRenderer } = require("electron");

function normalizeBaseURL(value) {
  return String(value || "").replace(/\/+$/, "");
}

function readBackendBaseURLArg() {
  const prefix = "--echo-backend-base-url=";
  const arg = process.argv.find((item) => String(item || "").startsWith(prefix));
  if (!arg) return "";
  const raw = arg.slice(prefix.length);
  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") return "";
    return normalizeBaseURL(url.toString());
  } catch {
    return "";
  }
}

const backendBaseURL = readBackendBaseURLArg();

const api = {
  // Implementation note.
  isElectron: true,
  platform: process.platform,
  backendBaseURL,

  // Implementation note.
  browser: {
    setDevice: (webContentsId, mode) =>
      ipcRenderer.invoke("browser:set-device", { webContentsId, mode }),
    executeJS: (webContentsId, code) =>
      ipcRenderer.invoke("browser:execute-js", { webContentsId, code }),
    reload: (webContentsId) =>
      ipcRenderer.invoke("browser:reload", { webContentsId }),
    goBack: (webContentsId) =>
      ipcRenderer.invoke("browser:go-back", { webContentsId }),
    goForward: (webContentsId) =>
      ipcRenderer.invoke("browser:go-forward", { webContentsId }),
    openDevTools: (webContentsId) =>
      ipcRenderer.invoke("browser:open-devtools", { webContentsId }),
    capturePage: (webContentsId) =>
      ipcRenderer.invoke("browser:capture-page", { webContentsId }),
    extractText: (webContentsId) =>
      ipcRenderer.invoke("browser:extract-text", { webContentsId }),
    // Implementation note.
    click: (webContentsId, selector) =>
      ipcRenderer.invoke("browser:click", { webContentsId, selector }),
    type: (webContentsId, selector, text, opts) =>
      ipcRenderer.invoke("browser:type", {
        webContentsId,
        selector,
        text,
        clear: !!opts?.clear,
      }),
    hover: (webContentsId, selector) =>
      ipcRenderer.invoke("browser:hover", { webContentsId, selector }),
    scroll: (webContentsId, opts) =>
      ipcRenderer.invoke("browser:scroll", { webContentsId, ...opts }),
    waitFor: (webContentsId, selector, timeout) =>
      ipcRenderer.invoke("browser:wait-for", {
        webContentsId,
        selector,
        timeout,
      }),
    pressKey: (webContentsId, key) =>
      ipcRenderer.invoke("browser:press-key", { webContentsId, key }),
    getAriaTree: (webContentsId, opts) =>
      ipcRenderer.invoke("browser:get-aria-tree", {
        webContentsId,
        maxDepth: opts?.maxDepth,
      }),
    getCurrentUrl: (webContentsId) =>
      ipcRenderer.invoke("browser:get-current-url", { webContentsId }),
    clearSiteData: (webContentsId) =>
      ipcRenderer.invoke("browser:clear-site-data", { webContentsId }),
    showDownloadInFolder: (id) =>
      ipcRenderer.invoke("browser:show-download-in-folder", { id }),
    openDownload: (id) => ipcRenderer.invoke("browser:open-download", { id }),
  },

  // ── Native dialog ──────────────────────────────────
  dialog: {
    open: (options) => ipcRenderer.invoke("dialog:open", options),
    save: (options) => ipcRenderer.invoke("dialog:save", options),
  },

  // Implementation note.
  extensions: {
    list: () => ipcRenderer.invoke("extensions:list"),
    installFromFolder: () =>
      ipcRenderer.invoke("extensions:install-from-folder"),
    setEnabled: (id, enabled) =>
      ipcRenderer.invoke("extensions:set-enabled", { id, enabled }),
    remove: (id) => ipcRenderer.invoke("extensions:remove", { id }),
  },

  // Implementation note.
  app: {
    getVersion: () => ipcRenderer.invoke("app:get-version"),
    openExternal: (url) => ipcRenderer.invoke("app:open-external", url),
    getPlatform: () => ipcRenderer.invoke("app:get-platform"),
  },

  desktop: {
    listItems: () => ipcRenderer.invoke("desktop:list-items"),
    openItem: (path) => ipcRenderer.invoke("desktop:open-item", { path }),
    installContextMenu: () => ipcRenderer.invoke("desktop:install-context-menu"),
    removeContextMenu: () => ipcRenderer.invoke("desktop:remove-context-menu"),
    moveItem: (srcPath, destDir) =>
      ipcRenderer.invoke("desktop:move-item", { srcPath, destDir }),
    moveItemsBatch: (items) =>
      ipcRenderer.invoke("desktop:move-items-batch", { items }),
    undoMoves: () => ipcRenderer.invoke("desktop:undo-moves"),
    getSystemInfo: () => ipcRenderer.invoke("desktop:get-system-info"),
  },

  // Implementation note.
  backend: {
    getBaseURL: () => ipcRenderer.invoke("backend:get-base-url"),
    restart: () => ipcRenderer.invoke("backend:restart"),
  },

  // Implementation note.
  window: {
    setDeviceBounds: (mode, width, height) =>
      ipcRenderer.invoke("window:set-device-bounds", { mode, width, height }),
    setTitleBarOverlay: (opts) =>
      ipcRenderer.invoke("window:set-title-bar-overlay", opts),
    setMousePassthrough: (enabled) =>
      ipcRenderer.invoke("window:set-mouse-passthrough", { enabled }),
    openDevTools: () => ipcRenderer.invoke("window:open-devtools"),
  },

  // Implementation note.
  // Implementation note.
  bridge: {
    setActiveTab: (webContentsId) =>
      ipcRenderer.send("bridge:set-active-tab", { webContentsId }),
  },

  // Implementation note.
  // Implementation note.
  on: (channel, listener) => {
    const allowed = [
      "app:update-downloaded",
      "app:deep-link",
      "browser:open-tab",
      "browser:tab-crashed",
      "browser:keyboard-shortcut",
      "browser:download-event",
      "desktop:organize-now",
      "desktop:items-changed",
    ];
    if (!allowed.includes(channel)) {
      throw new Error(`channel not allowed: ${channel}`);
    }
    const wrapped = (_event, ...args) => listener(...args);
    ipcRenderer.on(channel, wrapped);
    return () => ipcRenderer.removeListener(channel, wrapped);
  },
};

contextBridge.exposeInMainWorld("echo", api);
