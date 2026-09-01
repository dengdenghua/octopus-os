/**
 * Echo OS 桌面窗口(原生路线:桌面即窗口系统)。
 *
 * 第三方应用 Web UI 以 iframe 开成桌面内的可拖拽/缩放窗口。许多自托管应用
 * 默认允许内嵌即可直接显示;设了 X-Frame-Options 的需要反向代理剥头
 * (P2 后续,需真实应用验证),在此一律提供"新标签打开"兜底,不做不可靠的
 * 跨域内嵌探测。
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from "react";
import { ExternalLinkIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  calculateLiquidGlassMotion,
  emitLiquidGlassMotion,
  type LiquidGlassMotionSample,
} from "@/appliance/liquid-glass-motion";

export type DesktopWindow = {
  id: string;
  title: string;
  url: string;
  /** 系统内建应用直接渲染 React 内容，不经过 iframe 或另一套前端。 */
  content?: ReactNode;
  /** Agent 等内建应用把导航并入窗口内容，由 Echo OS 叠加唯一一套系统按钮。 */
  integratedChrome?: boolean;
};

const MIN_W = 360;
const MIN_H = 240;

type Rect = { x: number; y: number; w: number; h: number };

function initialRect(index: number): Rect {
  // 层叠摆放,避免完全重叠。
  const offset = (index % 6) * 28;
  const w = Math.min(960, Math.max(MIN_W, window.innerWidth - 220));
  const h = Math.min(620, Math.max(MIN_H, window.innerHeight - 220));
  return {
    x: Math.max(20, (window.innerWidth - w) / 2 - 60 + offset),
    y: Math.max(16, (window.innerHeight - h) / 2 - 40 + offset),
    w,
    h,
  };
}

