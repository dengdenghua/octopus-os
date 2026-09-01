import { describe, expect, it, vi } from "vitest";

import {
  calculateLiquidGlassMotion,
  emitLiquidGlassMotion,
  LIQUID_GLASS_MOTION_EVENT,
} from "./liquid-glass-motion";

describe("liquid glass interaction motion", () => {
  it("converts gesture velocity into a bounded optical impulse", () => {
    expect(calculateLiquidGlassMotion(0, 0, 16)).toEqual({
      x: 0,
      y: 0,
      energy: 0,
      settleMs: 0,
    });

    const sample = calculateLiquidGlassMotion(48, -24, 16);
    expect(sample.x).toBe(1);
    expect(sample.y).toBeLessThan(0);
    expect(sample.energy).toBe(1);
    expect(sample.settleMs).toBe(380);
  });

  it("rejects invalid samples instead of starting an animation loop", () => {
    expect(calculateLiquidGlassMotion(12, 4, 0).settleMs).toBe(0);
    expect(calculateLiquidGlassMotion(Number.NaN, 4, 16).energy).toBe(0);
  });

  it("emits one short optical invalidation with the supplied detail", () => {
    const listener = vi.fn();
    window.addEventListener(LIQUID_GLASS_MOTION_EVENT, listener);
    const detail = {
      source: "window-move" as const,
      x: 0.4,
      y: -0.2,
      energy: 0.5,
      settleMs: 265,
      layout: true,
    };

    emitLiquidGlassMotion(detail);

    expect(listener).toHaveBeenCalledOnce();
    expect((listener.mock.calls[0]?.[0] as CustomEvent).detail).toEqual(detail);
    window.removeEventListener(LIQUID_GLASS_MOTION_EVENT, listener);
  });
});
