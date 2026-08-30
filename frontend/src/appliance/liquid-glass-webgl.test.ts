import { createElement } from "react";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  calculateLiquidScissor,
  calculateCoverTransform,
  createLiquidGlassFrameScheduler,
  liquidGlassShaderTuning,
  liquidGlassSurfaceOptics,
  liquidIntensityScale,
  LIQUID_GLASS_INTERACTION_SETTLE_MS,
  MacLiquidGlassWebGL,
  nativeLiquidGlassOwnsOptics,
  roundedSuperellipseDistance,
} from "./liquid-glass-webgl";
import { calculateDockGlassBounds } from "./liquid-glass-surfaces";

describe("Liquid Glass WebGL geometry", () => {
  it("matches CSS cover/center when the viewport is taller than the image", () => {
    const transform = calculateCoverTransform(1298, 994, 2400, 1332);

    expect(transform.scale).toBeCloseTo(994 / 1332, 8);
    expect(transform.cropX).toBeCloseTo((2400 * (994 / 1332) - 1298) / 2, 8);
    expect(transform.cropY).toBeCloseTo(0, 8);
  });

  it("matches CSS cover/center when the viewport is wider than the image", () => {
    const transform = calculateCoverTransform(1600, 720, 1200, 900);

    expect(transform.scale).toBeCloseTo(1600 / 1200, 8);
    expect(transform.cropX).toBeCloseTo(0, 8);
    expect(transform.cropY).toBeCloseTo(240, 8);
  });

  it("keeps invalid dimensions finite for the fallback path", () => {
    expect(calculateCoverTransform(0, 720, 1200, 900)).toEqual({
      scale: 1,
      cropX: 0,
      cropY: 0,
    });
  });

  it("maps the three user-facing intensity levels to bounded shader scales", () => {
    expect(liquidIntensityScale("weak")).toBe(0.72);
    expect(liquidIntensityScale("balanced")).toBe(1);
    expect(liquidIntensityScale("strong")).toBe(1.28);
    expect(liquidIntensityScale(undefined)).toBe(1);
  });

  it("maps custom optical controls to safe shader values", () => {
    const tuning = liquidGlassShaderTuning({
      liquidTransparency: "72",
      liquidRefraction: "100",
      liquidFrost: "64",
      liquidThickness: "24",
      liquidDispersion: "40",
      liquidSaturation: "150",
      liquidTint: "#ff8040",
      liquidTintStrength: "20",
    });

    expect(tuning).toMatchObject({
      ior: 1.8,
      thickness: 24,
      roughness: 1,
      dispersion: 0.04,
      absorption: 0.5,
      opacity: 0.7496,
      saturation: 1.5,
      tint: [1, 128 / 255, 64 / 255],
    });
  });

  it("keeps refraction visible when material fill reaches zero", () => {
    const tuning = liquidGlassShaderTuning({
      liquidTransparency: "100",
      liquidRefraction: "65",
      liquidFrost: "4",
      liquidThickness: "12",
      liquidDispersion: "12",
      liquidSaturation: "140",
      liquidTint: "#ffffff",
      liquidTintStrength: "0",
    });

    expect(tuning.ior).toBeCloseTo(1.52, 8);
    expect(tuning.thickness).toBe(12);
    expect(tuning.roughness).toBeCloseTo(0.0625, 8);
    expect(tuning.dispersion).toBeCloseTo(0.012, 8);
    expect(tuning.opacity).toBeCloseTo(0.66, 8);
    expect(tuning.absorption).toBe(0);
  });

  it("keeps thin trays optically lighter than cards and windows", () => {
    const dock = liquidGlassSurfaceOptics("thin", true);
    const widget = liquidGlassSurfaceOptics("thick-dark");
    const window = liquidGlassSurfaceOptics("ultra-thick");

    expect(dock.thicknessScale).toBeLessThan(widget.thicknessScale);
    expect(widget.thicknessScale).toBeLessThan(window.thicknessScale);
    expect(dock.alpha).toBeLessThan(widget.alpha);
  });

  it("retires WebGL only when a complete native material owns the optics", () => {
    expect(nativeLiquidGlassOwnsOptics("ready", "appkit")).toBe(true);
    expect(nativeLiquidGlassOwnsOptics("ready", "kwin-wayland-effect")).toBe(
      true,
    );
    expect(nativeLiquidGlassOwnsOptics("ready", "kwin-x11")).toBe(false);
    expect(nativeLiquidGlassOwnsOptics(undefined, "kwin-wayland-effect")).toBe(
      false,
    );
  });

  it("uses a fourth-order continuous corner instead of a circular arc", () => {
    const radius = 20;
    const cornerCoordinate = 30 + radius / Math.pow(2, 1 / 4);

    expect(
      roundedSuperellipseDistance(
        cornerCoordinate,
        cornerCoordinate,
        50,
        50,
        radius,
      ),
    ).toBeCloseTo(0, 8);
    expect(roundedSuperellipseDistance(50, 0, 50, 50, radius)).toBeCloseTo(
      0,
      8,
    );
    expect(roundedSuperellipseDistance(51, 0, 50, 50, radius)).toBeCloseTo(
      1,
      8,
    );
  });

  it("clips high-DPI lens bounds to physical canvas pixels", () => {
    expect(
      calculateLiquidScissor(28.8, 1281.6, 284.8, 236.8, 2077, 1590),
    ).toEqual({ x: 28, y: 1281, width: 286, height: 238 });
    expect(calculateLiquidScissor(-5.2, -3.4, 20, 12, 100, 80)).toEqual({
      x: 0,
      y: 0,
      width: 15,
      height: 9,
    });
    expect(calculateLiquidScissor(120, 90, 10, 10, 100, 80)).toBeNull();
  });

  it("aligns the optical lens with the Dock's painted tray", () => {
    const tray = calculateDockGlassBounds(
      new DOMRect(100, 700, 600, 68),
      552,
      68,
    );

    expect({
      x: tray.x,
      y: tray.y,
      width: tray.width,
      height: tray.height,
      bottom: tray.bottom,
    }).toEqual({ x: 124, y: 701, width: 552, height: 68, bottom: 769 });
  });

  it("keeps the SVG/CSS path active when WebGL2 is unavailable", () => {
    const { container } = render(
      createElement(
        "div",
        { className: "macos-desktop-root" },
        createElement(MacLiquidGlassWebGL),
      ),
    );

    expect(container.firstElementChild).toHaveAttribute(
      "data-liquid-webgl",
      "fallback",
    );
    expect(container.querySelector("canvas")).toHaveAttribute(
      "data-liquid-webgl",
      "fallback",
    );
  });
});

