import { useEffect } from "react";

import {
  liquidGlassSurfaceBounds,
  visibleLiquidGlassSurfaces,
  type NativeLiquidGlassMaterial,
  type NativeLiquidGlassSurface,
  type NativeLiquidGlassWallpaper,
} from "@/appliance/liquid-glass-surfaces";

type NativeLiquidGlassProps = Readonly<{
  enabled: boolean;
  wallpaper: NativeLiquidGlassWallpaper;
}>;

const nativeIdentifiers = new WeakMap<HTMLElement, string>();
let nextNativeIdentifier = 0;

function nativeIdentifier(element: HTMLElement): string {
  const existing = nativeIdentifiers.get(element);
  if (existing) return existing;
  const identifier = `surface:${++nextNativeIdentifier}`;
  nativeIdentifiers.set(element, identifier);
  return identifier;
}

function surfaceMaterial(element: HTMLElement): NativeLiquidGlassMaterial {
  const material = element.dataset.liquidSurface;
  if (
    material === "ultra-thin" ||
    material === "thin" ||
    material === "thick" ||
    material === "thick-dark" ||
    material === "ultra-thick"
  ) {
    return material;
  }
  return "thick";
}

export function measureNativeLiquidGlassSurface(
  element: HTMLElement,
  rootBounds: DOMRect,
): NativeLiquidGlassSurface {
  const dock = element.classList.contains("mac-dock");
  const bounds = liquidGlassSurfaceBounds(element);
  const style = getComputedStyle(element);
  const parsedRadius = Number.parseFloat(style.borderTopLeftRadius);
  const cornerRadius = Math.min(
    dock ? 22 : Number.isFinite(parsedRadius) ? parsedRadius : 18,
    bounds.width / 2,
    bounds.height / 2,
  );
  return {
    id: nativeIdentifier(element),
    x: bounds.left - rootBounds.left,
    y: bounds.top - rootBounds.top,
    width: bounds.width,
    height: bounds.height,
    cornerRadius,
    material: surfaceMaterial(element),
  };
}

function clearNativeSurfaceMarkers(root: HTMLElement) {
  delete root.dataset.liquidNative;
  delete root.dataset.liquidNativeMaterial;
  delete root.dataset.liquidNativeBackend;
  delete root.dataset.liquidNativeSurfaceCount;
  document.documentElement.classList.remove("native-liquid-glass");
  document.body.classList.remove("native-liquid-glass");
  root
    .querySelectorAll<HTMLElement>("[data-liquid-native-surface]")
    .forEach((element) => delete element.dataset.liquidNativeSurface);
}

/**
 * Keeps the platform compositor scene aligned with ordinary DOM surfaces.
 * AppKit and the KWin Wayland Effect own the complete native material. The X11
 * blur-region fallback keeps the WebGL optics layer because it cannot install
 * a custom compositor shader. Text, icons and interaction stay in Chromium.
 */
export function MacNativeLiquidGlass({
  enabled,
  wallpaper,
}: NativeLiquidGlassProps) {
  useEffect(() => {
    const root = document.querySelector<HTMLElement>(".macos-desktop-root");
    const bridge = window.echo?.nativeGlass;
    if (!root || !bridge || !enabled) {
      if (root) clearNativeSurfaceMarkers(root);
      return;
    }

    let disposed = false;
    let frame = 0;
    let syncing = false;
    let pending = false;
    let markedElements: HTMLElement[] = [];
    const layoutResizeObserver =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => queueSync());
    layoutResizeObserver?.observe(root);

    const commitMarkers = (
      elements: HTMLElement[],
      result: Awaited<ReturnType<typeof bridge.sync>>,
    ) => {
      markedElements.forEach(
        (element) => delete element.dataset.liquidNativeSurface,
      );
      markedElements = [];
      if (!result.active) {
        clearNativeSurfaceMarkers(root);
        return;
      }
      root.dataset.liquidNative = "ready";
      root.dataset.liquidNativeMaterial = result.material || "native";
      root.dataset.liquidNativeBackend = result.backend || "native";
      root.dataset.liquidNativeSurfaceCount = String(result.surfaceCount);
      document.documentElement.classList.add("native-liquid-glass");
      document.body.classList.add("native-liquid-glass");
      elements.forEach((element) => {
        element.dataset.liquidNativeSurface = "ready";
      });
      markedElements = elements;
    };

    const sync = async () => {
      if (disposed) return;
      if (syncing) {
        pending = true;
        return;
      }
      syncing = true;
      const elements = visibleLiquidGlassSurfaces(root);
      const rootBounds = root.getBoundingClientRect();
      try {
        const result = await bridge.sync({
          wallpaper,
          surfaces: elements.map((element) =>
            measureNativeLiquidGlassSurface(element, rootBounds),
          ),
        });
        if (!disposed) commitMarkers(elements, result);
      } catch {
        if (!disposed) clearNativeSurfaceMarkers(root);
      } finally {
        syncing = false;
        if (pending && !disposed) {
          pending = false;
          queueSync();
        }
      }
    };

    function queueSync() {
      if (disposed || frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        void sync();
      });
    }

    const mutationObserver = new MutationObserver(queueSync);
    mutationObserver.observe(root, { childList: true, subtree: true });
    window.addEventListener("resize", queueSync);
    void bridge.getCapabilities().then((capabilities) => {
      if (disposed || !capabilities.supported) return;
      queueSync();
    });

    return () => {
      disposed = true;
      if (frame) window.cancelAnimationFrame(frame);
      mutationObserver.disconnect();
      layoutResizeObserver?.disconnect();
      window.removeEventListener("resize", queueSync);
      markedElements.forEach(
        (element) => delete element.dataset.liquidNativeSurface,
      );
      clearNativeSurfaceMarkers(root);
      void bridge.deactivate();
    };
  }, [enabled, wallpaper]);

  return null;
}
