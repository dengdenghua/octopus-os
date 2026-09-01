import { useCallback, useEffect, useRef, useState } from "react";

import { swallow } from "@/core/utils/log";

const KEYBOARD_RESIZE_STEP_PX = 16;

function readStoredWidth(key: string): number | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const px = Number.parseInt(raw, 10);
    if (Number.isFinite(px)) {
      return px;
    }
  } catch (e) {
    swallow(e, "storage");
  }
  return null;
}

function writeStoredWidth(key: string, px: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, String(px));
  } catch (e) {
    swallow(e, "storage");
  }
}

function readStoredWidthInRange(
  key: string,
  minPx: number,
  maxPx: number,
): number | null {
  const px = readStoredWidth(key);
  if (px && px >= minPx && px <= maxPx) return px;
  return null;
}

/** Numeric estimate for the width strings resolveSidebarWidth produces
 *  ("min(300px, 36vw)", "420px", "26rem"). Used for aria-valuenow, keyboard
 *  resizing and viewport clamping before any dragged width is stored. */
function estimateCssWidthPx(css: string, viewportWidth: number): number | null {
  const terms =
    css.startsWith("min(") && css.endsWith(")")
      ? css.slice(4, -1).split(",")
      : [css];
  const values: number[] = [];
  for (const term of terms) {
    const m = term.trim().match(/^(\d+(?:\.\d+)?)(px|rem|vw)$/);
    if (!m) return null;
    const n = Number(m[1]);
    values.push(
      m[2] === "px" ? n : m[2] === "rem" ? n * 16 : (n / 100) * viewportWidth,
    );
  }
  return Math.min(...values);
}

export interface UseResizablePanelOptions {
  /** localStorage key used to persist the dragged width. */
  storageKey: string;
  minPx: number;
  maxPx: number;
  /** The CSS width string (e.g. "min(300px, 36vw)") used as a fallback
   *  before any width has been dragged. */
  defaultCssWidth: string;
  /** Current viewport width; drives responsive clamps. */
  viewportWidth: number;
  /** Clamp a candidate width against the absolute range AND the viewport,
   *  reserving room for the other open panel. Re-created each render with
   *  the latest viewport / sibling-panel state. */
  clamp: (px: number) => number;
  /** Fallback px used when no stored width and the CSS default is unparseable. */
  fallbackPx: number;
}

export interface ResizablePanelController {
  /** Numeric basis before clamping (stored width or parsed CSS default). */
  basisPx: number | null;
  /** Clamped width used for rendering / aria-valuenow. */
  resolvedPx: number;
  handleMouseDown: (e: React.MouseEvent) => void;
  handleKeyDown: (e: React.KeyboardEvent) => void;
}

/** Shared drag / keyboard resize handling for horizontally-resizable panels
 *  (the chat sidebar and the secondary workbench). Lazy-loads the persisted
 *  width from localStorage, throttles drag state updates to animation frames,
 *  flushes + persists on drag-end, and restores the body cursor/user-select.
 *  Viewport clamping is delegated to the caller via ``clamp`` so sibling-panel
 *  dependencies stay in the component. */
export function useResizablePanel({
  storageKey,
  minPx,
  maxPx,
  defaultCssWidth,
  viewportWidth,
  clamp,
  fallbackPx,
}: UseResizablePanelOptions): ResizablePanelController {
  // Lazy init from localStorage so a previously-dragged width persists
  // across reloads / remounts (SSR-safe — returns null on the server).
  const [customWidth, setCustomWidth] = useState<number | null>(() =>
    readStoredWidthInRange(storageKey, minPx, maxPx),
  );
  const basisPx =
    customWidth ?? estimateCssWidthPx(defaultCssWidth, viewportWidth);
  const resolvedPx = clamp(basisPx ?? fallbackPx);

  // The document-level drag listeners are registered once ([] deps); route
  // them through a ref so they clamp against the current viewport state.
  const clampRef = useRef(clamp);
  clampRef.current = clamp;

  // Resize drag handling. ``latest`` mirrors the most recent width in a ref
  // (the document-level mouseup listener captures a stale closure, so it
  // persists from the ref instead).
  const resizeRef = useRef<{
    startX: number;
    startWidth: number;
    latest: number;
    raf: number | null;
  } | null>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const aside = (e.target as HTMLElement).parentElement;
    if (!aside) return;
    const rect = aside.getBoundingClientRect();
    resizeRef.current = {
      startX: e.clientX,
      startWidth: rect.width,
      latest: rect.width,
      raf: null,
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!resizeRef.current) return;
      // Right-docked panel with a left-edge handle: dragging left widens.
      const delta = resizeRef.current.startX - e.clientX;
      const newWidth = clampRef.current(resizeRef.current.startWidth + delta);
      resizeRef.current.latest = newWidth;
      // Throttle React state updates to animation frames to avoid
      // triggering reconciliation on every mousemove event.
      if (!resizeRef.current.raf) {
        resizeRef.current.raf = requestAnimationFrame(() => {
          resizeRef.current!.raf = null;
          setCustomWidth(resizeRef.current!.latest);
        });
      }
    };

    const handleMouseUp = () => {
      if (resizeRef.current) {
        // Flush any pending RAF update before persisting.
        if (resizeRef.current.raf) {
          cancelAnimationFrame(resizeRef.current.raf);
          resizeRef.current.raf = null;
          setCustomWidth(resizeRef.current.latest);
        }
        // Persist only at drag-end (not per mousemove) to avoid thrashing
        // localStorage.
        writeStoredWidth(storageKey, resizeRef.current.latest);
        resizeRef.current = null;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [storageKey]);

  // Left-edge handle on a right-docked panel: ArrowLeft moves the edge
  // left (wider), ArrowRight moves it right (narrower).
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const delta =
        e.key === "ArrowLeft"
          ? KEYBOARD_RESIZE_STEP_PX
          : e.key === "ArrowRight"
            ? -KEYBOARD_RESIZE_STEP_PX
            : 0;
      if (!delta) return;
      e.preventDefault();
      const next = clamp(resolvedPx + delta);
      setCustomWidth(next);
      writeStoredWidth(storageKey, next);
    },
    [clamp, resolvedPx, storageKey],
  );

  return { basisPx, resolvedPx, handleMouseDown, handleKeyDown };
}
