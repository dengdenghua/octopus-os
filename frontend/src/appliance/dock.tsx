/**
 * Echo OS 原创桌面栏邻近缩放。
 *
 * 原创实现,不依赖任何专有资源或动画库(仓库把 motion/react 别名成了
 * 精简 shim,缺 useTransform 等;故用纯 React + CSS 变量实现)。
 *
 * 工作方式:Dock 在 pointermove(rAF 节流)时量取每个 .dock-item 的中心,
 * 按光标距离算出缩放比写入 CSS 变量 --dock-s;宽高与内容缩放、过渡缓动
 * 全部交给 .dock-item 的 CSS(见 globals.css),每帧每图标仅一次 DOM 写入。
 */

import {
  useEffect,
  useLayoutEffect,
  useRef,
  type MouseEventHandler,
  type PointerEventHandler,
  type ReactNode,
} from "react";

import { emitLiquidGlassMotion } from "@/appliance/liquid-glass-motion";

// 静息边长(px)、放大峰值、上浮距离与高斯衰减宽度(px)。数值参考
// 邻近缩放数值按 Echo OS 的玻璃底座和图标密度独立调校。
const BASE_SIZE = 54;
const MAX_SCALE = 1.72;
const LIFT = 19;
const SIGMA = 76;

function isDockLayoutChild(element: Element): element is HTMLElement {
  return (
    element instanceof HTMLElement &&
    !element.classList.contains("mac-dock-lens")
  );
}

/**
 * 邻近缩放会改变按钮的布局宽度,但 Dock 玻璃应保持静息尺寸。这里从每个
 * item 的未缩放 inner 宽度计算底座宽度,不受当前 --dock-s 影响。
 */
function measureRestingGlassWidth(nav: HTMLElement): number {
  const navStyle = getComputedStyle(nav);
  const children = Array.from(nav.children).filter(isDockLayoutChild);
  const gap = Number.parseFloat(navStyle.columnGap || navStyle.gap) || 0;
  const chrome =
    (Number.parseFloat(navStyle.paddingLeft) || 0) +
    (Number.parseFloat(navStyle.paddingRight) || 0) +
    (Number.parseFloat(navStyle.borderLeftWidth) || 0) +
    (Number.parseFloat(navStyle.borderRightWidth) || 0);
  const content = children.reduce((total, child) => {
    const childStyle = getComputedStyle(child);
    const inner = child.matches(".dock-item")
      ? child.querySelector<HTMLElement>(".dock-item-inner")
      : null;
    const width = inner
      ? Number.parseFloat(getComputedStyle(inner).width) || BASE_SIZE
      : Number.parseFloat(childStyle.width) ||
        child.getBoundingClientRect().width;
    return (
      total +
      width +
      (Number.parseFloat(childStyle.marginLeft) || 0) +
      (Number.parseFloat(childStyle.marginRight) || 0)
    );
  }, 0);
  return Math.ceil(chrome + content + Math.max(0, children.length - 1) * gap);
}

/**
 * Return the centers from the unscaled flex layout. During hover the button
 * widths grow to make room for the enlarged icons; measuring their live rects
 * would feed that growth back into the next distance calculation and make the
 * magnification feel like a single jumping icon instead of one smooth wave.
 */
