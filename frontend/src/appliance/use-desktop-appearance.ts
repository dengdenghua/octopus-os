/**
 * 桌面外观统一偏好 hook。
 *
 * 把原先四套独立的 localStorage 键合并为一个 `DesktopAppearance` 对象
 * （`echo:desktop-appearance`），一次读写；主题切换联动玻璃参数，
 * 消除 theme/liquid-glass-style/intensity/wallpaper 碎片化导致的视觉打架。
 *
 * 兼容策略：首次读取时如果新 key 不存在，从旧四个 key 迁移并写回新 key，
 * 旧 key 保留（别的组件可能还在读），后续以新 key 为准。
 */

import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";
import type { CSSProperties } from "react";

import type { DesktopTheme } from "./intent-desktop-surface";
import {
  DEFAULT_LIQUID_GLASS_TUNING,
  isDefaultLiquidGlassTuning,
  LIQUID_GLASS_TUNING_STORAGE_KEY,
  liquidGlassCssVariables,
  normalizeLiquidGlassTuning,
  parseLiquidGlassTuning,
  type LiquidGlassTuning,
} from "./liquid-glass-settings";
import type {
  MacLiquidGlassIntensity,
  MacLiquidGlassStyle,
} from "./macos-shell";

export type WallpaperVariant = "orbit" | "aurora" | "sunset" | "midnight";

export type DesktopAppearance = {
  theme: DesktopTheme;
  liquidGlassStyle: MacLiquidGlassStyle;
  liquidGlassIntensity: MacLiquidGlassIntensity;
  liquidGlassTuning: LiquidGlassTuning;
  wallpaper: WallpaperVariant;
};

const APPEARANCE_STORAGE_KEY = "echo:desktop-appearance";

const DEFAULT_APPEARANCE: DesktopAppearance = {
  theme: "theme-white",
  liquidGlassStyle: "crystal",
  liquidGlassIntensity: "balanced",
  liquidGlassTuning: DEFAULT_LIQUID_GLASS_TUNING,
  wallpaper: "orbit",
};

const THEME_NAMES: Record<DesktopTheme, string> = {
  "theme-white": "象牙白",
  "theme-glass": "液态玻璃",
  "theme-dark": "暗夜黑",
};

/** 主题与玻璃参数的联动预设：切主题时把玻璃调到视觉协调的组合，并联动壁纸。 */
const THEME_GLASS_PRESETS: Record<
  DesktopTheme,
  Pick<DesktopAppearance, "liquidGlassStyle" | "liquidGlassIntensity" | "wallpaper">
> = {
  "theme-white": { liquidGlassStyle: "softlight", liquidGlassIntensity: "weak", wallpaper: "orbit" },
  "theme-glass": { liquidGlassStyle: "crystal", liquidGlassIntensity: "balanced", wallpaper: "aurora" },
  "theme-dark": { liquidGlassStyle: "crystal", liquidGlassIntensity: "strong", wallpaper: "midnight" },
};

function normalizeWallpaper(value: string | null): WallpaperVariant {
  // 旧值迁移：tahoe→orbit, sequoia→aurora, sonoma→sunset
  if (value === "tahoe") return "orbit";
  if (value === "sequoia") return "aurora";
  if (value === "sonoma") return "sunset";
  return value === "orbit" ||
    value === "aurora" ||
    value === "sunset" ||
    value === "midnight"
    ? value
    : "orbit";
}

function normalizeTheme(value: string | null): DesktopTheme {
  return value === "theme-white" ||
    value === "theme-glass" ||
    value === "theme-dark"
    ? value
    : "theme-white";
}

function normalizeStyle(value: string | null): MacLiquidGlassStyle {
  return value === "softlight" || value === "harmony" ? "softlight" : "crystal";
}

function normalizeIntensity(value: string | null): MacLiquidGlassIntensity {
  return value === "weak" || value === "strong" ? value : "balanced";
}

function loadAppearance(): DesktopAppearance {
  if (typeof window === "undefined") return DEFAULT_APPEARANCE;
  try {
    const raw = localStorage.getItem(APPEARANCE_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<DesktopAppearance>;
      return {
        theme: normalizeTheme(parsed.theme ?? null),
        liquidGlassStyle: normalizeStyle(parsed.liquidGlassStyle ?? null),
        liquidGlassIntensity: normalizeIntensity(
          parsed.liquidGlassIntensity ?? null,
        ),
        liquidGlassTuning: parsed.liquidGlassTuning
          ? normalizeLiquidGlassTuning(parsed.liquidGlassTuning)
          : DEFAULT_LIQUID_GLASS_TUNING,
        wallpaper: normalizeWallpaper(parsed.wallpaper ?? null),
      };
    }
    // 旧四键迁移
    const migrated: DesktopAppearance = {
      theme: normalizeTheme(localStorage.getItem("echo-desktop-theme")),
      liquidGlassStyle: normalizeStyle(
        localStorage.getItem("echo:liquid-glass-style"),
      ),
      liquidGlassIntensity: normalizeIntensity(
        localStorage.getItem("echo:liquid-glass-intensity"),
      ),
      liquidGlassTuning: parseLiquidGlassTuning(
        localStorage.getItem(LIQUID_GLASS_TUNING_STORAGE_KEY),
      ),
      wallpaper: normalizeWallpaper(
        localStorage.getItem("echo:desktop-wallpaper"),
      ),
    };
    return migrated;
  } catch {
    return DEFAULT_APPEARANCE;
  }
}

