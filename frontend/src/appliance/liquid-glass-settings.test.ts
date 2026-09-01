import { describe, expect, it } from "vitest";

import {
  CLEAR_LIQUID_GLASS_TUNING,
  DEFAULT_LIQUID_GLASS_TUNING,
  isDefaultLiquidGlassTuning,
  liquidGlassCssVariables,
  normalizeLiquidGlassTuning,
  parseLiquidGlassTuning,
} from "./liquid-glass-settings";

describe("Liquid Glass tuning", () => {
  it("recovers safely from invalid persisted settings", () => {
    expect(parseLiquidGlassTuning("not-json")).toEqual(
      DEFAULT_LIQUID_GLASS_TUNING,
    );
    expect(
      normalizeLiquidGlassTuning({
        transparency: 200,
        frost: -5,
        refraction: Number.NaN,
        thickness: 99,
        dispersion: -4,
        saturation: 30,
        tint: "red",
        tintStrength: 90,
      }),
    ).toEqual({
      transparency: 100,
      frost: 0,
      refraction: DEFAULT_LIQUID_GLASS_TUNING.refraction,
      thickness: 24,
      dispersion: 0,
      saturation: 70,
      tint: DEFAULT_LIQUID_GLASS_TUNING.tint,
      tintStrength: 40,
    });
  });

  it("maps tuning to bounded CSS material variables", () => {
    const variables = liquidGlassCssVariables(DEFAULT_LIQUID_GLASS_TUNING);

    expect(variables["--liquid-blur-thin"]).toBe("24px");
    expect(variables["--liquid-saturation"]).toBe("125%");
    // Fills carry a floor an accessibility media query can raise. The computed
    // value stays the second max() argument so tuning wins above the floor.
    expect(variables["--liquid-fill-thin"]).toBe(
      "max(var(--liquid-fill-floor-thin, 0%), 24%)",
    );
    expect(variables["--liquid-fill-thick"]).toBe(
      "max(var(--liquid-fill-floor-thick, 0%), 43%)",
    );
    expect(variables["--liquid-custom-colour"]).toContain("#dbeeff 12%");
  });

  it("keeps the clear preset lightly grounded without turning milky", () => {
    const variables = liquidGlassCssVariables(CLEAR_LIQUID_GLASS_TUNING);

    expect(variables["--liquid-fill-ultra-thin"]).toBe(
      "max(var(--liquid-fill-floor-ultra-thin, 0%), 3%)",
    );
    expect(variables["--liquid-fill-thin"]).toBe(
      "max(var(--liquid-fill-floor-thin, 0%), 3%)",
    );
    expect(variables["--liquid-fill-thick"]).toBe(
      "max(var(--liquid-fill-floor-thick, 0%), 6%)",
    );
    expect(variables["--liquid-fill-ultra-thick"]).toBe(
      "max(var(--liquid-fill-floor-ultra-thick, 0%), 9%)",
    );
    expect(variables["--liquid-fill-inner"]).toBe(
      "max(var(--liquid-fill-floor-inner, 0%), 5%)",
    );
    expect(variables["--liquid-blur-thin"]).toBe("6px");
  });

  it("migrates the overly strong first-generation clear preset", () => {
    expect(
      parseLiquidGlassTuning(
        JSON.stringify({
          transparency: 100,
          frost: 0,
          refraction: 65,
          thickness: 12,
          dispersion: 12,
          saturation: 140,
          tint: "#da1616",
          tintStrength: 0,
        }),
      ),
    ).toEqual(CLEAR_LIQUID_GLASS_TUNING);
  });

  it("recognizes when native defaults are untouched", () => {
    expect(isDefaultLiquidGlassTuning(DEFAULT_LIQUID_GLASS_TUNING)).toBe(true);
    expect(
      isDefaultLiquidGlassTuning({
        ...DEFAULT_LIQUID_GLASS_TUNING,
        refraction: 80,
      }),
    ).toBe(false);
  });
});
