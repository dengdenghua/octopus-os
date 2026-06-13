/**
 * Octopus OS 桌面窗口(原生路线:桌面即窗口系统)。
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
} from "react";
import { ExternalLinkIcon } from "lucide-react";

export type DesktopWindow = {
  id: string;
  title: string;
  url: string;
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
  const drag = useRef<{
    mode: "move" | "resize";
    startX: number;
    startY: number;
    orig: Rect;
  } | null>(null);

  const onPointerMove = useCallback((event: globalThis.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = event.clientX - d.startX;
    const dy = event.clientY - d.startY;
    if (d.mode === "move") {
      setRect({
        ...d.orig,
        x: Math.max(0, Math.min(window.innerWidth - 80, d.orig.x + dx)),
        y: Math.max(0, Math.min(window.innerHeight - 60, d.orig.y + dy)),
      });
    } else {
      setRect({
        ...d.orig,
        w: Math.max(MIN_W, d.orig.w + dx),
        h: Math.max(MIN_H, d.orig.h + dy),
      });
    }
  }, []);

  const endDrag = useCallback(() => {
    drag.current = null;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", endDrag);
  }, [onPointerMove]);

  useEffect(() => endDrag, [endDrag]);

  const startDrag = (mode: "move" | "resize") => (event: PointerEvent) => {
    event.preventDefault();
    onFocus();
    drag.current = {
      mode,
      startX: event.clientX,
      startY: event.clientY,
      orig: rect,
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
  };

  return (
    <div
      data-desktop-interactive
      onPointerDown={onFocus}
      className="absolute flex flex-col overflow-hidden rounded-[14px] border border-white/25 bg-white/95 shadow-[0_24px_60px_-12px_rgba(0,0,0,0.5)] ring-1 ring-black/10"
      style={{
        left: rect.x,
        top: rect.y,
        width: rect.w,
        height: rect.h,
        zIndex: focused ? 50 : 40,
      }}
    >
      {/* 标题栏 */}
      <div
        onPointerDown={startDrag("move")}
        className="flex h-9 shrink-0 cursor-default items-center gap-2 border-b border-black/8 bg-slate-100/90 px-3 select-none"
      >
        <div className="flex items-center gap-2">
          <button
            type="button"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={onClose}
            title="关闭"
            className="size-3 rounded-full bg-[#ff5f57] transition hover:brightness-90"
          />
          <button
            type="button"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={onMinimize}
            title="最小化"
            className="size-3 rounded-full bg-[#febc2e] transition hover:brightness-90"
          />
          <span className="size-3 rounded-full bg-[#28c840]" />
        </div>
        <span className="mx-auto truncate text-xs font-medium text-slate-600">
          {win.title}
        </span>
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => window.open(win.url, "_blank", "noopener")}
          title="在新标签打开"
          className="grid size-6 place-items-center rounded-md text-slate-400 transition hover:bg-black/8 hover:text-slate-600"
        >
          <ExternalLinkIcon className="size-3.5" />
        </button>
      </div>

      {/* 内容 */}
      <div className="relative min-h-0 flex-1 bg-white">
        <iframe
          src={win.url}
          title={win.title}
          className="size-full border-0"
          sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads"
        />
        {/* 失焦时盖一层透明层,避免拖动经过 iframe 被吞掉指针事件 */}
        {!focused && (
          <div className="absolute inset-0" onPointerDown={onFocus} />
        )}
      </div>

      {/* 右下角缩放手柄 */}
      <div
        onPointerDown={startDrag("resize")}
        className="absolute bottom-0 right-0 size-4 cursor-nwse-resize"
      />
    </div>
  );
}