function persistAppearance(appearance: DesktopAppearance) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(appearance));
    // 同步旧键，保证还在读旧键的组件（如 liquid-glass-settings 面板）一致
    localStorage.setItem("echo-desktop-theme", appearance.theme);
    localStorage.setItem("echo:liquid-glass-style", appearance.liquidGlassStyle);
    localStorage.setItem(
      "echo:liquid-glass-intensity",
      appearance.liquidGlassIntensity,
    );
    localStorage.setItem(
      LIQUID_GLASS_TUNING_STORAGE_KEY,
      JSON.stringify(appearance.liquidGlassTuning),
    );
    localStorage.setItem("echo:desktop-wallpaper", appearance.wallpaper);
  } catch {
    // localStorage 不可用时静默降级为会话内状态
  }
}

function applyThemeClass(theme: DesktopTheme) {
  if (typeof document === "undefined") return;
  document.documentElement.classList.remove(
    "theme-white",
    "theme-glass",
    "theme-dark",
  );
  document.documentElement.classList.add(theme);
}

export function useDesktopAppearance() {
  const [appearance, setAppearance] = useState<DesktopAppearance>(loadAppearance);

  const update = useCallback((patch: Partial<DesktopAppearance>) => {
    setAppearance((current) => {
      const next: DesktopAppearance = {
        ...current,
        ...patch,
        liquidGlassTuning: patch.liquidGlassTuning
          ? normalizeLiquidGlassTuning(patch.liquidGlassTuning)
          : current.liquidGlassTuning,
      };
      persistAppearance(next);
      if (patch.theme && patch.theme !== current.theme) {
        applyThemeClass(patch.theme);
      }
      return next;
    });
  }, []);

  const setTheme = useCallback(
    (theme: DesktopTheme, options?: { linkGlass?: boolean; silent?: boolean }) => {
      const linkGlass = options?.linkGlass ?? true;
      update({
        theme,
        ...(linkGlass ? THEME_GLASS_PRESETS[theme] : {}),
      });
      if (!options?.silent) {
        toast.success(`桌面主题已切换为：${THEME_NAMES[theme]}`);
      }
    },
    [update],
  );

  const toggleTheme = useCallback(() => {
    const cycle: Record<DesktopTheme, DesktopTheme> = {
      "theme-white": "theme-glass",
      "theme-glass": "theme-dark",
      "theme-dark": "theme-white",
    };
    setTheme(cycle[appearance.theme] ?? "theme-white");
  }, [appearance.theme, setTheme]);

  const setLiquidGlassStyle = useCallback(
    (liquidGlassStyle: MacLiquidGlassStyle) => update({ liquidGlassStyle }),
    [update],
  );
  const setLiquidGlassIntensity = useCallback(
    (liquidGlassIntensity: MacLiquidGlassIntensity) =>
      update({ liquidGlassIntensity }),
    [update],
  );
  const patchLiquidGlassTuning = useCallback(
    (patch: Partial<LiquidGlassTuning>) =>
      update({
        liquidGlassTuning: { ...appearance.liquidGlassTuning, ...patch },
      }),
    [appearance.liquidGlassTuning, update],
  );
  const setWallpaper = useCallback(
    (wallpaper: WallpaperVariant) => update({ wallpaper }),
    [update],
  );

  const cycleWallpaper = useCallback(() => {
    const order: WallpaperVariant[] = ["orbit", "aurora", "sunset", "midnight"];
    const index = order.indexOf(appearance.wallpaper);
    setWallpaper(order[(index + 1) % order.length] ?? "orbit");
  }, [appearance.wallpaper, setWallpaper]);

  const resetLiquidGlass = useCallback(() => {
    update({
      liquidGlassStyle: "crystal",
      liquidGlassIntensity: "balanced",
      liquidGlassTuning: DEFAULT_LIQUID_GLASS_TUNING,
    });
  }, [update]);

  const liquidGlassVariables = useMemo(
    () => liquidGlassCssVariables(appearance.liquidGlassTuning) as CSSProperties,
    [appearance.liquidGlassTuning],
  );
  const liquidGlassUsesNativeDefaults = isDefaultLiquidGlassTuning(
    appearance.liquidGlassTuning,
  );

  return {
    ...appearance,
    setTheme,
    toggleTheme,
    setLiquidGlassStyle,
    setLiquidGlassIntensity,
    patchLiquidGlassTuning,
    setWallpaper,
    cycleWallpaper,
    resetLiquidGlass,
    liquidGlassVariables,
    liquidGlassUsesNativeDefaults,
  };
}
