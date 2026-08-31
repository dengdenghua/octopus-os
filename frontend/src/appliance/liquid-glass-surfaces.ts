export const MAX_LIQUID_GLASS_SURFACES = 8;

export type NativeLiquidGlassWallpaper =
  | "orbit"
  | "aurora"
  | "sunset"
  | "midnight";

export type NativeLiquidGlassMaterial =
  | "ultra-thin"
  | "thin"
  | "thick"
  | "thick-dark"
  | "ultra-thick";

export type NativeLiquidGlassSurface = Readonly<{
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  cornerRadius: number;
  material: NativeLiquidGlassMaterial;
}>;

export const LIQUID_GLASS_SURFACE_SELECTOR = [
  ".mac-calendar-widget",
  ".mac-agent-widget",
  ".mac-menu-dropdown",
  ".mac-desktop-context-menu",
  ".mac-spotlight",
  ".mac-control-center",
  ".mac-notification-center",
  ".mac-liquid-glass-panel",
].join(",");

export function calculateDockGlassBounds(
  bounds: DOMRect,
  glassWidth: number,
  glassHeight: number,
): DOMRect {
  return new DOMRect(
    bounds.left + (bounds.width - glassWidth) / 2,
    bounds.bottom - glassHeight + 1,
    glassWidth,
    glassHeight,
  );
}

/** Keep every compositor aligned with the Dock's actual painted tray. */
export function liquidGlassSurfaceBounds(element: HTMLElement): DOMRect {
  const bounds = element.getBoundingClientRect();
  if (!element.classList.contains("mac-dock")) return bounds;

  const style = getComputedStyle(element);
  const glassWidth =
    Number.parseFloat(style.getPropertyValue("--dock-glass-width")) ||
    bounds.width;
  const glassHeight =
    Number.parseFloat(style.getPropertyValue("--dock-glass-height")) ||
    bounds.height;
  return calculateDockGlassBounds(bounds, glassWidth, glassHeight);
}

export function visibleLiquidGlassSurfaces(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(LIQUID_GLASS_SURFACE_SELECTOR),
  )
    .filter((element) => {
      const bounds = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return (
        bounds.width > 1 &&
        bounds.height > 1 &&
        style.display !== "none" &&
        style.visibility !== "hidden"
      );
    })
    .slice(0, MAX_LIQUID_GLASS_SURFACES);
}
