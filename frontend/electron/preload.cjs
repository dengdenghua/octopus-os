/**
 * Echo desktop shell — preload bridge.
 *
 * Exposes the compatibility `window.octopus` bridge implementing the
 * OctopusElectronAPI contract
 * declared in src/types/electron.d.ts. Every method maps 1:1 onto an
 * ipcMain.handle channel in main.cjs.
 */
const { contextBridge, ipcRenderer } = require("electron");

const invoke =
  (channel) =>
  (...args) =>
    ipcRenderer.invoke(channel, ...args);

const EVENT_CHANNELS = [
  "app:update-downloaded",
  "app:deep-link",
  "browser:open-tab",
  "browser:tab-crashed",
  "browser:keyboard-shortcut",
  "browser:download-event",
  "desktop:organize-now",
  "desktop:items-changed",
];

const api = {
  isElectron: true,
  platform: process.platform,
  backendBaseURL: ipcRenderer.sendSync("backend:getBaseURLSync"),

  // 原生 shell(A 路线):本地已装应用 枚举/启动。Dock/启动器渲染真实应用清单。
  apps: {
    list: invoke("apps:list"), // → [{id,name,icon,categories,source}]
    launch: invoke("apps:launch"), // (appId) → {ok,error?}; resolves after gio exits
  },

  // 目标 C:由系统窗口管理器提供真实应用窗口，而不是 React 假窗口。
  windows: {
    getCapabilities: invoke("windows:getCapabilities"),
    list: invoke("windows:list"),
    focus: invoke("windows:focus"),
    minimize: invoke("windows:minimize"),
    close: invoke("windows:close"),
  },

  browser: {
    setDevice: invoke("browser:setDevice"),
    executeJS: invoke("browser:executeJS"),
    reload: invoke("browser:reload"),
    goBack: invoke("browser:goBack"),
    goForward: invoke("browser:goForward"),
    openDevTools: invoke("browser:openDevTools"),
    capturePage: invoke("browser:capturePage"),
    extractText: invoke("browser:extractText"),
    click: invoke("browser:click"),
    type: invoke("browser:type"),
    hover: invoke("browser:hover"),
    scroll: invoke("browser:scroll"),
    waitFor: invoke("browser:waitFor"),
    pressKey: invoke("browser:pressKey"),
    getAriaTree: invoke("browser:getAriaTree"),
    getCurrentUrl: invoke("browser:getCurrentUrl"),
    clearSiteData: invoke("browser:clearSiteData"),
    showDownloadInFolder: invoke("browser:showDownloadInFolder"),
    openDownload: invoke("browser:openDownload"),
  },

  dialog: {
    open: invoke("dialog:open"),
    save: invoke("dialog:save"),
  },

  extensions: {
    list: invoke("extensions:list"),
    installFromFolder: invoke("extensions:installFromFolder"),
    setEnabled: invoke("extensions:setEnabled"),
    remove: invoke("extensions:remove"),
  },

  app: {
    getVersion: invoke("app:getVersion"),
    openExternal: invoke("app:openExternal"),
    getPlatform: invoke("app:getPlatform"),
  },

  system: {
    getCapabilities: invoke("system:getCapabilities"),
    runAction: invoke("system:runAction"),
  },

  updates: {
    getCapabilities: invoke("updates:getCapabilities"),
    getStatus: invoke("updates:getStatus"),
    apply: invoke("updates:apply"),
  },

  systemControls: {
    getState: invoke("controls:getState"),
    setWifiEnabled: invoke("controls:setWifiEnabled"),
    setBluetoothEnabled: invoke("controls:setBluetoothEnabled"),
    setAudioVolume: invoke("controls:setAudioVolume"),
    setDisplayBrightness: invoke("controls:setDisplayBrightness"),
  },

  notifications: {
    getCapabilities: invoke("notifications:getCapabilities"),
    list: invoke("notifications:list"),
    close: invoke("notifications:close"),
    clear: invoke("notifications:clear"),
  },

  desktop: {
    listItems: invoke("desktop:listItems"),
    openItem: invoke("desktop:openItem"),
    installContextMenu: invoke("desktop:installContextMenu"),
    removeContextMenu: invoke("desktop:removeContextMenu"),
    moveItem: invoke("desktop:moveItem"),
    moveItemsBatch: invoke("desktop:moveItemsBatch"),
    undoMoves: invoke("desktop:undoMoves"),
    getSystemInfo: invoke("desktop:getSystemInfo"),
  },

  backend: {
    getBaseURL: invoke("backend:getBaseURL"),
    restart: invoke("backend:restart"),
  },

  window: {
    setDeviceBounds: invoke("window:setDeviceBounds"),
    setTitleBarOverlay: invoke("window:setTitleBarOverlay"),
    setMousePassthrough: invoke("window:setMousePassthrough"),
    openDevTools: invoke("window:openDevTools"),
  },

  bridge: {
    setActiveTab: (webContentsId) =>
      ipcRenderer.send("bridge:setActiveTab", webContentsId),
  },

  on: (channel, listener) => {
    if (!EVENT_CHANNELS.includes(channel)) {
      throw new Error(`unknown event channel: ${channel}`);
    }
    const wrapped = (_event, ...args) => listener(...args);
    ipcRenderer.on(channel, wrapped);
    return () => ipcRenderer.removeListener(channel, wrapped);
  },
};

contextBridge.exposeInMainWorld("octopus", api);
contextBridge.exposeInMainWorld("__OCTOPUS_DESKTOP__", true);