export function AppWindow({
  win,
  index,
  focused,
  onFocus,
  onClose,
  onMinimize,
}: {
  win: DesktopWindow;
  index: number;
  focused: boolean;
  onFocus: () => void;
  onClose: () => void;
  onMinimize: () => void;
}) {
  const [rect, setRect] = useState<Rect>(() => initialRect(index));
  const [maximized, setMaximized] = useState(false);
  const restoreRect = useRef<Rect | null>(null);
  const windowRef = useRef<HTMLDivElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const rectFrame = useRef<number | null>(null);
  const pendingRect = useRef<Rect | null>(null);
  const motionPoint = useRef({ x: 0, y: 0, time: 0 });
  const lastMotion = useRef<LiquidGlassMotionSample>({
    x: 0,
    y: 0,
    energy: 0,
    settleMs: 0,
  });
  const drag = useRef<{
    mode: "move" | "resize";
    startX: number;
    startY: number;
    orig: Rect;
  } | null>(null);

  const queueRect = useCallback((next: Rect) => {
    pendingRect.current = next;
    if (rectFrame.current !== null) return;
    rectFrame.current = window.requestAnimationFrame(() => {
      if (pendingRect.current) setRect(pendingRect.current);
      pendingRect.current = null;
      rectFrame.current = null;
    });
  }, []);

  const beginWindowMotion = useCallback(
    (x: number, y: number, mode: "move" | "resize", time: number) => {
      motionPoint.current = { x, y, time };
      const element = windowRef.current;
      if (!element) return;
      element.dataset.windowDragging = mode;
      element.style.setProperty("--window-tilt-x", "0deg");
      element.style.setProperty("--window-tilt-y", "0deg");
    },
    [],
  );

  const updateWindowMotion = useCallback(
    (x: number, y: number, mode: "move" | "resize", time: number) => {
      const previous = motionPoint.current;
      const motion = calculateLiquidGlassMotion(
        x - previous.x,
        y - previous.y,
        time - previous.time,
      );
      motionPoint.current = { x, y, time };
      lastMotion.current = motion;
      const element = windowRef.current;
      if (element) {
        const tiltScale = mode === "move" ? 0.62 : 0;
        element.style.setProperty(
          "--window-tilt-x",
          `${(-motion.y * tiltScale).toFixed(3)}deg`,
        );
        element.style.setProperty(
          "--window-tilt-y",
          `${(motion.x * tiltScale).toFixed(3)}deg`,
        );
        element.style.setProperty(
          "--liquid-motion-x",
          `${(motion.x * 5.5).toFixed(2)}px`,
        );
        element.style.setProperty(
          "--liquid-motion-y",
          `${(motion.y * 4.5).toFixed(2)}px`,
        );
        element.style.setProperty(
          "--liquid-motion-energy",
          motion.energy.toFixed(3),
        );
      }
      if (motion.energy > 0) {
        emitLiquidGlassMotion({
          source: mode === "move" ? "window-move" : "window-resize",
          ...motion,
          layout: true,
        });
      }
    },
    [],
  );

  const settleWindowMotion = useCallback(() => {
    if (rectFrame.current !== null) {
      window.cancelAnimationFrame(rectFrame.current);
      rectFrame.current = null;
    }
    if (pendingRect.current) {
      setRect(pendingRect.current);
      pendingRect.current = null;
    }
    const element = windowRef.current;
    if (element) {
      delete element.dataset.windowDragging;
      element.style.setProperty("--window-tilt-x", "0deg");
      element.style.setProperty("--window-tilt-y", "0deg");
      element.style.setProperty("--liquid-motion-x", "0px");
      element.style.setProperty("--liquid-motion-y", "0px");
      element.style.setProperty("--liquid-motion-energy", "0");
    }
    const motion = lastMotion.current;
    emitLiquidGlassMotion({
      source: drag.current?.mode === "resize" ? "window-resize" : "window-move",
      ...motion,
      settleMs: Math.max(180, motion.settleMs),
      layout: true,
    });
    lastMotion.current = { x: 0, y: 0, energy: 0, settleMs: 0 };
  }, []);

  const onPointerMove = useCallback(
    (event: globalThis.PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      const dx = event.clientX - d.startX;
      const dy = event.clientY - d.startY;
      if (d.mode === "move") {
        queueRect({
          ...d.orig,
          x: Math.max(0, Math.min(window.innerWidth - 80, d.orig.x + dx)),
          y: Math.max(25, Math.min(window.innerHeight - 110, d.orig.y + dy)),
        });
      } else {
        queueRect({
          ...d.orig,
          w: Math.max(MIN_W, d.orig.w + dx),
          h: Math.max(MIN_H, d.orig.h + dy),
        });
      }
      updateWindowMotion(
        event.clientX,
        event.clientY,
        d.mode,
        event.timeStamp || performance.now(),
      );
    },
    [queueRect, updateWindowMotion],
  );

  const endDrag = useCallback(() => {
    settleWindowMotion();
    drag.current = null;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", endDrag);
  }, [onPointerMove, settleWindowMotion]);

  useEffect(
    () => () => {
      if (rectFrame.current !== null) {
        window.cancelAnimationFrame(rectFrame.current);
      }
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", endDrag);
    },
    [endDrag, onPointerMove],
  );

  const startDrag = (mode: "move" | "resize") => (event: PointerEvent) => {
    event.preventDefault();
    onFocus();
    if (maximized) return;
    drag.current = {
      mode,
      startX: event.clientX,
      startY: event.clientY,
      orig: rect,
    };
    beginWindowMotion(
      event.clientX,
      event.clientY,
      mode,
      event.timeStamp || performance.now(),
    );
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
  };

  const toggleMaximize = useCallback(() => {
    onFocus();
    if (maximized) {
      if (restoreRect.current) setRect(restoreRect.current);
      restoreRect.current = null;
      setMaximized(false);
      return;
    }
    restoreRect.current = rect;
    setRect({
      x: 6,
      y: 31,
      w: Math.max(MIN_W, window.innerWidth - 12),
      h: Math.max(MIN_H, window.innerHeight - 114),
    });
    setMaximized(true);
  }, [maximized, onFocus, rect]);

  useEffect(() => {
    if (!win.integratedChrome || win.content) return;
    const onMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as
        | {
            type?: string;
            action?: "close" | "minimize" | "maximize";
            phase?: "start" | "move" | "end";
            screenX?: number;
            screenY?: number;
          }
        | undefined;
      if (!data || typeof data !== "object") return;

      if (data.type === "echo-os:window-control") {
        if (data.action === "close") onClose();
        else if (data.action === "minimize") onMinimize();
        else if (data.action === "maximize") toggleMaximize();
        return;
      }

      if (
        data.type !== "echo-os:window-drag" ||
        typeof data.screenX !== "number" ||
        typeof data.screenY !== "number"
      ) {
        return;
      }
      if (data.phase === "start") {
        onFocus();
        if (maximized) return;
        drag.current = {
          mode: "move",
          startX: data.screenX,
          startY: data.screenY,
          orig: rect,
        };
        beginWindowMotion(
          data.screenX,
          data.screenY,
          "move",
          performance.now(),
        );
      } else if (data.phase === "move") {
        const current = drag.current;
        if (!current || current.mode !== "move") return;
        const dx = data.screenX - current.startX;
        const dy = data.screenY - current.startY;
        queueRect({
          ...current.orig,
          x: Math.max(0, Math.min(window.innerWidth - 80, current.orig.x + dx)),
          y: Math.max(
            25,
            Math.min(window.innerHeight - 110, current.orig.y + dy),
          ),
        });
        updateWindowMotion(
          data.screenX,
          data.screenY,
          "move",
          performance.now(),
        );
      } else if (data.phase === "end") {
        settleWindowMotion();
        drag.current = null;
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [
    beginWindowMotion,
    maximized,
    onClose,
    onFocus,
    onMinimize,
    queueRect,
    rect,
    settleWindowMotion,
    toggleMaximize,
    updateWindowMotion,
    win.content,
    win.integratedChrome,
  ]);

  useEffect(() => {
    if (!maximized) return;
    const resize = () =>
      setRect({
        x: 6,
        y: 31,
        w: Math.max(MIN_W, window.innerWidth - 12),
        h: Math.max(MIN_H, window.innerHeight - 114),
      });
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [maximized]);

  return (
    <div
      ref={windowRef}
      data-desktop-interactive
      data-liquid-surface="ultra-thick"
      onPointerDown={onFocus}
      className={cn(
        "mac-window absolute flex flex-col overflow-hidden",
        !focused && "is-unfocused",
        win.integratedChrome && "is-integrated-chrome",
      )}
      style={{
        left: rect.x,
        top: rect.y,
        width: rect.w,
        height: rect.h,
        zIndex: focused ? 50 : 40,
      }}
    >
      {win.integratedChrome && (
        /* 全尺寸内容窗口没有第二条标题栏，但系统按钮必须始终存在。 */
        <div
          className="mac-window-integrated-controls select-none"
          onPointerDown={startDrag("move")}
          onDoubleClick={toggleMaximize}
          aria-label={`${win.title}窗口控制`}
        >
          <div className="mac-traffic-lights">
            <button
              type="button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={onClose}
              title="关闭"
              aria-label={`关闭${win.title}`}
              className="mac-traffic-light close"
            />
            <button
              type="button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={onMinimize}
              title="最小化"
              aria-label={`最小化${win.title}`}
              className="mac-traffic-light minimize"
            />
            <button
              type="button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={toggleMaximize}
              title={maximized ? "恢复" : "缩放"}
              aria-label={maximized ? `恢复${win.title}` : `缩放${win.title}`}
              className="mac-traffic-light zoom"
            />
          </div>
        </div>
      )}

      {!win.integratedChrome && (
        /* 第三方应用仍由 Echo OS 提供完整窗口框架。 */
        <div
          onPointerDown={startDrag("move")}
          onDoubleClick={toggleMaximize}
          className="mac-window-titlebar select-none"
        >
          <div className="mac-traffic-lights">
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={onClose}
              title="关闭"
              className="mac-traffic-light close"
            />
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={onMinimize}
              title="最小化"
              className="mac-traffic-light minimize"
            />
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={toggleMaximize}
              title={maximized ? "恢复" : "缩放"}
              className="mac-traffic-light zoom"
            />
          </div>
          <span className="mac-window-title">{win.title}</span>
          <div className="mac-window-actions">
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => window.open(win.url, "_blank", "noopener")}
              title="在新标签打开"
              className="mac-window-action"
            >
              <ExternalLinkIcon className="size-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* 内容 */}
      <div className="mac-window-content">
        {win.content ?? (
          <iframe
            ref={iframeRef}
            src={win.url}
            title={win.title}
            className="size-full border-0"
            allow="camera; microphone; clipboard-read; clipboard-write; fullscreen"
            sandbox="allow-same-origin allow-scripts allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-downloads"
          />
        )}
        {/* iframe 失焦时盖一层透明层，避免拖动经过第三方页面被吞掉指针事件。 */}
        {!focused && !win.content && (
          <div className="absolute inset-0" onPointerDown={onFocus} />
        )}
      </div>

      {/* 右下角缩放手柄 */}
      <div onPointerDown={startDrag("resize")} className="mac-window-resize" />
    </div>
  );
}
