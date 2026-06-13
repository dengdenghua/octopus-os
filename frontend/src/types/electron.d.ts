/**
 * Implementation note.
 *
 * Implementation note.
 * Implementation note.
 * Implementation note.
 */

import type { DevicePreset } from "@/components/workspace/embedded-browser/browser-context";

export interface BrowserExtensionInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  manifestVersion: number;
  path: string;
  enabled: boolean;
  installedAt: string;
}

export interface NativeDesktopItem {
  id: string;
  name: string;
  subtitle: string;
  path: string;
  kind: "folder" | "file" | "app";
  extension: string;
}

/** 本地已装应用(原生 shell 系统手层枚举的 freedesktop .desktop)。 */
export interface NativeApp {
  id: string;
  name: string;
  exec: string;
  /** 解析出的图标文件绝对路径;解析不到为 null。 */
  icon: string | null;
  categories: string[];
  source: "native";
}

export interface OctopusElectronAPI {
  isElectron: true;
  platform: NodeJS.Platform;
  /** Synchronous backend URL injected by Electron preload for packaged builds. */
  backendBaseURL?: string;

  /**
   * 原生 shell(A 路线):本地已装应用 枚举/启动。会话 shell 模式下,Dock/启动器
   * 渲染真实已装应用(freedesktop .desktop)。非 Electron 端为 undefined。
   */
  apps?: {
    /** 枚举本地已装应用(原生 .desktop;Docker 应用仍走后端 app_registry)。 */
    list: () => Promise<NativeApp[]>;
    /** 启动一个应用(传 exec)。 */
    launch: (exec: string) => Promise<{ ok: boolean; pid?: number; error?: string }>;
  };

  browser: {
    setDevice: (
      webContentsId: number,
      mode: DevicePreset,
    ) => Promise<{ ok: boolean; mode: DevicePreset }>;
    executeJS: (webContentsId: number, code: string) => Promise<unknown>;
    reload: (webContentsId: number) => Promise<void>;
    goBack: (webContentsId: number) => Promise<void>;
    goForward: (webContentsId: number) => Promise<void>;
    openDevTools: (
      webContentsId: number,
    ) => Promise<{ ok: boolean; error?: string }>;
    capturePage: (
      webContentsId: number,
    ) => Promise<{ dataUrl: string; width: number; height: number }>;
    extractText: (webContentsId: number) => Promise<{
      url: string;
      title: string;
      text: string;
      truncated: boolean;
      textLength: number;
    }>;

    // Implementation note.
    click: (
      webContentsId: number,
      selector: string,
    ) => Promise<{
      ok: boolean;
      error?: string;
      tag?: string;
      text?: string;
    }>;
    type: (
      webContentsId: number,
      selector: string,
      text: string,
      opts?: { clear?: boolean },
    ) => Promise<{ ok: boolean; error?: string; value?: string }>;
    hover: (
      webContentsId: number,
      selector: string,
    ) => Promise<{ ok: boolean; error?: string }>;
    scroll: (
      webContentsId: number,
      opts: { selector?: string; deltaX?: number; deltaY?: number },
    ) => Promise<{ ok: boolean; error?: string; y?: number }>;
    waitFor: (
      webContentsId: number,
      selector: string,
      timeout?: number,
    ) => Promise<{ ok: boolean; error?: string; elapsed?: number }>;
    pressKey: (
      webContentsId: number,
      key: string,
    ) => Promise<{ ok: boolean; key: string }>;
    getAriaTree: (
      webContentsId: number,
      opts?: { maxDepth?: number },
    ) => Promise<{
      ok: boolean;
      error?: string;
      nodes?: Array<{
        id: string;
        role: string;
        name: string;
        value: string;
        backendDOMNodeId?: number;
        childIds: string[];
        ignored: boolean;
      }>;
    }>;
    getCurrentUrl: (
      webContentsId: number,
    ) => Promise<{ ok: boolean; url: string; title: string }>;
    clearSiteData: (
      webContentsId: number,
    ) => Promise<{ ok: boolean; origin?: string; error?: string }>;
    showDownloadInFolder: (
      id: string,
    ) => Promise<{ ok: boolean; error?: string }>;
    openDownload: (id: string) => Promise<{ ok: boolean; error?: string }>;
  };

