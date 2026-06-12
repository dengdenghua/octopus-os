/**
 * Octopus OS:macOS / Magic视界 风格的 Dock 邻近缩放。
 *
 * 原创实现,不依赖任何专有资源或动画库(仓库把 motion/react 别名成了
 * 精简 shim,缺 useTransform 等;故用纯 React + CSS 变量实现)。
 *
 * 工作方式:Dock 在 mousemove(rAF 节流)时量取每个 .dock-item 的中心,
 * 按光标距离算出缩放比写入 CSS 变量 --dock-s;宽高与内容缩放、过渡缓动
 * 全部交给 .dock-item 的 CSS(见 globals.css),每帧每图标仅一次 DOM 写入。
 */

import { useEffect, useRef, type ReactNode } from "react";

// 静息边长(px)、放大峰值、光标影响半径(px)。
const BASE_SIZE = 54;
const MAX_SCALE = 78 / BASE_SIZE;
const INFLUENCE = 132;

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

  const apply = (clientX: number | null) => {
    const nav = navRef.current;
    if (!nav) return;
    const items = nav.querySelectorAll<HTMLElement>(".dock-item");
    items.forEach((el) => {
      if (clientX === null) {
        el.style.setProperty("--dock-s", "1");
        return;
      }
      const rect = el.getBoundingClientRect();
      const center = rect.left + rect.width / 2;
      const proximity = Math.max(0, 1 - Math.abs(clientX - center) / INFLUENCE);
      const scale = 1 + proximity * (MAX_SCALE - 1);
      el.style.setProperty("--dock-s", scale.toFixed(3));
    });
  };

  const handleMove = (event: React.MouseEvent) => {
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
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      className={className}
    >
      {children}
    </nav>
  );
}

export function DockItem({
  children,
  onClick,
  title,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  title?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`dock-item ${className ?? ""}`}
    >
      <span className="dock-item-inner relative grid size-full place-items-center">
        {children}
      </span>
    </button>
  );
}
