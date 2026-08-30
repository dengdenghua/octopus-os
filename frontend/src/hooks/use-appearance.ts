import { swallow } from "@/core/utils/log";
import { useCallback, useEffect, useState } from "react";

/**
 * Appearance preferences:
 *
 *   - cornerScale: global multiplier on all radius tokens (0.5–1.5).
 *       0.5 = crisp, 1 = default, 1.5 = pill / friendly.
 *   - density: global base font size and spacing scale for information
 *       density, from relaxed to ultra-dense.
 *
 * Persisted to localStorage and applied as data-* attributes on <html>
 * so any Tailwind/CSS rule can react via data attributes.
 */

export type CornerScale = 0.5 | 0.75 | 1 | 1.25 | 1.5;
export type Density =
  | "relaxed"
  | "comfortable"
  | "compact"
  | "dense"
  | "ultradense";

/** 配色主题：rouge = 蔷薇粉（默认），steel = 冷钢蓝，emerald/violet/amber/teal = 高级预设，custom = 用户自定义主色。 */
export type Palette =
  | "rouge"
  | "apricot"
  | "violet"
  | "mint"
  | "steel"
  | "teal"
  | "emerald"
  | "amber"
  | "custom";

const CORNER_KEY = "echo-corner-scale";
const DENSITY_KEY = "echo-density";
const PALETTE_KEY = "echo-palette";
const CUSTOM_COLOR_KEY = "echo-custom-color";
const APPEARANCE_CHANGE_EVENT = "echo:appearance-change";

const DEFAULT_CORNER: CornerScale = 1;
const DEFAULT_DENSITY: Density = "comfortable";
const DEFAULT_PALETTE: Palette = "rouge";
const DEFAULT_CUSTOM_COLOR = "#3e6fd8";

/** 主色相关的 CSS 变量；自定义配色时覆盖这一组即可,其余 token 沿用基础主题。 */
const CUSTOM_PALETTE_VARS = [
  "--primary",
  "--primary-foreground",
  "--ring",
  "--sidebar-primary",
  "--sidebar-primary-foreground",
  "--sidebar-ring",
  "--chart-1",
] as const;

/** 根据色值亮度返回可读前景色（深色字 / 浅色字）,保证主色上的文字对比度达标。 */
function readableForeground(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "#f8fafc";
  const int = parseInt(m[1] ?? "0", 16);
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  const r = channel((int >> 16) & 255);
  const g = channel((int >> 8) & 255);
  const b = channel(int & 255);
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return luminance > 0.45 ? "#1f2937" : "#f8fafc";
}
const DENSITY_TOKENS: Record<
  Density,
  {
    baseFontSize: string;
    cardMinHeight: string;
    gap: string;
    headerPaddingX: string;
    headerPaddingY: string;
    pagePadding: string;
    panelPadding: string;
    rowPaddingX: string;
    rowPaddingY: string;
  }
> = {
  relaxed: {
    baseFontSize: "16px",
    rowPaddingY: "calc(var(--spacing) * 1.75)",
    rowPaddingX: "calc(var(--spacing) * 3)",
    gap: "calc(var(--spacing) * 5)",
    pagePadding: "calc(var(--spacing) * 5)",
    panelPadding: "calc(var(--spacing) * 5)",
    headerPaddingY: "calc(var(--spacing) * 3.5)",
    headerPaddingX: "calc(var(--spacing) * 5.5)",
    cardMinHeight: "204px",
  },
  comfortable: {
    baseFontSize: "15px",
    rowPaddingY: "calc(var(--spacing) * 1.5)",
    rowPaddingX: "calc(var(--spacing) * 2.5)",
    gap: "calc(var(--spacing) * 4)",
    pagePadding: "calc(var(--spacing) * 4)",
    panelPadding: "calc(var(--spacing) * 4)",
    headerPaddingY: "calc(var(--spacing) * 3)",
    headerPaddingX: "calc(var(--spacing) * 5)",
    cardMinHeight: "184px",
  },
  compact: {
    baseFontSize: "14px",
    rowPaddingY: "calc(var(--spacing) * 1.25)",
    rowPaddingX: "calc(var(--spacing) * 2)",
    gap: "calc(var(--spacing) * 3)",
    pagePadding: "calc(var(--spacing) * 3.5)",
    panelPadding: "calc(var(--spacing) * 3.5)",
    headerPaddingY: "calc(var(--spacing) * 2.5)",
    headerPaddingX: "calc(var(--spacing) * 4)",
    cardMinHeight: "168px",
  },
  dense: {
    baseFontSize: "13px",
    rowPaddingY: "calc(var(--spacing) * 1)",
    rowPaddingX: "calc(var(--spacing) * 1.75)",
    gap: "calc(var(--spacing) * 2.5)",
    pagePadding: "calc(var(--spacing) * 3)",
    panelPadding: "calc(var(--spacing) * 3)",
    headerPaddingY: "calc(var(--spacing) * 2)",
    headerPaddingX: "calc(var(--spacing) * 3.5)",
    cardMinHeight: "152px",
  },
  ultradense: {
    baseFontSize: "12.5px",
    rowPaddingY: "calc(var(--spacing) * 0.75)",
    rowPaddingX: "calc(var(--spacing) * 1.5)",
    gap: "calc(var(--spacing) * 2)",
    pagePadding: "calc(var(--spacing) * 2.5)",
    panelPadding: "calc(var(--spacing) * 2.5)",
    headerPaddingY: "calc(var(--spacing) * 1.75)",
    headerPaddingX: "calc(var(--spacing) * 3)",
    cardMinHeight: "136px",
  },
};