describe("Liquid Glass idle frame scheduling", () => {
  it("coalesces requests and freezes after the interaction settle window", () => {
    let clock = 0;
    let nextHandle = 1;
    const pending = new Map<number, FrameRequestCallback>();
    const renderedAt: number[] = [];
    const states: string[] = [];
    const scheduler = createLiquidGlassFrameScheduler({
      requestFrame(callback) {
        const handle = nextHandle++;
        pending.set(handle, callback);
        return handle;
      },
      cancelFrame(handle) {
        pending.delete(handle);
      },
      now: () => clock,
      isVisible: () => true,
      render(timestamp) {
        renderedAt.push(timestamp);
        return true;
      },
      onStateChange(state) {
        states.push(state);
      },
    });
    const flush = (timestamp: number) => {
      clock = timestamp;
      const callbacks = [...pending.values()];
      pending.clear();
      callbacks.forEach((callback) => callback(timestamp));
    };

    scheduler.request(LIQUID_GLASS_INTERACTION_SETTLE_MS);
    scheduler.request(LIQUID_GLASS_INTERACTION_SETTLE_MS);
    expect(pending).toHaveLength(1);

    flush(0);
    flush(90);
    flush(LIQUID_GLASS_INTERACTION_SETTLE_MS);

    expect(renderedAt).toEqual([0, 90, LIQUID_GLASS_INTERACTION_SETTLE_MS]);
    expect(pending).toHaveLength(0);
    expect(states.at(-1)).toBe("idle");
  });

  it("cancels pending work while the page is suspended", () => {
    let visible = true;
    let nextHandle = 1;
    const pending = new Map<number, FrameRequestCallback>();
    const states: string[] = [];
    const scheduler = createLiquidGlassFrameScheduler({
      requestFrame(callback) {
        const handle = nextHandle++;
        pending.set(handle, callback);
        return handle;
      },
      cancelFrame(handle) {
        pending.delete(handle);
      },
      now: () => 0,
      isVisible: () => visible,
      render: () => true,
      onStateChange(state) {
        states.push(state);
      },
    });

    scheduler.request(LIQUID_GLASS_INTERACTION_SETTLE_MS);
    expect(pending).toHaveLength(1);
    visible = false;
    scheduler.suspend();

    expect(pending).toHaveLength(0);
    expect(states.at(-1)).toBe("suspended");
  });

  it("does not keep a burst alive when there are no visible lenses", () => {
    let callback: FrameRequestCallback | null = null;
    const scheduler = createLiquidGlassFrameScheduler({
      requestFrame(next) {
        callback = next;
        return 1;
      },
      cancelFrame: () => undefined,
      now: () => 0,
      isVisible: () => true,
      render: () => false,
    });

    scheduler.request(LIQUID_GLASS_INTERACTION_SETTLE_MS);
    const scheduled = callback;
    callback = null;
    scheduled?.(0);

    expect(callback).toBeNull();
  });
});
