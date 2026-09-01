export const LIQUID_GLASS_MOTION_EVENT = "echo:liquid-glass-motion";

export type LiquidGlassMotionSource =
  | "pointer"
  | "dock"
  | "window-move"
  | "window-resize";

export type LiquidGlassMotionSample = Readonly<{
  x: number;
  y: number;
  energy: number;
  settleMs: number;
}>;

export type LiquidGlassMotionDetail = LiquidGlassMotionSample &
  Readonly<{
    source: LiquidGlassMotionSource;
    layout: boolean;
  }>;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Convert a pointer delta into a bounded, device-independent motion vector.
 * The result drives highlights and a very small window tilt; it never changes
 * the actual pointer geometry. A faster gesture gets a longer settle time,
 * while a stationary pointer produces no animation window at all.
 */
export function calculateLiquidGlassMotion(
  deltaX: number,
  deltaY: number,
  elapsedMs: number,
): LiquidGlassMotionSample {
  if (
    !Number.isFinite(deltaX) ||
    !Number.isFinite(deltaY) ||
    !Number.isFinite(elapsedMs) ||
    elapsedMs <= 0
  ) {
    return { x: 0, y: 0, energy: 0, settleMs: 0 };
  }

  // Pointer events can be delivered faster than the display refresh rate.
  // Bounding dt avoids a noisy one-pixel event becoming a full-strength kick.
  const dt = clamp(elapsedMs, 8, 48);
  const velocityX = deltaX / dt;
  const velocityY = deltaY / dt;
  const speed = Math.hypot(velocityX, velocityY);
  const energy = clamp(speed / 1.35, 0, 1);

  if (energy < 0.025) {
    return { x: 0, y: 0, energy: 0, settleMs: 0 };
  }

  return {
    x: clamp(velocityX / 1.35, -1, 1),
    y: clamp(velocityY / 1.35, -1, 1),
    energy,
    settleMs: Math.round(150 + energy * 230),
  };
}

/**
 * A tiny invalidation bus shared by windows, Dock and the WebGL optical pass.
 * It lets moving geometry request a short render burst without observing every
 * inline-style mutation or running a permanent animation loop.
 */
export function emitLiquidGlassMotion(detail: LiquidGlassMotionDetail): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<LiquidGlassMotionDetail>(LIQUID_GLASS_MOTION_EVENT, {
      detail,
    }),
  );
}