function readCorner(): CornerScale {
  if (typeof window === "undefined") return DEFAULT_CORNER;
  const raw = window.localStorage.getItem(CORNER_KEY);
  const v = raw != null ? Number(raw) : NaN;
  return ([0.5, 0.75, 1, 1.25, 1.5] as const).includes(v as CornerScale)
    ? (v as CornerScale)
    : DEFAULT_CORNER;
}

function readDensity(): Density {
  if (typeof window === "undefined") return DEFAULT_DENSITY;
  const raw = window.localStorage.getItem(DENSITY_KEY);
  return isDensity(raw) ? raw : DEFAULT_DENSITY;
}

function isDensity(value: string | null): value is Density {
  return (
    value === "relaxed" ||
    value === "comfortable" ||
    value === "compact" ||
    value === "dense" ||
    value === "ultradense"
  );
}

/** Single source of truth for valid palette ids — keep in sync with the
    [data-theme] blocks in globals.css. */
const PALETTES = [
  "rouge",
  "apricot",
  "violet",
  "mint",
  "steel",
  "teal",
  "emerald",
  "amber",
  "custom",
] as const satisfies readonly Palette[];

function isPalette(value: string | null): value is Palette {
  return (PALETTES as readonly string[]).includes(value ?? "");
}

function readPalette(): Palette {
  if (typeof window === "undefined") return DEFAULT_PALETTE;
  const raw = window.localStorage.getItem(PALETTE_KEY);
  return isPalette(raw) ? raw : DEFAULT_PALETTE;
}

function isHexColor(value: string | null | undefined): value is string {
  return /^#[0-9a-f]{6}$/i.test(value ?? "");
}

function readCustomColor(): string {
  if (typeof window === "undefined") return DEFAULT_CUSTOM_COLOR;
  const raw = window.localStorage.getItem(CUSTOM_COLOR_KEY);
  return isHexColor(raw) ? raw : DEFAULT_CUSTOM_COLOR;
}

function clearCustomPaletteVars(root: HTMLElement) {
  for (const v of CUSTOM_PALETTE_VARS) root.style.removeProperty(v);
}

function applyPalette(palette: Palette, customColor?: string) {
  const root = document.documentElement;
  if (palette === "custom") {
    // 以 rouge 基础变量为底,再用用户色值覆盖主色相关 token。
    root.dataset.theme = "rouge";
    const color = isHexColor(customColor) ? customColor : DEFAULT_CUSTOM_COLOR;
    const fg = readableForeground(color);
    root.style.setProperty("--primary", color);
    root.style.setProperty("--primary-foreground", fg);
    root.style.setProperty("--ring", color);
    root.style.setProperty("--sidebar-primary", color);
    root.style.setProperty("--sidebar-primary-foreground", fg);
    root.style.setProperty("--sidebar-ring", color);
    root.style.setProperty("--chart-1", color);
    return;
  }
  if (palette === DEFAULT_PALETTE) delete root.dataset.theme;
  else root.dataset.theme = palette;
  clearCustomPaletteVars(root);
}

