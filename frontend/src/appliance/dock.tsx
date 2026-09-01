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
  useLayoutEffect,
  useRef,
  type MouseEventHandler,
  type ReactNode,
} from "react";

// 应用图标属于稳定的系统识别层，不随鼠标或玻璃模式改变尺寸和基线。
const BASE_SIZE = 62;

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
export function Dock({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const navRef = useRef<HTMLElement>(null);

  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const syncGlassWidth = () => {
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

  return (
    <nav ref={navRef} data-desktop-interactive className={className}>
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
  return (
    <button
      type="button"
      data-liquid-icon
      onClick={onClick}
      onContextMenu={onContextMenu}
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
