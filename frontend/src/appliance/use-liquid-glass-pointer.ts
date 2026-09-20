/**
 * 液态玻璃指针交互 hook。
 *
 * 把 DesktopShellPage 的 ~150 行内联 onPointerMove 抽出来：
 * 量取指针位置与运动能量，rAF 节流每帧写入 --liquid-* CSS 变量，
 * 并跟踪当前 data-liquid-surface 元素写入局部坐标。root 引用由调用方
 * 提供（桌面根节点）。
 *
 * 视觉样式全部走 CSS 变量 + data-* 属性（见 macos-desktop.css），
 * hook 本身不渲染任何元素。
 */

import { useEffect, useRef, type PointerEventHandler } from "react";

import { calculateLiquidGlassMotion } from "./liquid-glass-motion";

export type LiquidGlassPointerHandlers = {
  onPointerMove: PointerEventHandler<HTMLElement>;
};

export function useLiquidGlassPointer(): LiquidGlassPointerHandlers {
  const liquidPointerFrameRef = useRef<number | null>(null);
  const liquidMotionResetTimerRef = useRef<number | null>(null);
  const liquidPointerRef = useRef({ x: 50, y: 30, clientX: 0, clientY: 0 });
  const liquidMotionSampleRef = useRef({ x: 0, y: 0, time: 0 });
  const liquidSurfaceRef = useRef<HTMLElement | null>(null);
  const activeLiquidSurfaceRef = useRef<HTMLElement | null>(null);

  useEffect(
    () => () => {
      if (liquidPointerFrameRef.current !== null) {
        window.cancelAnimationFrame(liquidPointerFrameRef.current);
      }
      if (liquidMotionResetTimerRef.current !== null) {
        window.clearTimeout(liquidMotionResetTimerRef.current);
      }
    },
    [],
  );

  const onPointerMove: PointerEventHandler<HTMLElement> = (event) => {
    const now = event.timeStamp || performance.now();
    const previousMotionSample = liquidMotionSampleRef.current;
    const motion = calculateLiquidGlassMotion(
      event.clientX - previousMotionSample.x,
      event.clientY - previousMotionSample.y,
      previousMotionSample.time > 0 ? now - previousMotionSample.time : 0,
    );
    liquidMotionSampleRef.current = {
      x: event.clientX,
      y: event.clientY,
      time: now,
    };
    liquidPointerRef.current = {
      x: (event.clientX / window.innerWidth) * 100,
      y: (event.clientY / window.innerHeight) * 100,
      clientX: event.clientX,
      clientY: event.clientY,
    };
    liquidSurfaceRef.current =
      event.target instanceof Element
        ? event.target.closest<HTMLElement>("[data-liquid-surface]")
        : null;
    if (liquidPointerFrameRef.current !== null) return;
    const root = event.currentTarget;
    liquidPointerFrameRef.current = window.requestAnimationFrame(() => {
      const surface = liquidSurfaceRef.current;
      const hasInteractiveGlass = !!(surface || activeLiquidSurfaceRef.current);
      if (hasInteractiveGlass) {
        root.style.setProperty(
          "--liquid-pointer-x",
          `${liquidPointerRef.current.x.toFixed(2)}%`,
        );
        root.style.setProperty(
          "--liquid-pointer-y",
          `${liquidPointerRef.current.y.toFixed(2)}%`,
        );
        root.style.setProperty(
          "--liquid-shift-x",
          `${((liquidPointerRef.current.x - 50) * 0.14).toFixed(2)}px`,
        );
        root.style.setProperty(
          "--liquid-shift-y",
          `${((liquidPointerRef.current.y - 50) * 0.1).toFixed(2)}px`,
        );
        root.style.setProperty(
          "--liquid-motion-x",
          `${(motion.x * 6).toFixed(2)}px`,
        );
        root.style.setProperty(
          "--liquid-motion-y",
          `${(motion.y * 5).toFixed(2)}px`,
        );
        root.style.setProperty(
          "--liquid-motion-energy",
          motion.energy.toFixed(3),
        );
        root.dataset.liquidMotion = motion.energy > 0 ? "active" : "idle";
      }

      if (activeLiquidSurfaceRef.current !== surface) {
        const previousSurface = activeLiquidSurfaceRef.current;
        previousSurface?.removeAttribute("data-liquid-active");
        previousSurface?.style.setProperty("--liquid-motion-x", "0px");
        previousSurface?.style.setProperty("--liquid-motion-y", "0px");
        previousSurface?.style.setProperty("--liquid-motion-energy", "0");
        surface?.setAttribute("data-liquid-active", "true");
        activeLiquidSurfaceRef.current = surface;
      }
      if (surface) {
        const bounds = surface.getBoundingClientRect();
        const localX = Math.max(
          0,
          Math.min(
            100,
            ((liquidPointerRef.current.clientX - bounds.left) /
              Math.max(1, bounds.width)) *
              100,
          ),
        );
        const localY = Math.max(
          0,
          Math.min(
            100,
            ((liquidPointerRef.current.clientY - bounds.top) /
              Math.max(1, bounds.height)) *
              100,
          ),
        );
        surface.style.setProperty(
          "--liquid-local-x",
          `${localX.toFixed(2)}%`,
        );
        surface.style.setProperty(
          "--liquid-local-y",
          `${localY.toFixed(2)}%`,
        );
        surface.style.setProperty(
          "--liquid-local-shift-x",
          `${((localX - 50) * 0.08).toFixed(2)}px`,
        );
        surface.style.setProperty(
          "--liquid-local-shift-y",
          `${((localY - 50) * 0.06).toFixed(2)}px`,
        );
        surface.style.setProperty(
          "--liquid-motion-x",
          `${(motion.x * 6).toFixed(2)}px`,
        );
        surface.style.setProperty(
          "--liquid-motion-y",
          `${(motion.y * 5).toFixed(2)}px`,
        );
        surface.style.setProperty(
          "--liquid-motion-energy",
          motion.energy.toFixed(3),
        );
      }

      if (liquidMotionResetTimerRef.current !== null) {
        window.clearTimeout(liquidMotionResetTimerRef.current);
      }
      if (surface) {
        const movingSurface = surface;
        liquidMotionResetTimerRef.current = window.setTimeout(() => {
          root.style.setProperty("--liquid-motion-x", "0px");
          root.style.setProperty("--liquid-motion-y", "0px");
          root.style.setProperty("--liquid-motion-energy", "0");
          root.dataset.liquidMotion = "idle";
          movingSurface?.style.setProperty("--liquid-motion-x", "0px");
          movingSurface?.style.setProperty("--liquid-motion-y", "0px");
          movingSurface?.style.setProperty("--liquid-motion-energy", "0");
          liquidMotionResetTimerRef.current = null;
        }, 72);
      } else {
        root.style.setProperty("--liquid-motion-x", "0px");
        root.style.setProperty("--liquid-motion-y", "0px");
        root.style.setProperty("--liquid-motion-energy", "0");
        root.dataset.liquidMotion = "idle";
      }
      liquidPointerFrameRef.current = null;
    });
  };

  return { onPointerMove };
}
