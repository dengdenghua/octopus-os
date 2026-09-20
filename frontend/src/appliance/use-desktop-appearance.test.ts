import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { LIQUID_GLASS_TUNING_STORAGE_KEY } from "./liquid-glass-settings";
import { useDesktopAppearance } from "./use-desktop-appearance";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() },
}));

const APPEARANCE_KEY = "echo:desktop-appearance";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove(
    "theme-white",
    "theme-glass",
    "theme-dark",
  );
});

it("falls back to the default appearance when storage is empty", () => {
  const { result } = renderHook(useDesktopAppearance);
  expect(result.current.theme).toBe("theme-white");
  expect(result.current.liquidGlassStyle).toBe("crystal");
  expect(result.current.liquidGlassIntensity).toBe("balanced");
  expect(result.current.wallpaper).toBe("orbit");
});

it("migrates the four legacy keys so an existing user keeps their preferences", () => {
  localStorage.setItem("echo-desktop-theme", "theme-dark");
  localStorage.setItem("echo:liquid-glass-style", "softlight");
  localStorage.setItem("echo:liquid-glass-intensity", "strong");
  localStorage.setItem("echo:desktop-wallpaper", "sunset");
  localStorage.setItem(
    LIQUID_GLASS_TUNING_STORAGE_KEY,
    JSON.stringify({ transparency: 55, frost: 40 }),
  );

  const { result } = renderHook(useDesktopAppearance);

  expect(result.current.theme).toBe("theme-dark");
  expect(result.current.liquidGlassStyle).toBe("softlight");
  expect(result.current.liquidGlassIntensity).toBe("strong");
  expect(result.current.wallpaper).toBe("sunset");
  expect(result.current.liquidGlassTuning.transparency).toBe(55);
  expect(result.current.liquidGlassTuning.frost).toBe(40);
});

it("maps retired wallpaper aliases onto the current variants", () => {
  localStorage.setItem("echo:desktop-wallpaper", "sequoia");
  const { result } = renderHook(useDesktopAppearance);
  expect(result.current.wallpaper).toBe("aurora");
});

it("prefers the unified key over the legacy keys once it exists", () => {
  localStorage.setItem(
    APPEARANCE_KEY,
    JSON.stringify({ theme: "theme-glass", wallpaper: "midnight" }),
  );
  // 旧键留着旧值，不应再影响结果。
  localStorage.setItem("echo-desktop-theme", "theme-dark");
  localStorage.setItem("echo:desktop-wallpaper", "orbit");

  const { result } = renderHook(useDesktopAppearance);
  expect(result.current.theme).toBe("theme-glass");
  expect(result.current.wallpaper).toBe("midnight");
});

it("links the glass preset when the theme changes", () => {
  const { result } = renderHook(useDesktopAppearance);
  act(() => result.current.setTheme("theme-dark"));

  // 暗夜黑必须配 crystal + strong，否则玻璃在深底上会发灰。
  expect(result.current.liquidGlassStyle).toBe("crystal");
  expect(result.current.liquidGlassIntensity).toBe("strong");
  expect(document.documentElement.classList.contains("theme-dark")).toBe(true);
});

it("keeps the glass preset when the caller opts out of linking", () => {
  const { result } = renderHook(useDesktopAppearance);
  act(() => result.current.setLiquidGlassStyle("softlight"));
  act(() => result.current.setTheme("theme-dark", { linkGlass: false }));

  expect(result.current.theme).toBe("theme-dark");
  expect(result.current.liquidGlassStyle).toBe("softlight");
});

it("writes the unified key and mirrors the legacy keys for existing readers", () => {
  const { result } = renderHook(useDesktopAppearance);
  act(() => result.current.setWallpaper("aurora"));

  expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY) ?? "{}")).toMatchObject(
    { wallpaper: "aurora" },
  );
  expect(localStorage.getItem("echo:desktop-wallpaper")).toBe("aurora");
});

it("cycles wallpapers in a stable order", () => {
  const { result } = renderHook(useDesktopAppearance);
  const seen: string[] = [];
  for (let step = 0; step < 4; step += 1) {
    act(() => result.current.cycleWallpaper());
    seen.push(result.current.wallpaper);
  }
  expect(seen).toEqual(["aurora", "sunset", "midnight", "orbit"]);
});

it("normalizes an out-of-range tuning patch instead of persisting it raw", () => {
  const { result } = renderHook(useDesktopAppearance);
  act(() => result.current.patchLiquidGlassTuning({ transparency: 999 }));

  // 上限是 100，超出必须被夹紧。
  expect(result.current.liquidGlassTuning.transparency).toBe(100);
  expect(result.current.liquidGlassUsesNativeDefaults).toBe(false);
});

it("restores the stock glass look on reset", () => {
  const { result } = renderHook(useDesktopAppearance);
  act(() => result.current.setLiquidGlassIntensity("strong"));
  act(() => result.current.patchLiquidGlassTuning({ frost: 64 }));
  act(() => result.current.resetLiquidGlass());

  expect(result.current.liquidGlassStyle).toBe("crystal");
  expect(result.current.liquidGlassIntensity).toBe("balanced");
  expect(result.current.liquidGlassUsesNativeDefaults).toBe(true);
});

it("survives a corrupt unified payload without throwing", () => {
  localStorage.setItem(APPEARANCE_KEY, "{not json");
  const { result } = renderHook(useDesktopAppearance);
  expect(result.current.theme).toBe("theme-white");
  expect(result.current.wallpaper).toBe("orbit");
});
