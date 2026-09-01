export type LiquidGlassTuning = Readonly<{
  transparency: number;
  frost: number;
  refraction: number;
  thickness: number;
  dispersion: number;
  saturation: number;
  tint: string;
  tintStrength: number;
}>;

export const LIQUID_GLASS_TUNING_STORAGE_KEY = "echo:liquid-glass-tuning";

export const DEFAULT_LIQUID_GLASS_TUNING: LiquidGlassTuning = {
  transparency: 72,
  frost: 32,
  refraction: 60,
  thickness: 8,
  dispersion: 8,
  saturation: 125,
  tint: "#dbeeff",
  tintStrength: 12,
};

export const CLEAR_LIQUID_GLASS_TUNING: LiquidGlassTuning = {
  transparency: 96,
  frost: 8,
  refraction: 61,
  thickness: 8,
  dispersion: 6,
  saturation: 122,
  tint: "#dbeeff",
  tintStrength: 3,
};

export const LIQUID_GLASS_TINTS = [
  { label: "无色", value: "#ffffff" },
  { label: "冰蓝", value: "#dbeeff" },
  { label: "暖金", value: "#ffe4b8" },
  { label: "暮紫", value: "#eadcff" },
  { label: "烟灰", value: "#cbd3df" },
] as const;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function finiteOr(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function normalizeLiquidGlassTuning(
  value: Partial<LiquidGlassTuning> | null | undefined,
): LiquidGlassTuning {
  const tint =
    typeof value?.tint === "string" && /^#[0-9a-f]{6}$/i.test(value.tint)
      ? value.tint.toLowerCase()
      : DEFAULT_LIQUID_GLASS_TUNING.tint;

  return {
    transparency: Math.round(
      clamp(
        finiteOr(value?.transparency, DEFAULT_LIQUID_GLASS_TUNING.transparency),
        35,
        100,
      ),
    ),
    frost: Math.round(
      clamp(finiteOr(value?.frost, DEFAULT_LIQUID_GLASS_TUNING.frost), 0, 64),
    ),
    refraction: Math.round(
      clamp(
        finiteOr(value?.refraction, DEFAULT_LIQUID_GLASS_TUNING.refraction),
        0,
        100,
      ),
    ),
    thickness: Math.round(
      clamp(
        finiteOr(value?.thickness, DEFAULT_LIQUID_GLASS_TUNING.thickness),
        1,
        24,
      ),
    ),
    dispersion: Math.round(
      clamp(
        finiteOr(value?.dispersion, DEFAULT_LIQUID_GLASS_TUNING.dispersion),
        0,
        40,
      ),
    ),
    saturation: Math.round(
      clamp(
        finiteOr(value?.saturation, DEFAULT_LIQUID_GLASS_TUNING.saturation),
        70,
        180,
      ),
    ),
    tint,
    tintStrength: Math.round(
      clamp(
        finiteOr(value?.tintStrength, DEFAULT_LIQUID_GLASS_TUNING.tintStrength),
        0,
        40,
      ),
    ),
  };
}

export function parseLiquidGlassTuning(
  serialized: string | null,
): LiquidGlassTuning {
  if (!serialized) return DEFAULT_LIQUID_GLASS_TUNING;
  try {
    const normalized = normalizeLiquidGlassTuning(
      JSON.parse(serialized) as Partial<LiquidGlassTuning>,
    );
    // Migrate the original "clear" preset. Its 12 mm slab, 1.52 IOR and
    // 140% saturation made thin surfaces inherit too much wallpaper colour.
    if (
      normalized.transparency === 100 &&
      normalized.frost <= 4 &&
      normalized.refraction === 65 &&
      normalized.thickness === 12 &&
      normalized.dispersion === 12 &&
      normalized.saturation === 140 &&
      normalized.tintStrength === 0
    ) {
      return CLEAR_LIQUID_GLASS_TUNING;
    }
    return normalized;
  } catch {
    return DEFAULT_LIQUID_GLASS_TUNING;
  }
}

export function liquidGlassCssVariables(
  tuning: LiquidGlassTuning,
): Record<`--${string}`, string> {
  const fill = 100 - tuning.transparency;
  const percentage = (value: number) => `${Math.round(value)}%`;
  const pixels = (value: number) => `${Math.round(value)}px`;

  /**
   * Wraps a computed fill in a floor the stylesheet can raise.
   *
   * These tokens land as inline styles on the desktop root, so a media query
   * cannot override them directly. It can, however, set the floor variable they
   * reference: the floor defaults to 0% and the computed value stays the second
   * argument, so user tuning wins whenever it already exceeds the floor.
   */
  const withFloor = (name: string, value: number) =>
    `max(var(--liquid-fill-floor-${name}, 0%), ${percentage(value)})`;

  return {
    "--liquid-blur-ultra-thin": pixels(tuning.frost * 0.48),
    "--liquid-blur-thin": pixels(tuning.frost * 0.76),
    "--liquid-blur-thick": pixels(tuning.frost * 1.18),
    "--liquid-blur-ultra-thick": pixels(tuning.frost * 1.55),
    "--liquid-saturation": percentage(tuning.saturation),
    // Mixed against --liquid-film-base rather than a literal white, so dark
    // mode can flip the film's polarity without touching the tuning values.
    "--liquid-custom-colour": `color-mix(in srgb, var(--liquid-film-base, white), ${tuning.tint} ${tuning.tintStrength}%)`,
    "--liquid-fill-ultra-thin": withFloor(
      "ultra-thin",
      clamp(fill * 0.65, 0, 48),
    ),
    "--liquid-fill-thin": withFloor("thin", clamp(fill * 0.85, 0, 58)),
    "--liquid-fill-thick": withFloor("thick", clamp(fill * 1.55, 0, 82)),
    "--liquid-fill-ultra-thick": withFloor(
      "ultra-thick",
      clamp(fill * 2.15, 0, 94),
    ),
    "--liquid-fill-inner": withFloor("inner", clamp(fill * 1.35, 0, 76)),
  };
}

export function isDefaultLiquidGlassTuning(tuning: LiquidGlassTuning): boolean {
  return (
    tuning.transparency === DEFAULT_LIQUID_GLASS_TUNING.transparency &&
    tuning.frost === DEFAULT_LIQUID_GLASS_TUNING.frost &&
    tuning.refraction === DEFAULT_LIQUID_GLASS_TUNING.refraction &&
    tuning.thickness === DEFAULT_LIQUID_GLASS_TUNING.thickness &&
    tuning.dispersion === DEFAULT_LIQUID_GLASS_TUNING.dispersion &&
    tuning.saturation === DEFAULT_LIQUID_GLASS_TUNING.saturation &&
    tuning.tint === DEFAULT_LIQUID_GLASS_TUNING.tint &&
    tuning.tintStrength === DEFAULT_LIQUID_GLASS_TUNING.tintStrength
  );
}
