/**
 * 原生 shell(A 路线):本地已装应用(freedesktop .desktop)。
 *
 * 仅 Electron 会话 shell 模式有(`window.echo.apps`);web/浏览器端 → 空数组,
 * Dock 不显示原生应用,行为不变。图标用主进程读好的 data URL 直接显示。
 */

import { useCallback, useEffect, useState } from "react";

import type {
  NativeApp,
  NativeApplicationLaunchResult,
  NativeWindow,
  EchoElectronAPI,
} from "@/types/electron";

const NATIVE_SYSTEM_SETTINGS_IDS = new Set([
  "systemsettings",
  "kdesystemsettings",
]);
const NATIVE_FILE_MANAGER_IDS = ["org.kde.dolphin", "dolphin"];

export function isNativeSystemSettingsApp(app: Pick<NativeApp, "id">): boolean {
  return NATIVE_SYSTEM_SETTINGS_IDS.has(app.id);
}

export function findNativeSystemSettingsApp(
  apps: readonly NativeApp[],
): NativeApp | undefined {
  return (
    apps.find((app) => app.id === "systemsettings") ??
    apps.find(isNativeSystemSettingsApp)
  );
}

export function findNativeFileManagerApp(
  apps: readonly NativeApp[],
): NativeApp | undefined {
  for (const id of NATIVE_FILE_MANAGER_IDS) {
    const match = apps.find((app) => app.id === id);
    if (match) return match;
  }
  return undefined;
}

function normalizeAppIdentity(value: string): string {
  return value
    .trim()
    .toLocaleLowerCase()
    .replace(/\.desktop$/, "")
    .replace(/[^a-z0-9]+/g, ".")
    .replace(/^\.+|\.+$/g, "");
}

/** Match a freedesktop application id against EWMH WM_CLASS/KWin app id. */
export function nativeWindowMatchesApp(
  nativeWindow: NativeWindow,
  app: Pick<NativeApp, "id"> & Partial<Pick<NativeApp, "startupWmClass">>,
): boolean {
  const windowId = normalizeAppIdentity(nativeWindow.wmClass);
  if (!windowId) return false;
  const identities = [app.id, app.startupWmClass]
    .filter((value): value is string => Boolean(value))
    .map(normalizeAppIdentity)
    .filter(Boolean);
  return identities.some((appId) => {
    if (windowId === appId) return true;
    const windowParts = windowId.split(".");
    return (
      windowParts.includes(appId) ||
      windowId.endsWith(`.${appId}`) ||
      appId.endsWith(`.${windowId}`)
    );
  });
}

type NativeAppsLaunchBridge = Pick<
  NonNullable<EchoElectronAPI["apps"]>,
  "launch"
>;

function boundedLaunchError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error || "");
  return (
    message.replace(/\s+/g, " ").trim().slice(0, 256) || "本地应用启动失败"
  );
}

export async function launchNativeApplication(
  bridge: NativeAppsLaunchBridge | undefined,
  appId: string,
): Promise<NativeApplicationLaunchResult> {
  if (!bridge) return { ok: false, error: "本地应用启动服务不可用" };
  try {
    const result = await bridge.launch(appId);
    if (!result || result.ok !== true) {
      return {
        ok: false,
        error: boundedLaunchError(result?.error),
      };
    }
    return { ok: true };
  } catch (error) {
    return { ok: false, error: boundedLaunchError(error) };
  }
}

export interface UseNativeAppsOptions {
  onLaunchError?: (message: string) => void;
}

export function useNativeApps(options: UseNativeAppsOptions = {}): {
  apps: NativeApp[];
  windows: NativeWindow[];
  launch: (appId: string) => Promise<NativeApplicationLaunchResult>;
  open: (app: NativeApp) => void;
  focus: (windowId: string) => void;
  minimize: (windowId: string) => void;
  close: (windowId: string) => void;
} {
  const { onLaunchError } = options;
  const [apps, setApps] = useState<NativeApp[]>([]);
  const [windows, setWindows] = useState<NativeWindow[]>([]);

  useEffect(() => {
    const api = window.echo?.apps;
    if (!api) return; // 非原生 shell(web / 寄生窗口)→ 无原生应用
    let alive = true;
    const refresh = () => {
      void api
        .list()
        .then((list) => {
          if (alive) setApps(Array.isArray(list) ? list : []);
        })
        .catch(() => {
          if (alive) setApps([]);
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 10_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    const api = window.echo?.windows;
    if (!api) return;
    let alive = true;
    const refresh = () => {
      void api
        .list()
        .then((result) => {
          if (alive) setWindows(result.ok ? result.windows : []);
        })
        .catch(() => {
          if (alive) setWindows([]);
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 1500);
    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const launch = useCallback(
    async (appId: string) => {
      const result = await launchNativeApplication(window.echo?.apps, appId);
      if (!result.ok) {
        onLaunchError?.(result.error || "本地应用启动失败");
      }
      return result;
    },
    [onLaunchError],
  );

  const focus = useCallback((windowId: string) => {
    void window.echo?.windows?.focus(windowId).catch(() => {});
  }, []);

  const minimize = useCallback((windowId: string) => {
    void window.echo?.windows?.minimize(windowId).catch(() => {});
  }, []);

  const close = useCallback((windowId: string) => {
    void window.echo?.windows?.close(windowId).catch(() => {});
  }, []);

  const open = useCallback(
    (app: NativeApp) => {
      const existing = windows.find((item) =>
        nativeWindowMatchesApp(item, app),
      );
      if (existing) {
        focus(existing.id);
        return;
      }
      void launch(app.id);
    },
    [focus, launch, windows],
  );

  return { apps, windows, launch, open, focus, minimize, close };
}
