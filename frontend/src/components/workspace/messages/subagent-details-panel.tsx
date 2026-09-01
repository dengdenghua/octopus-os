import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  CheckCircleIcon,
  ClockIcon,
  FileIcon,
  Loader2Icon,
  RefreshCwIcon,
  XCircleIcon,
} from "lucide-react";
import { useI18n } from "@/core/i18n/hooks";
import type { Subtask } from "@/core/tasks/types";

interface SubagentDetailsPanelProps {
  task: Subtask | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Format duration in milliseconds to human-readable string
 */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) {
    return remainingSeconds > 0
      ? `${minutes}m ${remainingSeconds}s`
      : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

/**
 * Detailed side panel for subagent execution information.
 * Similar to Kimi's right-side detail panel.
 */
export function SubagentDetailsPanel({
  task,
  open,
  onOpenChange,
}: SubagentDetailsPanelProps) {
  const { t } = useI18n();

  if (!task) return null;

  const isCompleted = task.status === "completed";
  const isFailed =
    task.status === "failed" ||
    task.status === "timed_out" ||
    task.status === "cancelled";
  const isRunning =
    task.status === "iterating" ||
    task.status === "reasoning" ||
    task.status === "generating" ||
    task.status === "analyzing" ||
    task.status === "summarizing";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[600px] overflow-y-auto">
        <SheetHeader>
          <div className="flex items-center gap-3">
            {task.avatarEmoji && (
              <div
                className="flex size-12 shrink-0 items-center justify-center rounded-lg text-2xl"
                style={
                  task.hue != null
                    ? { background: `hsl(${task.hue} 70% 92%)` }
                    : undefined
                }
              >
                {task.avatarEmoji}
              </div>
            )}
            <div className="min-w-0 flex-1">
              <SheetTitle className="truncate">
                {task.name ?? task.description}
              </SheetTitle>
              <SheetDescription className="truncate">
                {task.role ?? task.subagent_type ?? t.message.assistant}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="mt-6 space-y-6">
          {/* 状态和统计信息 */}
          <div>
            <h3 className="mb-3 text-sm font-semibold">
              {t.subagents.executionHistory}
            </h3>
            <div className="grid grid-cols-3 gap-4">
              {/* 状态 */}
              <div className="rounded-lg border border-border bg-muted/30 p-3">
                <div className="flex items-center gap-2">
                  {isCompleted && (
                    <CheckCircleIcon className="size-4 text-success" />
                  )}
                  {isFailed && (
                    <XCircleIcon className="size-4 text-destructive" />
                  )}
                  {isRunning && (
                    <Loader2Icon className="size-4 animate-spin text-primary" />
                  )}
                  <span className="text-xs text-muted-foreground">Status</span>
                </div>
                <div className="mt-1 font-semibold">
                  {isCompleted && t.subagents.completed}
                  {isFailed && t.subagents.failed}
                  {isRunning && t.subagents.in_progress}
                  {!isCompleted &&
                    !isFailed &&
                    !isRunning &&
                    t.subagents.pending}
                </div>
              </div>

              {/* 迭代次数 */}
              {task.iterationCount !== undefined && (
                <div className="rounded-lg border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <RefreshCwIcon className="size-4 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">
                      {t.subagents.iterations}
                    </span>
                  </div>
                  <div className="mt-1 font-semibold">
                    {task.iterationCount}
                  </div>
                </div>
              )}

              {/* 执行时长 */}
              {task.duration && (
                <div className="rounded-lg border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <ClockIcon className="size-4 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">
                      {t.subagents.duration}
                    </span>
                  </div>
                  <div className="mt-1 font-semibold">
                    {formatDuration(task.duration)}
                  </div>
                </div>
              )}

              {/* 文件修改数 */}
              {task.filesTouched && task.filesTouched.length > 0 && (
                <div className="rounded-lg border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <FileIcon className="size-4 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">
                      {t.subagents.filesModified}
                    </span>
                  </div>
                  <div className="mt-1 font-semibold">
                    {task.filesTouched.length}
                  </div>
                </div>
              )}

              {/* Token 使用 */}
              {task.tokenUsed !== undefined && (
                <div className="rounded-lg border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">
                      Tokens
                    </span>
                  </div>
                  <div className="mt-1 font-semibold">
                    {task.tokenUsed.toLocaleString()}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* 任务描述 */}
          {task.prompt && (
            <div>
              <h3 className="mb-2 text-sm font-semibold">Task Description</h3>
              <div className="whitespace-pre-wrap rounded-lg border border-border bg-muted/20 p-4 text-sm">
                {task.prompt}
              </div>
            </div>
          )}

          {/* 修改的文件列表 */}
          {task.filesTouched && task.filesTouched.length > 0 && (
            <div>
              <h3 className="mb-2 text-sm font-semibold">
                {t.subagents.modifiedFiles}
              </h3>
              <ul className="space-y-1">
                {task.filesTouched.map((file, index) => (
                  <li
                    key={index}
                    className="flex items-center gap-2 rounded-md bg-muted/30 px-3 py-2 text-sm"
                  >
                    <FileIcon className="size-3 text-muted-foreground" />
                    <span className="font-mono truncate">{file}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 执行结果 */}
          {task.result && (
            <div>
              <h3 className="mb-2 text-sm font-semibold">Result</h3>
              <div className="whitespace-pre-wrap rounded-lg border border-border bg-muted/20 p-4 text-sm">
                {task.result}
              </div>
            </div>
          )}

          {/* 错误信息 */}
          {task.error && (
            <div>
              <h3 className="mb-2 text-sm font-semibold text-destructive">
                {t.common.error}
              </h3>
              <div className="whitespace-pre-wrap rounded-lg border border-destructive/50 bg-destructive/5 p-4 text-sm text-destructive">
                {task.error}
              </div>
            </div>
          )}

          {/* 技能标签 */}
          {task.skills && task.skills.length > 0 && (
            <div>
              <h3 className="mb-2 text-sm font-semibold">Skills</h3>
              <div className="flex flex-wrap gap-2">
                {task.skills.map((skill, index) => (
                  <span
                    key={index}
                    className="rounded-md bg-primary/10 px-2 py-1 text-xs font-medium text-primary"
                  >
                    {skill}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
