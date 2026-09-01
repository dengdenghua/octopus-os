import { cn } from "@/lib/utils";
import { navigateToEchoOsDesktop } from "@/core/navigation/desktop-return";
import { env } from "@/env";
import {
  isEmbeddedWindow,
  type EmbeddedWindowAction,
} from "./embedded-window-bridge";

const inElectron = (): boolean =>
  typeof window !== "undefined" && !!window.echo?.isElectron;

const dots: Array<{
  action: EmbeddedWindowAction;
  label: string;
  className: string;
}> = [
  {
    action: "close",
    label: "关闭窗口",
    className: "border-destructive/45 bg-destructive",
  },
  {
    action: "minimize",
    label: "最小化窗口",
    className: "border-warning/45 bg-warning",
  },
  {
    action: "maximize",
    label: "缩放窗口",
    className: "border-success/45 bg-success",
  },
];

export function MacWindowControls({ className }: { className?: string }) {
  // Electron 和 Echo OS 嵌入态均由真正的窗口宿主提供按钮，Agent 不能再
  // 画第二套。只有独立 Web Agent 自己渲染这一排可操作按钮。
  if (inElectron() || isEmbeddedWindow()) return null;

  const handleControl = (action: EmbeddedWindowAction) => {
    if (action === "maximize") {
      void (async () => {
        try {
          if (document.fullscreenElement) {
            await document.exitFullscreen?.();
          } else {
            await document.documentElement.requestFullscreen?.();
          }
        } catch {
          // The browser may deny fullscreen while the rest of the title bar
          // remains usable; closing and minimizing still return to Echo OS.
        }
      })();
      return;
    }
    navigateToEchoOsDesktop(env.ECHO_OS_DESKTOP_URL);
  };

  return (
    <div
      className={cn(
        "flex h-8 w-12 shrink-0 items-center justify-center gap-1.5",
        className,
      )}
    >
      {dots.map((dot) => {
        const dotClass = cn(
          "block h-3 w-3 shrink-0 rounded-full border shadow-[inset_0_0.5px_0_rgba(255,255,255,0.55)]",
          dot.className,
        );
        return (
          <button
            key={dot.action}
            type="button"
            aria-label={dot.label}
            title={dot.label}
            className={dotClass}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => handleControl(dot.action)}
          />
        );
      })}
    </div>
  );
}
