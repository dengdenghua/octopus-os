import { act, render, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createLiquidDisplacementPixels,
  MacLiquidGlassOptics,
} from "./liquid-glass-optics";
import { LIQUID_GLASS_MOTION_EVENT } from "./liquid-glass-motion";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function pixelAt(
  map: ReturnType<typeof createLiquidDisplacementPixels>,
  x: number,
  y: number,
) {
  const index = (y * map.width + x) * 4;
  return Array.from(map.pixels.slice(index, index + 4));
}

describe("createLiquidDisplacementPixels", () => {
  const profile = {
    width: 96,
    height: 48,
    cornerRadius: 14,
    edgeWidth: 12,
    maxOffset: 6,
  };

  it("keeps the clear centre neutral", () => {
    const map = createLiquidDisplacementPixels(profile);
    const centre = pixelAt(map, profile.width / 2, profile.height / 2);

    expect(centre[0]).toBeCloseTo(128, 0);
    expect(centre[1]).toBeCloseTo(128, 0);
    expect(centre[2]).toBe(0);
    expect(centre[3]).toBe(255);
  });

  it("bends samples inward along the glass rim", () => {
    const map = createLiquidDisplacementPixels(profile);
    const left = pixelAt(map, 1, profile.height / 2);
    const top = pixelAt(map, profile.width / 2, 1);

    expect(left[0]).toBeGreaterThan(128);
    expect(left[2]).toBeGreaterThan(200);
    expect(top[1]).toBeGreaterThan(128);
    expect(top[2]).toBeGreaterThan(200);
  });

  it("leaves pixels outside rounded corners neutral", () => {
    const map = createLiquidDisplacementPixels(profile);

    expect(pixelAt(map, 0, 0)).toEqual([128, 128, 0, 255]);
  });
});

describe("MacLiquidGlassOptics", () => {
  it("keeps every optical profile but removes animation for reduced motion", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: true,
        media: "(prefers-reduced-motion: reduce)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      () =>
        ({
          createImageData: (width: number, height: number) => ({
            data: new Uint8ClampedArray(width * height * 4),
          }),
          putImageData: vi.fn(),
        }) as unknown as CanvasRenderingContext2D,
    );
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
      "data:image/png;base64,AA==",
    );

    const { container } = render(createElement(MacLiquidGlassOptics));
    const optics = container.querySelector<SVGSVGElement>(
      "[data-liquid-optics]",
    );

    await waitFor(() =>
      expect(optics).toHaveAttribute("data-liquid-optics", "ready"),
    );
    expect(
      Array.from(container.querySelectorAll("filter"), (filter) => filter.id),
    ).toEqual([
      "echo-liquid-dock-refraction",
      "echo-liquid-dock-transmission",
      "echo-liquid-wide-refraction",
      "echo-liquid-compact-refraction",
      "echo-liquid-compact-transmission",
    ]);
    expect(container.querySelectorAll("animate")).toHaveLength(0);
  });

  it("keeps the noise field static until a motion sample arrives", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: false,
        media: "(prefers-reduced-motion: reduce)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      () =>
        ({
          createImageData: (width: number, height: number) => ({
            data: new Uint8ClampedArray(width * height * 4),
          }),
          putImageData: vi.fn(),
        }) as unknown as CanvasRenderingContext2D,
    );
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
      "data:image/png;base64,AA==",
    );

    const { container } = render(createElement(MacLiquidGlassOptics));
    const optics = container.querySelector<SVGSVGElement>(
      "[data-liquid-optics]",
    );

    await waitFor(() =>
      expect(optics).toHaveAttribute("data-liquid-optics", "ready"),
    );

    // Idle: an animated feTurbulence inside backdrop-filter would regenerate
    // fractal noise every frame, so nothing may animate before interaction.
    expect(optics).toHaveAttribute("data-liquid-optics-motion", "idle");
    expect(container.querySelectorAll("animate")).toHaveLength(0);

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(LIQUID_GLASS_MOTION_EVENT, {
          detail: { x: 4, y: 2, energy: 0.6, settleMs: 320, source: "dock" },
        }),
      );
    });

    expect(optics).toHaveAttribute("data-liquid-optics-motion", "active");
    expect(container.querySelectorAll("animate").length).toBeGreaterThan(0);
  });
});
