import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import {
  ArrowRightIcon,
  BotIcon,
  CheckCircle2Icon,
  CpuIcon,
  DatabaseIcon,
  DropletIcon,
  FileCodeIcon,
  FolderIcon,
  LayersIcon,
  Maximize2Icon,
  MoonIcon,
  PlayIcon,
  RotateCcwIcon,
  SparklesIcon,
  SunIcon,
  TerminalIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { AgentPipMonitor } from "./agent-pip-monitor";

export type DesktopMode = "pure" | "workspace";
export type DesktopTheme = "theme-white" | "theme-glass" | "theme-dark";
export type OpenRoomType = "all" | "research" | "builder" | "operator";

export interface OpenRoom {
  id: OpenRoomType;
  title: string;
  agentLabel: string;
  icon: string;
  desc: string;
}

export const OPEN_ROOMS: OpenRoom[] = [
  { id: "all", title: "全局总览", agentLabel: "全员在场", icon: "◫", desc: "所有任务与空间统一呈现" },
  { id: "research", title: "调研室 (Research)", agentLabel: "Researcher 在场", icon: "🔍", desc: "文献图谱、全网检索与技术分析" },
  { id: "builder", title: "工坊 (Builder Studio)", agentLabel: "Builder 在场", icon: "⚡", desc: "终端构建、代码编译与装配流水线" },
  { id: "operator", title: "中枢 (Operator Suite)", agentLabel: "Operator 在场", icon: "🛡️", desc: "ZFS 存储、NPU 算力与硬件监控" },
];

export interface TaskObject {
  id: string;
  title: string;
  agent: "researcher" | "builder" | "operator";
  agentName: string;
  status: "running" | "completed" | "idle";
  progress: number;
  outputPreview: string;
  contextLinks: Array<{ label: string; icon: "file" | "db" | "terminal" }>;
  side: "left" | "right";
}

export interface IntentDesktopSurfaceProps {
  initialMode?: DesktopMode;
  initialTheme?: DesktopTheme;
  onOpenWorkbench?: (prompt?: string) => void;
  onOpenApp?: (appId: string) => void;
  children?: ReactNode;
  className?: string;
}

const DEFAULT_TASKS: TaskObject[] = [
  {
    id: "task-01",
    title: "全网分析 DeepSeek V3 架构并生成与 Qwen2.5 对比评测",
    agent: "researcher",
    agentName: "Researcher (调研员)",
    status: "running",
    progress: 78,
    outputPreview:
      "已完成 4 篇论文交叉对比与算力评估；正在汇总 MoE 门控稀疏激活模式与显存驻留测试曲线...",
    contextLinks: [
      { label: "arxiv_2412.19437.pdf", icon: "file" },
      { label: "Vector DB (知识库)", icon: "db" },
      { label: "Web Search Cache", icon: "terminal" },
    ],
    side: "left",
  },
  {
    id: "task-02",
    title: "RK3576 ARM64 固件装配与 NPU 加速驱动交叉编译",
    agent: "builder",
    agentName: "Builder (架构师)",
    status: "completed",
    progress: 100,
    outputPreview:
      "[BUILD_SUCCESS] rknpu2.ko driver compiled in 48.2s. Kernel image packed: echo-rk3576-v0.2.img (2.1GB).",
    contextLinks: [
      { label: "deploy/rk3576/firmware", icon: "file" },
      { label: "Cross-compiler ARM64", icon: "terminal" },
    ],
    side: "right",
  },
];

export function IntentDesktopSurface({
  initialMode = "workspace",
  initialTheme = "theme-white",
  onOpenWorkbench,
  onOpenApp,
  children,
  className,
}: IntentDesktopSurfaceProps) {
  const [mode, setMode] = useState<DesktopMode>(() => {
    const saved = localStorage.getItem("echo-desktop-mode") as DesktopMode | null;
    return saved === "pure" || saved === "workspace" ? saved : initialMode;
  });

  const [theme, setTheme] = useState<DesktopTheme>(() => {
    const saved = localStorage.getItem("echo-desktop-theme") as DesktopTheme | null;
    return saved === "theme-white" || saved === "theme-glass" || saved === "theme-dark"
      ? saved
      : initialTheme;
  });

  const [intentInput, setIntentInput] = useState("");
  const [selectedAgent, setSelectedAgent] = useState<"researcher" | "builder" | "operator">("researcher");
  const [tasks, setTasks] = useState<TaskObject[]>(DEFAULT_TASKS);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const [activeRoom, setActiveRoom] = useState<OpenRoomType>("all");
  const [pipVisible, setPipVisible] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);

  const toggleMode = useCallback(() => {
    setMode((prev) => {
      const next = prev === "pure" ? "workspace" : "pure";
      localStorage.setItem("echo-desktop-mode", next);
      toast.info(
        next === "pure"
          ? "已进入纯净意图态 (已退散工作区窗口)"
          : "已恢复多窗口工作台",
        {
          description:
            next === "pure"
              ? "点击空白壁纸或按空格键可随时恢复窗口"
              : "点击空白壁纸或按空格键可返回纯净意图态",
        }
      );
      return next;
    });
  }, []);

  const handleSelectTheme = (newTheme: DesktopTheme) => {
    setTheme(newTheme);
    localStorage.setItem("echo-desktop-theme", newTheme);
    if (typeof document !== "undefined") {
      document.documentElement.classList.remove("theme-white", "theme-glass", "theme-dark");
      document.documentElement.classList.add(newTheme);
    }
    const themeNames = {
      "theme-white": "象牙温和白",
      "theme-glass": "静谧液态玻璃",
      "theme-dark": "纯粹暗夜黑",
    };
    toast.success(`桌面主题已切换为：${themeNames[newTheme]}`);
  };

  const handleDismissTask = (taskId: string) => {
    setTasks((prev) => prev.filter((t) => t.id !== taskId));
    toast.info("任务已收纳移除");
  };

  const handleTogglePause = (taskId: string) => {
    setTasks((prev) =>
      prev.map((t) => {
        if (t.id === taskId) {
          const nextStatus = t.status === "running" ? "idle" : "running";
          toast.info(nextStatus === "idle" ? `已暂停任务：${t.title}` : `已恢复任务：${t.title}`);
          return { ...t, status: nextStatus };
        }
        return t;
      })
    );
  };

  // 点击空白壁纸退散或召回窗口 (macOS 风格)
  const handleBackdropClick = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      toggleMode();
    }
  };

  // 全局键盘人机工程学：Cmd+K / Ctrl+K / '/' 聚焦意图，Escape 退出，Space 双态互换
  useEffect(() => {
    const handleKeyDown = (e: globalThis.KeyboardEvent) => {
      const activeTag = document.activeElement?.tagName?.toLowerCase();
      const isInput = activeTag === "input" || activeTag === "textarea";

      // Cmd+K / Ctrl+K 或 '/' 快速聚焦意图输入框
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !isInput)) {
        e.preventDefault();
        inputRef.current?.focus();
        return;
      }

      // Escape 退出展开态或退散回纯净态
      if (e.key === "Escape") {
        if (expandedTaskId) {
          setExpandedTaskId(null);
          return;
        }
        if (isInput) {
          (document.activeElement as HTMLElement)?.blur();
          return;
        }
        if (mode === "workspace") {
          toggleMode();
        }
        return;
      }

      if (!isInput && e.code === "Space") {
        e.preventDefault();
        toggleMode();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [expandedTaskId, mode, toggleMode]);

  // 提交意图
  const handleSubmitIntent = () => {
    const trimmed = intentInput.trim();
    if (!trimmed) return;

    const agentNames = {
      researcher: "Researcher (调研员)",
      builder: "Builder (架构师)",
      operator: "Operator (管家)",
    };

    const newTask: TaskObject = {
      id: `task-${Date.now()}`,
      title: trimmed,
      agent: selectedAgent,
      agentName: agentNames[selectedAgent],
      status: "running",
      progress: 12,
      outputPreview: `[Agent 启动] 已分配至 ${agentNames[selectedAgent]}，正在规划执行子图与分配算力配额...`,
      contextLinks: [
        { label: "User Prompt Intent", icon: "file" },
        { label: "Active Execution Engine", icon: "terminal" },
      ],
      side: tasks.length % 2 === 0 ? "left" : "right",
    };

    setTasks((prev) => [newTask, ...prev]);
    setIntentInput("");

    // 自动切换为多窗口工作台模式以展示任务执行
    if (mode === "pure") {
      setMode("workspace");
      localStorage.setItem("echo-desktop-mode", "workspace");
    }

    toast.success(`新任务已创建并分派给 ${agentNames[selectedAgent]}`, {
      description: trimmed,
    });

    onOpenWorkbench?.(trimmed);
  };

  const handleInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmitIntent();
    }
  };

  return (
    <div
      className={cn(
        "relative flex size-full select-none flex-col overflow-hidden transition-colors duration-500",
        theme,
        theme === "theme-white" &&
          "bg-[radial-gradient(circle_at_50%_15%,#ffffff_0%,#f5f6f3_50%,#e8ebe4_100%)] text-slate-800",
        theme === "theme-glass" &&
          "bg-[radial-gradient(at_15%_15%,rgba(45,75,130,0.5)_0%,transparent_55%),radial-gradient(at_85%_85%,rgba(125,45,110,0.4)_0%,transparent_55%),linear-gradient(135deg,#0c111c_0%,#121927_50%,#090d15_100%)] text-slate-100",
        theme === "theme-dark" &&
          "bg-[radial-gradient(circle_at_50%_0%,#151820_0%,#090b0e_65%,#040507_100%)] text-slate-100",
        className
      )}
      onClick={handleBackdropClick}
      data-testid="intent-desktop-surface"
    >
      {/* 边缘提示微光 (当窗口退散到两端时给予视觉回馈) */}
      <div
        className={cn(
          "pointer-events-none absolute inset-y-12 left-0 w-3 transition-opacity duration-300",
          mode === "pure" ? "opacity-100 shadow-[inset_8px_0_20px_rgba(0,0,0,0.15)]" : "opacity-0"
        )}
      />
      <div
        className={cn(
          "pointer-events-none absolute inset-y-12 right-0 w-3 transition-opacity duration-300",
          mode === "pure" ? "opacity-100 shadow-[inset_-8px_0_20px_rgba(0,0,0,0.15)]" : "opacity-0"
        )}
      />

      {/* 顶部桌面快捷控制条 (包含双态切换胶囊与 3 色主题开关) */}
      <div className="z-40 flex items-center justify-between px-6 py-2.5">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              toggleMode();
            }}
            className={cn(
              "group inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold shadow-sm backdrop-blur-md transition-all",
              theme === "theme-white"
                ? "border-black/10 bg-white/70 text-slate-700 hover:bg-white"
                : "border-white/15 bg-white/10 text-slate-200 hover:bg-white/20"
            )}
            title="点击或按空格键在【意图纯净态】与【多窗口工作台】间无缝切换"
          >
            <span
              className={cn(
                "size-2 rounded-full transition-transform group-hover:scale-125",
                mode === "pure" ? "bg-emerald-500" : "bg-blue-500"
              )}
            />
            <span>{mode === "pure" ? "意图纯净态" : "多窗口工作台"}</span>
            <kbd className="rounded border border-black/10 bg-black/5 px-1 py-0.2 text-[9px] text-slate-400 dark:border-white/15 dark:bg-white/10">
              Space
            </kbd>
          </button>

          {/* OpenRoom 空间选择器 */}
          <div
            className="flex items-center gap-1 rounded-full border border-black/5 bg-black/[0.03] p-0.5 text-[11px] backdrop-blur-md dark:border-white/10 dark:bg-white/[0.04]"
            onClick={(e) => e.stopPropagation()}
          >
            {OPEN_ROOMS.map((room) => (
              <button
                key={room.id}
                type="button"
                onClick={() => {
                  setActiveRoom(room.id);
                  toast.info(`已进入协作空间：${room.title}`, {
                    description: `${room.agentLabel} · ${room.desc}`,
                  });
                }}
                className={cn(
                  "flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-all",
                  activeRoom === room.id
                    ? "bg-white text-slate-900 shadow-sm dark:bg-white/20 dark:text-white font-semibold"
                    : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
                )}
                title={room.desc}
              >
                <span>{room.icon}</span>
                <span>{room.title.split(" ")[0]}</span>
              </button>
            ))}
          </div>
        </div>

        {/* 主题选择微组件 */}
        <div
          className={cn(
            "flex items-center rounded-lg border p-0.5 text-[11px] font-medium backdrop-blur-md",
            theme === "theme-white"
              ? "border-black/10 bg-black/5 text-slate-600"
              : "border-white/15 bg-white/5 text-slate-300"
          )}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            onClick={() => handleSelectTheme("theme-white")}
            className={cn(
              "flex items-center gap-1 rounded-md px-2 py-0.5 transition-all",
              theme === "theme-white" && "bg-white text-slate-900 shadow-sm"
            )}
          >
            <SunIcon className="size-3" />
            <span>象牙白</span>
          </button>
          <button
            type="button"
            onClick={() => handleSelectTheme("theme-glass")}
            className={cn(
              "flex items-center gap-1 rounded-md px-2 py-0.5 transition-all",
              theme === "theme-glass" && "bg-blue-500/20 text-blue-300 shadow-sm"
            )}
          >
            <DropletIcon className="size-3" />
            <span>液态玻璃</span>
          </button>
          <button
            type="button"
            onClick={() => handleSelectTheme("theme-dark")}
            className={cn(
              "flex items-center gap-1 rounded-md px-2 py-0.5 transition-all",
              theme === "theme-dark" && "bg-slate-800 text-white shadow-sm"
            )}
          >
            <MoonIcon className="size-3" />
            <span>暗夜黑</span>
          </button>
        </div>
      </div>

      {/* 桌面主要视口区域 */}
      <div
        className="relative flex flex-1 flex-col items-center justify-center px-6 pb-20"
        onClick={handleBackdropClick}
      >
        {/* 中央 Zen Intent 意图核心 */}
        <div
          className={cn(
            "z-20 flex w-full max-w-2xl flex-col items-center text-center transition-all duration-500",
            mode === "workspace"
              ? "-translate-y-20 scale-90 opacity-80 pointer-events-auto"
              : "translate-y-0 scale-100 opacity-100"
          )}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Echo 核心呼吸节点 */}
          <div
            className="group mb-5 flex size-12 cursor-pointer items-center justify-center rounded-full bg-slate-900 shadow-lg shadow-black/20 ring-8 ring-black/5 transition-transform hover:scale-110 dark:bg-white dark:ring-white/10"
            onClick={toggleMode}
            title="Echo 原生节点"
          >
            <span className="size-3.5 animate-pulse rounded-full bg-white dark:bg-slate-900" />
          </div>

          <h2 className="text-sm font-medium tracking-wide text-slate-500 dark:text-slate-400">
            下午好，今天有什么探索计划？
          </h2>
          <h1 className="mt-1 text-3xl font-extrabold tracking-tight">
            意图优先 · 让 Agent 驱动执行
          </h1>

          {/* 意图输入胶囊 */}
          <div
            className={cn(
              "mt-6 w-full rounded-2xl border p-3 shadow-2xl backdrop-blur-2xl transition-all focus-within:ring-2 focus-within:ring-blue-500/30",
              theme === "theme-white"
                ? "border-black/10 bg-white/95 text-slate-900 shadow-black/5"
                : "border-white/15 bg-slate-900/80 text-white shadow-black/40"
            )}
          >
            <div className="flex items-center gap-3">
              <SparklesIcon className="size-5 shrink-0 text-blue-500" />
              <input
                ref={inputRef}
                type="text"
                value={intentInput}
                onChange={(e) => setIntentInput(e.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder="输入意图，例如：分析 NAS 存储碎片化并生成优化方案..."
                className="flex-1 bg-transparent text-sm font-medium outline-none placeholder:text-slate-400"
              />
              <button
                type="button"
                onClick={handleSubmitIntent}
                disabled={!intentInput.trim()}
                className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-slate-900 text-white transition-all hover:scale-105 disabled:opacity-40 dark:bg-white dark:text-slate-900"
                aria-label="提交意图"
              >
                <ArrowRightIcon className="size-4" />
              </button>
            </div>

            {/* 意图标签与 Agent 切换 */}
            <div className="mt-3 flex items-center justify-between border-t border-black/5 pt-2.5 text-xs dark:border-white/10">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] font-semibold text-slate-400">
                  分派:
                </span>
                {(
                  [
                    { key: "researcher", label: "Researcher" },
                    { key: "builder", label: "Builder" },
                    { key: "operator", label: "Operator" },
                  ] as const
                ).map((ag) => (
                  <button
                    key={ag.key}
                    type="button"
                    onClick={() => setSelectedAgent(ag.key)}
                    className={cn(
                      "rounded-md px-2 py-0.5 text-[11px] font-medium transition-all",
                      selectedAgent === ag.key
                        ? "bg-blue-600 text-white shadow-sm"
                        : "bg-black/5 text-slate-600 hover:bg-black/10 dark:bg-white/10 dark:text-slate-300"
                    )}
                  >
                    {ag.label}
                  </button>
                ))}
              </div>

              <div className="hidden items-center gap-1.5 sm:flex">
                <span className="text-[10px] text-slate-400">快捷预设:</span>
                {[
                  "全网调研 DeepSeek",
                  "固件装配编译",
                  "存储快照体检",
                ].map((hint) => (
                  <button
                    key={hint}
                    type="button"
                    onClick={() => setIntentInput(hint)}
                    className="rounded bg-black/5 px-1.5 py-0.5 text-[10px] text-slate-500 hover:text-slate-900 dark:bg-white/10 dark:text-slate-400 dark:hover:text-white"
                  >
                    {hint}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Task Objects 任务窗口卡片 (多窗口工作区一级公民) */}
        <div
          className={cn(
            "pointer-events-none absolute inset-x-8 bottom-16 top-24 z-30 flex items-center justify-between gap-6 transition-all duration-500",
            mode === "workspace" ? "pointer-events-auto" : ""
          )}
        >
          {tasks.slice(0, 2).map((task) => {
            const isLeft = task.side === "left";
            return (
              <div
                key={task.id}
                className={cn(
                  "flex h-[360px] w-full max-w-[500px] flex-col overflow-hidden rounded-2xl border shadow-2xl backdrop-blur-2xl transition-all duration-500",
                  theme === "theme-white"
                    ? "border-black/10 bg-white/90 text-slate-800 shadow-slate-300/40"
                    : "border-white/15 bg-slate-900/90 text-slate-100 shadow-black/60",
                  // 双态退散核心动效：pure 模式向左/右滑出屏幕并淡化
                  mode === "pure" &&
                    (isLeft
                      ? "-translate-x-[115%] scale-90 opacity-15 pointer-events-none"
                      : "translate-x-[115%] scale-90 opacity-15 pointer-events-none"),
                  mode === "workspace" && "translate-x-0 scale-100 opacity-100"
                )}
                onClick={(e) => e.stopPropagation()}
              >
                {/* 窗口头部：macOS 交通灯与任务标题 */}
                <div className="flex h-9 items-center justify-between border-b border-black/5 bg-black/[0.02] px-3.5 dark:border-white/10 dark:bg-white/[0.02]">
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDismissTask(task.id);
                        }}
                        className="size-2.5 rounded-full bg-red-400/80 transition-transform hover:scale-125 hover:bg-red-500"
                        title="收纳移除此任务"
                        aria-label="关闭任务"
                      />
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleTogglePause(task.id);
                        }}
                        className="size-2.5 rounded-full bg-amber-400/80 transition-transform hover:scale-125 hover:bg-amber-500"
                        title={task.status === "running" ? "暂停任务" : "恢复执行"}
                        aria-label="暂停或继续任务"
                      />
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setExpandedTaskId(task.id);
                        }}
                        className="size-2.5 rounded-full bg-emerald-400/80 transition-transform hover:scale-125 hover:bg-emerald-500"
                        title="展开任务详情"
                        aria-label="展开任务详情"
                      />
                    </div>
                    <span className="text-[11px] font-semibold text-slate-400">
                      [{task.agentName.split(" ")[0]}]
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                        task.status === "completed"
                          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                          : "bg-blue-500/10 text-blue-600 dark:text-blue-400"
                      )}
                    >
                      {task.status === "completed"
                        ? "已完成 100%"
                        : `执行中 ${task.progress}%`}
                    </span>
                  </div>
                </div>

                {/* 窗口分栏：左侧 Context，右侧 Live Execution */}
                <div className="flex flex-1 overflow-hidden">
                  {/* Context Pane */}
                  <div className="flex w-36 flex-col border-r border-black/5 bg-black/[0.01] p-3 dark:border-white/10 dark:bg-white/[0.01]">
                    <span className="text-[9px] font-bold uppercase tracking-wider text-slate-400">
                      上下文关联
                    </span>
                    <div className="mt-2 flex flex-col gap-1.5 text-[11px]">
                      {task.contextLinks.map((link, idx) => (
                        <div
                          key={idx}
                          className="flex items-center gap-1.5 truncate text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200"
                          title={link.label}
                        >
                          {link.icon === "file" && (
                            <FolderIcon className="size-3 text-amber-500" />
                          )}
                          {link.icon === "db" && (
                            <DatabaseIcon className="size-3 text-blue-500" />
                          )}
                          {link.icon === "terminal" && (
                            <TerminalIcon className="size-3 text-emerald-500" />
                          )}
                          <span className="truncate">{link.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Live Stream / Progress Pane */}
                  <div className="flex flex-1 flex-col justify-between p-3.5">
                    <div>
                      <h4 className="line-clamp-2 text-xs font-semibold leading-relaxed">
                        {task.title}
                      </h4>
                      <div className="mt-3 rounded-lg bg-black/[0.03] p-2.5 font-mono text-[11px] leading-relaxed text-slate-600 dark:bg-white/[0.04] dark:text-slate-300">
                        {task.outputPreview}
                      </div>
                    </div>

                    <div className="flex items-center justify-between pt-2">
                      <div className="flex items-center gap-1.5 text-[10px] text-slate-400">
                        <span>Agent 持续自适应迭代中</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => setExpandedTaskId(task.id)}
                          className="rounded-lg border border-black/10 px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-black/5 dark:border-white/15 dark:text-slate-300 dark:hover:bg-white/10"
                        >
                          查看详情
                        </button>
                        <button
                          type="button"
                          onClick={() => onOpenWorkbench?.(task.title)}
                          className="rounded-lg bg-slate-900 px-2.5 py-1 text-[11px] font-semibold text-white transition-all hover:bg-slate-800 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-200"
                        >
                          打开空间 →
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* 纯净意图态下的底部轻提示 */}
        {mode === "pure" && (
          <div
            className={cn(
              "pointer-events-none absolute bottom-4 z-20 flex items-center gap-2 rounded-full border px-4 py-1 text-xs font-medium shadow-sm backdrop-blur-md transition-all",
              theme === "theme-white"
                ? "border-black/10 bg-white/80 text-slate-600"
                : "border-white/15 bg-slate-900/80 text-slate-300"
            )}
          >
            <span className="size-1.5 animate-ping rounded-full bg-emerald-500" />
            <span>纯净意图态 · 按 Space 空格键或点击空白壁纸召回工作台</span>
          </div>
        )}

        {/* 展开的任务全图与上下文 Drawer/Dialog */}
        {expandedTaskId && (() => {
          const expandedTask = tasks.find((t) => t.id === expandedTaskId);
          if (!expandedTask) return null;
          return (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6 backdrop-blur-sm"
              onClick={() => setExpandedTaskId(null)}
            >
              <div
                className={cn(
                  "relative flex h-[480px] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border p-6 shadow-2xl backdrop-blur-2xl transition-all",
                  theme === "theme-white"
                    ? "border-black/10 bg-white/95 text-slate-800 shadow-black/10"
                    : "border-white/15 bg-slate-900/95 text-slate-100 shadow-black/60"
                )}
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-between border-b border-black/5 pb-3.5 dark:border-white/10">
                  <div className="flex items-center gap-2.5">
                    <span className="flex size-7 items-center justify-center rounded-lg bg-blue-500/10 text-xs font-bold text-blue-600 dark:text-blue-400">
                      {expandedTask.agentName.charAt(0)}
                    </span>
                    <div>
                      <h3 className="text-sm font-bold leading-tight">{expandedTask.title}</h3>
                      <p className="text-[11px] text-slate-400">
                        分派员工：{expandedTask.agentName} · 执行进度：{expandedTask.progress}%
                      </p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setExpandedTaskId(null)}
                    className="rounded-lg p-1.5 text-slate-400 hover:bg-black/5 hover:text-slate-700 dark:hover:bg-white/10 dark:hover:text-slate-200"
                    aria-label="关闭详情"
                  >
                    <XIcon className="size-4" />
                  </button>
                </div>

                <div className="my-4 flex-1 space-y-4 overflow-y-auto pr-1 text-xs">
                  <div>
                    <h4 className="font-semibold text-slate-400">实时执行流与控制台日志</h4>
                    <div className="mt-1.5 rounded-xl bg-black/[0.03] p-3 font-mono text-[11px] leading-relaxed text-slate-600 dark:bg-white/[0.04] dark:text-slate-300">
                      {expandedTask.outputPreview}
                    </div>
                  </div>

                  <div>
                    <h4 className="font-semibold text-slate-400">挂载上下文资产 (Context Graph)</h4>
                    <div className="mt-1.5 flex flex-wrap gap-2">
                      {expandedTask.contextLinks.map((link, idx) => (
                        <div
                          key={idx}
                          className="flex items-center gap-1.5 rounded-lg border border-black/5 bg-black/[0.02] px-2.5 py-1 text-[11px] text-slate-600 dark:border-white/10 dark:bg-white/[0.02] dark:text-slate-300"
                        >
                          {link.icon === "file" && <FolderIcon className="size-3 text-amber-500" />}
                          {link.icon === "db" && <DatabaseIcon className="size-3 text-blue-500" />}
                          {link.icon === "terminal" && <TerminalIcon className="size-3 text-emerald-500" />}
                          <span>{link.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between border-t border-black/5 pt-3.5 dark:border-white/10">
                  <button
                    type="button"
                    onClick={() => handleTogglePause(expandedTask.id)}
                    className="rounded-lg border border-black/10 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-black/5 dark:border-white/15 dark:text-slate-300 dark:hover:bg-white/10"
                  >
                    {expandedTask.status === "running" ? "暂停任务" : "恢复执行"}
                  </button>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        handleDismissTask(expandedTask.id);
                        setExpandedTaskId(null);
                      }}
                      className="rounded-lg px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/30"
                    >
                      收纳移除
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setExpandedTaskId(null);
                        onOpenWorkbench?.(expandedTask.title);
                      }}
                      className="rounded-lg bg-slate-900 px-4 py-1.5 text-xs font-semibold text-white transition-all hover:bg-slate-800 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-200"
                    >
                      在工作台中深入会话 →
                    </button>
                  </div>
                </div>
              </div>
            </div>
          );
        })()}

        {/* Codex 风格画中画 (PiP) 浮窗：后台执行监视与危险操作放行审批 */}
        <AgentPipMonitor
          visible={pipVisible}
          onClose={() => setPipVisible(false)}
          onExpandToWindow={() => {
            setMode("workspace");
            onOpenWorkbench?.("RK3576 驱动装配与编译");
          }}
        />

        {/* 既有子节点（支持承载系统级窗口或扩展小组件） */}
        {children && (
          <div
            className={cn(
              "pointer-events-none absolute inset-0 z-10 transition-all duration-500",
              mode === "pure" ? "scale-95 opacity-0" : "scale-100 opacity-100 pointer-events-auto"
            )}
          >
            {children}
          </div>
        )}
      </div>
    </div>
  );
}
