import { describe, expect, it } from "vitest";

import {
  findNativeFileManagerApp,
  findNativeSystemSettingsApp,
  isNativeSystemSettingsApp,
  launchNativeApplication,
  nativeWindowMatchesApp,
} from "./apps-native";
import type { NativeApp } from "@/types/electron";

const nativeWindow = (wmClass: string) => ({
  id: "0x1",
  desktop: 0,
  pid: 1,
  host: "echo",
  wmClass,
  title: "Window",
  active: false,
  minimized: null,
  provider: "ewmh-x11" as const,
});

describe("nativeWindowMatchesApp", () => {
  it("matches a freedesktop desktop id to KWin/EWMH app identity", () => {
    expect(
      nativeWindowMatchesApp(nativeWindow("org.gnome.Nautilus"), {
        id: "org.gnome.Nautilus.desktop",
      }),
    ).toBe(true);
  });

  it("matches a KWin desktop-file path to the enumerated application id", () => {
    expect(
      nativeWindowMatchesApp(
        nativeWindow(
          "/var/lib/flatpak/exports/share/applications/org.mozilla.firefox.desktop",
        ),
        { id: "org.mozilla.firefox" },
      ),
    ).toBe(true);
  });

  it("matches the class component used by common X11 applications", () => {
    expect(
      nativeWindowMatchesApp(nativeWindow("code.Code"), { id: "code" }),
    ).toBe(true);
  });

  it("does not associate unrelated applications", () => {
    expect(
      nativeWindowMatchesApp(nativeWindow("firefox.Firefox"), { id: "code" }),
    ).toBe(false);
  });

  it("uses StartupWMClass for a branded desktop entry", () => {
    expect(
      nativeWindowMatchesApp(nativeWindow("plasma-discover"), {
        id: "echo-app-store",
        startupWmClass: "plasma-discover",
      }),
    ).toBe(true);
  });
});

describe("native system settings", () => {
  const app = (id: string): NativeApp => ({
    id,
    name: id,
    startupWmClass: null,
    icon: null,
    iconDataUrl: null,
    categories: ["Settings"],
    source: "native",
  });

  it("selects KDE System Settings by desktop id, not by display name", () => {
    expect(isNativeSystemSettingsApp(app("systemsettings"))).toBe(true);
    expect(isNativeSystemSettingsApp(app("kdesystemsettings"))).toBe(true);
    expect(isNativeSystemSettingsApp(app("fake-settings"))).toBe(false);
    expect(
      findNativeSystemSettingsApp([
        app("kdesystemsettings"),
        app("systemsettings"),
      ])?.id,
    ).toBe("systemsettings");
  });
});

describe("native file manager", () => {
  const app = (id: string): NativeApp => ({
    id,
    name: id,
    startupWmClass: null,
    icon: null,
    iconDataUrl: null,
    categories: ["FileManager"],
    source: "native",
  });

  it("selects the packaged Dolphin desktop id without using display text", () => {
    expect(
      findNativeFileManagerApp([
        app("fake-dolphin-name"),
        app("dolphin"),
        app("org.kde.dolphin"),
      ])?.id,
    ).toBe("org.kde.dolphin");
    expect(
      findNativeFileManagerApp([app("fake-dolphin-name")]),
    ).toBeUndefined();
  });
});

describe("launchNativeApplication", () => {
  it("returns success only when the Electron bridge confirms gio success", async () => {
    await expect(
      launchNativeApplication(
        { launch: async () => ({ ok: true }) },
        "org.kde.kcalc",
      ),
    ).resolves.toEqual({ ok: true });
    await expect(
      launchNativeApplication(
        {
          launch: async () => ({ ok: false, error: "gio exited non-zero" }),
        },
        "org.kde.kcalc",
      ),
    ).resolves.toEqual({ ok: false, error: "gio exited non-zero" });
  });

  it("turns missing or rejected IPC into bounded user-visible failures", async () => {
    await expect(
      launchNativeApplication(undefined, "org.kde.kcalc"),
    ).resolves.toEqual({ ok: false, error: "本地应用启动服务不可用" });
    await expect(
      launchNativeApplication(
        {
          launch: async () => {
            throw new Error(`IPC rejected ${"x".repeat(1024)}`);
          },
        },
        "org.kde.kcalc",
      ),
    ).resolves.toMatchObject({ ok: false });
    const rejected = await launchNativeApplication(
      {
        launch: async () => {
          throw new Error(`IPC rejected ${"x".repeat(1024)}`);
        },
      },
      "org.kde.kcalc",
    );
    expect(rejected.error?.length).toBeLessThanOrEqual(256);
  });
});
