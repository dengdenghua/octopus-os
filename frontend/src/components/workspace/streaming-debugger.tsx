import { useState, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  BugIcon,
  CopyIcon,
  DownloadIcon,
  FilterIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import type { LiveToolEvent } from "./live-tool-timeline";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface StreamingDebuggerProps {
  events: LiveToolEvent[];
  className?: string;
}

/**
 * 流式事件调试面板
 *
 * 开发者工具，用于实时追踪和调试流式事件流。
 * 仅在开发环境或通过 localStorage flag 启用。
 *
 * 启用方式:
 * localStorage.setItem('echo:debug:streaming', '1')
 *
 * 功能:
 * - 实时事件列表
 * - 事件详情查看
 * - 事件复制/导出
 * - 事件序列重放（用于自动化测试）
 *
 * 优化目标:
 * - 开发调试效率提升 50%
 * - Bug 定位时间从 2h → 30min
 */
export function StreamingDebugger({
  events,
  className,
}: StreamingDebuggerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [selectedEvent, setSelectedEvent] = useState<LiveToolEvent | null>(
    null,
  );
  const [filterType, setFilterType] = useState<"all" | "error" | "running">(
    "all",
  );

  // 仅在开发环境或显式启用时可用
  const enabled =
    import.meta.env.DEV ||
    (typeof window !== "undefined" &&
      window.localStorage.getItem("echo:debug:streaming") === "1");

  const filteredEvents = useMemo(() => {
    return events.filter((e) => {
      // 类型筛选
      if (filterType === "error" && e.status !== "error") return false;
      if (filterType === "running" && e.status !== "running") return false;

      // 文本筛选
      if (!filter) return true;
      const query = filter.toLowerCase();
      return (
        e.name.toLowerCase().includes(query) ||
        e.id.toLowerCase().includes(query) ||
        e.agentName?.toLowerCase().includes(query)
      );
    });
  }, [events, filter, filterType]);

  const stats = useMemo(() => {
    const total = events.length;
    const running = events.filter((e) => e.status === "running").length;
    const done = events.filter((e) => e.status === "done").length;
    const error = events.filter((e) => e.status === "error").length;
    const waiting = events.filter(
      (e) => e.status === "waiting_approval",
    ).length;

    return { total, running, done, error, waiting };
  }, [events]);

  const handleCopyEvent = () => {
    if (!selectedEvent) return;
    navigator.clipboard.writeText(JSON.stringify(selectedEvent, null, 2));
    toast.success("事件已复制到剪贴板");
  };

  const handleExportSequence = () => {
    if (!selectedEvent) return;
    const index = events.indexOf(selectedEvent);
    const sequence = events.slice(0, index + 1);
    const json = JSON.stringify(sequence, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `events-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success("事件序列已导出");
  };

  const handleExportAll = () => {
    const json = JSON.stringify(events, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `all-events-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success(`已导出 ${events.length} 个事件`);
  };

  if (!enabled) return null;

  return (
    <>
      {/* 悬浮按钮 — 同样 portal 到 body，否则被 main 的 z-1 关进内层上下文。 */}
      {createPortal(
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className={cn(
            // Dev-only affordance: tucked into the very corner at a small size
            // so it never competes with the composer or the paused-tasks
            // banner (which owns bottom-4 right-4 at z-50 — we sit below it).
            "fixed bottom-1.5 right-1.5 z-[199] flex size-7 items-center justify-center rounded-full",
            "border border-border-default bg-background/80 text-muted-foreground/70 backdrop-blur-sm",
            "opacity-35 transition-[opacity,color,background-color] duration-fast",
            "hover:opacity-100 hover:text-foreground hover:bg-background",
            "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/50",
            isOpen &&
              "opacity-100 border-primary/40 bg-primary/10 text-primary",
            className,
          )}
          title="流式调试面板 (Ctrl+Shift+D)"
        >
          <BugIcon className="size-3.5" />
        </button>,
        document.body,
      )}

      {/* 调试面板 — portal 到 body：本组件挂在 <main class="relative z-1"> 内，
          而侧栏是 main 的兄弟节点且为 z-10。面板的 z-50 只在 main 的层叠上下文
          内生效，跨兄弟比较时用的是 main 的 z-1，所以不 portal 就会被侧栏盖住
          左侧一截。 */}
      {isOpen &&
        createPortal(
          <div className="fixed inset-4 z-[200] flex flex-col overflow-hidden rounded-lg border border-border-default bg-background shadow-2xl">
            {/* 头部 */}
            <div className="flex shrink-0 items-center gap-3 border-b border-border-subtle bg-muted/30 px-4 py-3">
              <BugIcon className="size-5 text-primary" />
              <div className="flex-1">
                <h3 className="text-sm font-semibold">流式事件追踪</h3>
                <p className="text-xs text-muted-foreground">
                  共 {stats.total} 个事件 · 运行 {stats.running} · 完成{" "}
                  {stats.done} · 错误 {stats.error}
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={handleExportAll}
                className="shrink-0"
              >
                <DownloadIcon className="size-4 mr-1.5" />
                导出全部
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setIsOpen(false)}
                className="shrink-0"
              >
                <XIcon className="size-4" />
              </Button>
            </div>

            {/* 筛选栏 */}
            <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle bg-background px-4 py-2">
              <FilterIcon className="size-4 text-muted-foreground" />
              <Input
                placeholder="筛选事件 (名称/ID/Agent)..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="h-8 flex-1 text-xs"
              />
              <div className="flex gap-1">
                {(["all", "running", "error"] as const).map((type) => (
                  <button
                    key={type}
                    type="button"
                    onClick={() => setFilterType(type)}
                    className={cn(
                      "rounded-md px-2 py-1 text-xs font-medium transition-colors",
                      filterType === type
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:bg-muted",
                    )}
                  >
                    {type === "all" && "全部"}
                    {type === "running" && "运行中"}
                    {type === "error" && "错误"}
                  </button>
                ))}
              </div>
            </div>

            {/* 内容区 */}
            <div className="flex min-h-0 flex-1">
              {/* 事件列表 */}
              <div className="w-2/5 overflow-y-auto border-r border-border-subtle">
                {filteredEvents.map((event, index) => (
                  <button
                    key={event.id}
                    type="button"
                    onClick={() => setSelectedEvent(event)}
                    className={cn(
                      "flex w-full items-center gap-2 border-b border-border-subtle px-3 py-2 text-left transition-colors hover:bg-muted/50",
                      selectedEvent?.id === event.id && "bg-muted",
                    )}
                  >
                    <span className="shrink-0 text-xs font-mono text-muted-foreground">
                      #{index}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-xs font-medium">
                          {event.name}
                        </span>
                        {event.agentName && (
                          <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                            {event.agentName}
                          </span>
                        )}
                      </div>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {event.id}
                      </span>
                    </div>
                    <span
                      className={cn(
                        "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium",
                        event.status === "running" &&
                          "bg-blue-500/10 text-blue-500",
                        event.status === "done" &&
                          "bg-green-500/10 text-green-500",
                        event.status === "error" &&
                          "bg-red-500/10 text-red-500",
                        event.status === "waiting_approval" &&
                          "bg-yellow-500/10 text-yellow-500",
                      )}
                    >
                      {event.status}
                    </span>
                    <span className="shrink-0 text-[10px] text-muted-foreground">
                      {new Date(event.startedAt).toLocaleTimeString()}
                    </span>
                  </button>
                ))}
              </div>

              {/* 事件详情 */}
              <div className="flex flex-1 flex-col overflow-hidden">
                {selectedEvent ? (
                  <>
                    <div className="shrink-0 border-b border-border-subtle bg-muted/30 px-4 py-3">
                      <h4 className="text-sm font-semibold">
                        {selectedEvent.name}
                      </h4>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {selectedEvent.id}
                      </p>
                    </div>
                    <div className="flex-1 overflow-y-auto p-4">
                      <pre className="text-xs">
                        {JSON.stringify(selectedEvent, null, 2)}
                      </pre>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 border-t border-border-subtle bg-muted/30 px-4 py-3">
                      <Button size="sm" onClick={handleCopyEvent}>
                        <CopyIcon className="size-3.5 mr-1.5" />
                        复制事件
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={handleExportSequence}
                      >
                        <DownloadIcon className="size-3.5 mr-1.5" />
                        导出序列
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                    选择事件查看详情
                  </div>
                )}
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
