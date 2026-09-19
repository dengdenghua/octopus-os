import { useState } from "react";
import {
  AlertCircleIcon,
  BotIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ExternalLinkIcon,
  Maximize2Icon,
  Minimize2Icon,
  TerminalIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

export interface PipTask {
  id: string;
  title: string;
  agentName: string;
  agentType: "researcher" | "builder" | "operator";
  stepText: string;
  progress: number;
  needsApproval?: boolean;
  approvalRiskMessage?: string;
}

export interface AgentPipMonitorProps {
  task?: PipTask;
  visible?: boolean;
  onExpandToWindow?: () => void;
  onClose?: () => void;
  className?: string;
}

const DEFAULT_PIP_TASK: PipTask = {
  id: "pip-task-live",
  title: "RK3576 驱动装配与编译",
  agentName: "Builder (架构师)",
  agentType: "builder",
  stepText: "正在执行交叉编译: aarch64-linux-gnu-gcc -O2 rknpu2.c -o rknpu2.ko...",
  progress: 68,
  needsApproval: true,
  approvalRiskMessage: "申请写入系统内核模块 /lib/modules/6.1.0/kernel/drivers/rknpu",
};

export function AgentPipMonitor({
  task = DEFAULT_PIP_TASK,
  visible = true,
  onExpandToWindow,
  onClose,
  className,
}: AgentPipMonitorProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [approvalHandled, setApprovalHandled] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);

  if (!visible || isDismissed) return null;

  const handleApprove = () => {
    setApprovalHandled(true);
    toast.success("已批准执行高危操作", {
      description: task.approvalRiskMessage,
    });
  };

  const handleReject = () => {
    setApprovalHandled(true);
    toast.error("已拦截并拒绝高危操作");
  };

  // 极简折叠态：仅留一个微脉冲呼吸胶囊
  if (collapsed) {
    return (
      <div
        className={cn(
          "fixed bottom-6 right-6 z-50 flex cursor-pointer items-center gap-2 rounded-full border border-black/10 bg-white/85 px-3 py-1.5 shadow-xl backdrop-blur-xl transition-all hover:scale-105 hover:bg-white dark:border-white/15 dark:bg-slate-900/85 dark:hover:bg-slate-900",
          className
        )}
        onClick={() => setCollapsed(false)}
        title="点击展开 Codex 画中画监控"
      >
        <span className="relative flex size-2 items-center justify-center">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-blue-400 opacity-60" />
          <span className="relative inline-flex size-1.5 rounded-full bg-blue-500" />
        </span>
        <span className="text-[11px] font-semibold text-slate-700 dark:text-slate-200">
          {task.agentName.split(" ")[0]} PiP · {task.progress}%
        </span>
        <ChevronUpIcon className="size-3 text-slate-400" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "fixed bottom-6 right-6 z-50 flex w-80 flex-col overflow-hidden rounded-2xl border border-black/10 bg-white/90 text-slate-800 shadow-2xl shadow-black/15 backdrop-blur-2xl transition-all dark:border-white/15 dark:bg-slate-900/90 dark:text-slate-100",
        className
      )}
      data-testid="agent-pip-monitor"
    >
      {/* 头部：Agent 身份、状态灯与控制按钮 */}
      <div className="flex h-8 items-center justify-between border-b border-black/5 bg-black/[0.02] px-3 dark:border-white/10 dark:bg-white/[0.02]">
        <div className="flex items-center gap-1.5">
          <span className="relative flex size-2 items-center justify-center">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex size-1.5 rounded-full bg-emerald-500" />
          </span>
          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
            Codex PiP 画中画 · {task.agentName.split(" ")[0]}
          </span>
        </div>

        <div className="flex items-center gap-1 text-slate-400">
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="rounded p-1 hover:bg-black/5 hover:text-slate-700 dark:hover:bg-white/10 dark:hover:text-slate-200"
            title="最小化为胶囊"
            aria-label="最小化"
          >
            <ChevronDownIcon className="size-3" />
          </button>
          <button
            type="button"
            onClick={onExpandToWindow}
            className="rounded p-1 hover:bg-black/5 hover:text-slate-700 dark:hover:bg-white/10 dark:hover:text-slate-200"
            title="放大为全屏窗口"
            aria-label="放大为全屏窗口"
          >
            <Maximize2Icon className="size-3" />
          </button>
          <button
            type="button"
            onClick={() => {
              setIsDismissed(true);
              onClose?.();
            }}
            className="rounded p-1 hover:bg-black/5 hover:text-slate-700 dark:hover:bg-white/10 dark:hover:text-slate-200"
            title="关闭画中画"
            aria-label="关闭画中画"
          >
            <XIcon className="size-3" />
          </button>
        </div>
      </div>

      {/* 主体：当前执行微步与终端预览 */}
      <div className="p-3">
        <div className="flex items-center justify-between text-xs font-semibold">
          <span className="truncate pr-2">{task.title}</span>
          <span className="shrink-0 text-[10px] text-blue-600 dark:text-blue-400 font-mono">
            {task.progress}%
          </span>
        </div>

        {/* 紧凑进度条 */}
        <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-black/5 dark:bg-white/10">
          <div
            className="h-full bg-blue-500 transition-all duration-300"
            style={{ width: `${task.progress}%` }}
          />
        </div>

        {/* 控制台微预览 */}
        <div className="mt-2.5 flex items-start gap-1.5 rounded-lg bg-black/[0.04] p-2 font-mono text-[10px] leading-relaxed text-slate-600 dark:bg-white/[0.04] dark:text-slate-300">
          <TerminalIcon className="mt-0.5 size-3 shrink-0 text-slate-400" />
          <span className="line-clamp-2">{task.stepText}</span>
        </div>

        {/* 关键安全决策卡片 (Human-in-the-loop 快速批准) */}
        {task.needsApproval && !approvalHandled && (
          <div className="mt-2.5 rounded-lg border border-amber-500/30 bg-amber-50/70 p-2 text-[11px] dark:border-amber-400/30 dark:bg-amber-950/40">
            <div className="flex items-center gap-1.5 font-semibold text-amber-700 dark:text-amber-400">
              <AlertCircleIcon className="size-3.5 shrink-0" />
              <span>需要操作授权</span>
            </div>
            <p className="mt-1 text-[10px] text-slate-600 dark:text-slate-300">
              {task.approvalRiskMessage}
            </p>
            <div className="mt-2 flex items-center justify-end gap-1.5">
              <button
                type="button"
                onClick={handleReject}
                className="rounded-md border border-black/10 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50 dark:border-white/15 dark:bg-white/10 dark:text-slate-300"
              >
                拦截
              </button>
              <button
                type="button"
                onClick={handleApprove}
                className="rounded-md bg-amber-600 px-2 py-0.5 text-[10px] font-semibold text-white shadow-sm hover:bg-amber-700"
              >
                批准放行
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