function applyCorner(scale: CornerScale) {
  const root = document.documentElement;
  root.style.setProperty("--corner-radius-scale", String(scale));
  root.dataset.cornerScale = String(scale);
}

function applyDensity(density: Density) {
  const root = document.documentElement;
  const tokens = DENSITY_TOKENS[density];
  root.style.setProperty("--density-base-font-size", tokens.baseFontSize);
  root.style.setProperty("--density-row-padding-y", tokens.rowPaddingY);
  root.style.setProperty("--density-row-padding-x", tokens.rowPaddingX);
  root.style.setProperty("--density-gap", tokens.gap);
  root.style.setProperty("--density-page-padding", tokens.pagePadding);
  root.style.setProperty("--density-panel-padding", tokens.panelPadding);
  root.style.setProperty("--density-header-padding-y", tokens.headerPaddingY);
  root.style.setProperty("--density-header-padding-x", tokens.headerPaddingX);
  root.style.setProperty("--density-card-min-height", tokens.cardMinHeight);
  if (density === DEFAULT_DENSITY) delete root.dataset.density;
  else root.dataset.density = density;
}

function emitAppearanceChange() {
  window.dispatchEvent(new Event(APPEARANCE_CHANGE_EVENT));
}

export function useAppearance() {
  const [cornerScale, setCornerScaleState] =
    useState<CornerScale>(DEFAULT_CORNER);
  const [density, setDensityState] = useState<Density>(DEFAULT_DENSITY);
  const [palette, setPaletteState] = useState<Palette>(DEFAULT_PALETTE);
  const [customColor, setCustomColorState] =
    useState<string>(DEFAULT_CUSTOM_COLOR);

  useEffect(() => {
    const syncFromStorage = () => {
      const c = readCorner();
      const d = readDensity();
      const p = readPalette();
      const pc = readCustomColor();
      setCornerScaleState(c);
      setDensityState(d);
      setPaletteState(p);
      setCustomColorState(pc);
      applyCorner(c);
      applyDensity(d);
      applyPalette(p, pc);
    };

    syncFromStorage();
    window.addEventListener(APPEARANCE_CHANGE_EVENT, syncFromStorage);
    window.addEventListener("storage", syncFromStorage);
    return () => {
      window.removeEventListener(APPEARANCE_CHANGE_EVENT, syncFromStorage);
      window.removeEventListener("storage", syncFromStorage);
    };
  }, []);

  const setCornerScale = useCallback((scale: CornerScale) => {
    setCornerScaleState(scale);
    applyCorner(scale);
    try {
      window.localStorage.setItem(CORNER_KEY, String(scale));
    } catch (e) {
      swallow(e, "storage");
    }
    emitAppearanceChange();
  }, []);

  const setDensity = useCallback((d: Density) => {
    setDensityState(d);
    applyDensity(d);
    try {
      window.localStorage.setItem(DENSITY_KEY, d);
    } catch (e) {
      swallow(e, "storage");
    }
    emitAppearanceChange();
  }, []);

  const setPalette = useCallback((p: Exclude<Palette, "custom">) => {
    setPaletteState(p);
    applyPalette(p);
    try {
      window.localStorage.setItem(PALETTE_KEY, p);
    } catch (e) {
      swallow(e, "storage");
    }
    emitAppearanceChange();
  }, []);

  const setCustomColor = useCallback((hex: string) => {
    const color = isHexColor(hex) ? hex : DEFAULT_CUSTOM_COLOR;
    setPaletteState("custom");
    setCustomColorState(color);
    applyPalette("custom", color);
    try {
      window.localStorage.setItem(PALETTE_KEY, "custom");
      window.localStorage.setItem(CUSTOM_COLOR_KEY, color);
    } catch (e) {
      swallow(e, "storage");
    }
    emitAppearanceChange();
  }, []);

  return {
    cornerScale,
    density,
    palette,
    customColor,
    setCornerScale,
    setDensity,
    setPalette,
    setCustomColor,
  };
}

/** Mount once at app root to hydrate appearance from storage before first paint. */
export function AppearanceBootstrap() {
  useAppearance();
  return null;
}