  dialog: {
    open: (
      options?: Electron.OpenDialogOptions,
    ) => Promise<Electron.OpenDialogReturnValue>;
    save: (
      options?: Electron.SaveDialogOptions,
    ) => Promise<Electron.SaveDialogReturnValue>;
  };

  extensions: {
    list: () => Promise<{
      ok: boolean;
      extensions: BrowserExtensionInfo[];
      error?: string;
    }>;
    installFromFolder: () => Promise<{
      ok: boolean;
      canceled?: boolean;
      extension?: BrowserExtensionInfo;
      error?: string;
    }>;
    setEnabled: (
      id: string,
      enabled: boolean,
    ) => Promise<{
      ok: boolean;
      extension?: BrowserExtensionInfo;
      error?: string;
    }>;
    remove: (id: string) => Promise<{ ok: boolean; error?: string }>;
  };

  app: {
    getVersion: () => Promise<string>;
    openExternal: (url: string) => Promise<void>;
    getPlatform: () => Promise<NodeJS.Platform>;
  };

  desktop: {
    listItems: () => Promise<{
      ok: boolean;
      desktopPath?: string;
      items: NativeDesktopItem[];
      error?: string;
    }>;
    openItem: (path: string) => Promise<{ ok: boolean; error?: string }>;
    installContextMenu: () => Promise<{ ok: boolean; error?: string }>;
    removeContextMenu: () => Promise<{ ok: boolean; error?: string }>;
    moveItem: (
      srcPath: string,
      destDir: string,
    ) => Promise<{
      ok: boolean;
      destPath?: string;
      skipped?: boolean;
      error?: string;
    }>;
    moveItemsBatch: (
      items: Array<{ srcPath: string; category: string }>,
    ) => Promise<{
      ok: boolean;
      moved: number;
      skipped: number;
      error?: string;
    }>;
    undoMoves: () => Promise<{
      ok: boolean;
      undone: number;
      error?: string;
    }>;
    getSystemInfo: () => Promise<{
      ok: boolean;
      cpu?: {
        model: string;
        cores: number;
        usage: number;
      };
      memory?: {
        total: number;
        used: number;
        percent: number;
      };
      uptime?: number;
      platform?: string;
      error?: string;
    }>;
  };

  backend: {
    /** Return the actual desktop backend URL selected by the Electron main process. */
    getBaseURL: () => Promise<string>;
    /* Implementation note. */
    restart: () => Promise<{ ok: boolean; reason?: string }>;
  };

  window: {
    // Resize the native shell to match the selected device preview.
    setDeviceBounds: (
      mode: DevicePreset,
      width?: number,
      height?: number,
    ) => Promise<{ ok: boolean; mode?: DevicePreset; reason?: string }>;
    // Update the native title bar overlay colors.
    setTitleBarOverlay: (opts: {
      color: string;
      symbolColor: string;
    }) => Promise<{ ok: boolean; error?: string }>;
    /** Desktop organizer overlay: when enabled, transparent empty areas pass
     * mouse events through to the real Windows desktop. */
    setMousePassthrough: (
      enabled: boolean,
    ) => Promise<{ ok: boolean; enabled?: boolean; error?: string }>;
    /** Open DevTools for the host renderer (used by the preview panel's
     * inspector button so the user can examine runtime errors). */
    openDevTools: () => Promise<{ ok: boolean; error?: string }>;
  };

  /* Implementation note. */
  bridge: {
    // Active browser tab bridge used by the Electron main process.
    setActiveTab: (webContentsId: number | null) => void;
  };

  on: (
    channel:
      | "app:update-downloaded"
      | "app:deep-link"
      | "browser:open-tab"
      | "browser:tab-crashed"
      | "browser:keyboard-shortcut"
      | "browser:download-event"
      | "desktop:organize-now"
      | "desktop:items-changed",
    listener: (...args: unknown[]) => void,
  ) => () => void;
}

declare global {
  interface Window {
    octopus?: OctopusElectronAPI;
    __OCTOPUS_DESKTOP__?: boolean;
  }

  // Implementation note.
  // Implementation note.
  // Implementation note.
  namespace JSX {
    interface IntrinsicElements {
      webview: Omit<
        React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>,
        "allowpopups"
      > & {
        src?: string;
        partition?: string;
        allowpopups?: string;
        useragent?: string;
        preload?: string;
        httpreferrer?: string;
        disablewebsecurity?: string;
        nodeintegration?: string;
        plugins?: string;
        webpreferences?: string;
      };
    }
  }
}

export {};