function measureRestingCenters(nav: HTMLElement): number[] {
  const navStyle = getComputedStyle(nav);
  const navRect = nav.getBoundingClientRect();
  const gap = Number.parseFloat(navStyle.columnGap || navStyle.gap) || 0;
  const paddingLeft = Number.parseFloat(navStyle.paddingLeft) || 0;
  const borderLeft = Number.parseFloat(navStyle.borderLeftWidth) || 0;
  let cursor = navRect.left + borderLeft + paddingLeft;
  const centers: number[] = [];

  Array.from(nav.children)
    .filter(isDockLayoutChild)
    .forEach((element) => {
      const style = getComputedStyle(element);
      const marginLeft = Number.parseFloat(style.marginLeft) || 0;
      const marginRight = Number.parseFloat(style.marginRight) || 0;
      const inner = element.matches(".dock-item")
        ? element.querySelector<HTMLElement>(".dock-item-inner")
        : null;
      const width = inner
        ? Number.parseFloat(getComputedStyle(inner).width) || BASE_SIZE
        : Number.parseFloat(style.width) ||
          element.getBoundingClientRect().width;

      cursor += marginLeft;
      if (element.matches(".dock-item")) {
        centers.push(cursor + width / 2);
      }
      cursor += width + marginRight + gap;
    });

  return centers;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

export function Dock({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const navRef = useRef<HTMLElement>(null);
  const frame = useRef(0);
  const restingCenters = useRef<number[]>([]);

  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const syncGlassWidth = () => {
      restingCenters.current = measureRestingCenters(nav);
      const glassWidth = measureRestingGlassWidth(nav);
      nav.style.setProperty("--dock-glass-width", `${glassWidth}px`);
      const lens = nav.querySelector<HTMLElement>(".mac-dock-lens");
      if (lens) {
        const rect = lens.getBoundingClientRect();
        lens.style.setProperty("--mac-dock-lens-left", `${rect.left}px`);
        lens.style.setProperty("--mac-dock-lens-top", `${rect.top}px`);
      }
    };
    syncGlassWidth();
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(syncGlassWidth);
    observer?.observe(nav);
    window.addEventListener("resize", syncGlassWidth);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", syncGlassWidth);
    };
  }, [children]);

  const apply = (clientX: number | null) => {
    const nav = navRef.current;
    if (!nav) return;
    const items = nav.querySelectorAll<HTMLElement>(".dock-item");
    items.forEach((el, index) => {
      const inner = el.querySelector<HTMLElement>(".dock-item-inner");
      if (!inner) return;

      const engaged = clientX !== null;
      el.style.transition = engaged
        ? "none"
        : "width 340ms cubic-bezier(0.16, 1, 0.3, 1), height 340ms cubic-bezier(0.16, 1, 0.3, 1)";
      inner.style.transition = engaged
        ? "none"
        : "transform 340ms cubic-bezier(0.16, 1, 0.3, 1)";

      if (clientX === null) {
        el.style.setProperty("--dock-s", "1");
        inner.style.setProperty("--dock-lift", "0px");
        return;
      }
      const rect = el.getBoundingClientRect();
      const center =
        restingCenters.current[index] ?? rect.left + rect.width / 2;
      const distance = clientX - center;
      const proximity = Math.exp(-(distance * distance) / (2 * SIGMA * SIGMA));
      const scale = 1 + proximity * (MAX_SCALE - 1);
      el.style.setProperty("--dock-s", scale.toFixed(3));
      inner.style.setProperty(
        "--dock-lift",
        `${(-LIFT * proximity).toFixed(2)}px`,
      );
    });
  };

  const handleMove: PointerEventHandler<HTMLElement> = (event) => {
    if (prefersReducedMotion()) return;
    const x = event.clientX;
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => apply(x));
  };

  const handleLeave = () => {
    cancelAnimationFrame(frame.current);
    apply(null);
  };

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  return (
    <nav
      ref={navRef}
      data-desktop-interactive
      data-liquid-surface="thin"
      onPointerMove={handleMove}
      onPointerLeave={handleLeave}
      className={className}
    >
      <span className="mac-dock-lens" aria-hidden>
        <img src="/third-party/appletechie-macos/wallpaper-day2.jpg" alt="" />
      </span>
      {children}
    </nav>
  );
}

export function DockItem({
  children,
  onClick,
  onContextMenu,
  title,
  className,
  running = false,
}: {
  children: ReactNode;
  onClick?: () => void;
  onContextMenu?: MouseEventHandler<HTMLButtonElement>;
  title?: string;
  className?: string;
  running?: boolean;
}) {
  const press: PointerEventHandler<HTMLButtonElement> = (event) => {
    event.currentTarget.dataset.dockPressed = "true";
    emitLiquidGlassMotion({
      source: "dock",
      x: 0,
      y: 0.42,
      energy: 0.42,
      settleMs: 250,
      layout: false,
    });
  };
  const release: PointerEventHandler<HTMLButtonElement> = (event) => {
    delete event.currentTarget.dataset.dockPressed;
    emitLiquidGlassMotion({
      source: "dock",
      x: 0,
      y: -0.26,
      energy: 0.3,
      settleMs: 330,
      layout: false,
    });
  };

  return (
    <button
      type="button"
      data-liquid-icon
      onClick={onClick}
      onContextMenu={onContextMenu}
      onPointerDown={press}
      onPointerUp={release}
      onPointerCancel={release}
      onPointerLeave={release}
      title={title}
      aria-label={title}
      className={`dock-item ${className ?? ""}`}
    >
      <span className="dock-item-inner relative grid size-full place-items-center">
        <span className="dock-item-spring">{children}</span>
      </span>
      {running && <span className="mac-dock-running-dot" aria-hidden />}
    </button>
  );
}
