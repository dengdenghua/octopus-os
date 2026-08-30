import {
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  PauseCircleIcon,
  XCircleIcon,
} from "lucide-react";
import { Suspense, lazy, useCallback, useMemo, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Button } from "@/components/ui/button";
import { DotProgress } from "@/components/workspace/swarm/dot-progress";
import { useI18n } from "@/core/i18n/hooks";
import { hasToolCalls } from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { useStreamdownPluginsWithWordAnimation } from "@/core/streamdown";
import { useSubtask } from "@/core/tasks/context";
import {
  isSubtaskActive,
  SUBTASK_STATUS_LABELS,
  type SubtaskStatus,
} from "@/core/tasks/types";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";
import {
  agentRunHue,
  agentRunIconClass,
  agentRunStatusLightPulseClass,
} from "../agent-run-status";
import { subtaskRunState } from "./subtask-status-ui";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";
import { useThreadStreaming } from "./context";

import { MarkdownContent } from "./markdown-content";

const LazyStreamdown = lazy(
  () => import("@/components/ai-elements/streamdown-host"),
);

function getStatusIcon(status: SubtaskStatus) {
  const runState = subtaskRunState(status);
  if (status === "completed")
    return (
      <CheckCircleIcon className={cn("size-3", agentRunIconClass(runState))} />
    );
  if (status === "failed")
    return <XCircleIcon className="size-3 text-destructive" />;
  if (status === "cancelled")
    return <XCircleIcon className="size-3 text-warning" />;
  if (status === "timed_out")
    return <XCircleIcon className="size-3 text-destructive" />;
  if (status === "pending")
    return <PauseCircleIcon className="size-3 text-warning" />;
  if (isSubtaskActive(status))
    return (
      <Loader2Icon className="size-3 animate-spin text-success" />
    );
  return <ClipboardListIcon className="size-3" />;
}

function getStatusLabel(
  status: SubtaskStatus,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const label = t.subagents[status as keyof typeof t.subagents];
  return (
    (typeof label === "string" ? label : undefined) ??
    SUBTASK_STATUS_LABELS[status] ??
    status
  );
}

export function SubtaskCard({
  className,
  taskId,
  isLoading,
}: {
  className?: string;
  taskId: string;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(false);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const streamdownPluginsWithWordAnimation =
    useStreamdownPluginsWithWordAnimation();
  const task = useSubtask(taskId);
  const { subgraphStreams } = useThreadStreaming();
  const subgraphStream = task ? subgraphStreams[task.id] : undefined;
  const icon = useMemo(
    () => (task ? getStatusIcon(task.status) : null),
    [task],
  );
  const isActive = task ? isSubtaskActive(task.status) : false;
  const runState = task ? subtaskRunState(task.status) : "pending";

  // Hoisted above the ``if (!task) return`` guard so the hook fires
  // every render. When ``task`` flipped from null → loaded the old
  // placement caused "Rendered more hooks than during the previous
  // render." useCallback still guards against missing task inside.
  const handleHeaderClick = useCallback(() => {
    setCollapsed((c) => !c);
  }, []);

  if (!task) {
    return (
      <div
        className={cn(
          "rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground",
          className,
        )}
      >
        {taskId}
      </div>
    );
  }

  return (
    <ChainOfThought
      className={cn("relative w-full gap-2 rounded-lg border py-0", className)}
      open={!collapsed}
    >
      <div className="bg-background/95 flex w-full flex-col rounded-lg">
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={handleHeaderClick}
          >
            <div className="flex w-full items-center justify-between">
              <div className="flex items-center gap-2">
                {task.avatarEmoji && (
                  <span
                    className="flex size-6 shrink-0 items-center justify-center rounded-lg text-xs"
                    style={
                      task.hue != null
                        ? { background: `hsl(${task.hue} 70% 92%)` }
                        : undefined
                    }
                  >
                    {task.avatarEmoji}
                  </span>
                )}
                <ChainOfThoughtStep
                  className="font-normal"
                  label={task.name ?? task.description}
                  icon={<ClipboardListIcon />}
                ></ChainOfThoughtStep>
              </div>
              <div className="flex items-center gap-1.5">
                {collapsed && (
                  <div
                    className={cn(
                      "text-muted-foreground flex items-center gap-1 text-xs font-normal",
                      task.status === "failed"
                        ? "text-destructive opacity-60"
                        : "",
                    )}
                  >
                    {icon}
                    <FlipDisplay
                      className="max-w-[420px] truncate pb-1"
                      uniqueKey={task.latestMessage?.id ?? ""}
                    >
                      {isActive &&
                      task.latestMessage &&
                      hasToolCalls(task.latestMessage)
                        ? explainLastToolCall(task.latestMessage, t)
                        : getStatusLabel(task.status, t)}
                    </FlipDisplay>
                  </div>
                )}
                {isActive && task.hue != null && (
                  <DotProgress
                    progress={task.progress}
                    hue={agentRunHue(runState)}
                    cols={10}
                    rows={2}
                    className={cn(agentRunStatusLightPulseClass(runState))}
                  />
                )}
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    !collapsed ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        <ChainOfThoughtContent className="px-3 pb-3">
          {task.prompt && (
            <ChainOfThoughtStep
              label={
                <Suspense
                  fallback={
                    <div className="whitespace-pre-wrap break-words">
                      {task.prompt}
                    </div>
                  }
                >
                  <LazyStreamdown
                    {...streamdownPluginsWithWordAnimation}
                    components={{ a: CitationLink }}
                  >
                    {task.prompt}
                  </LazyStreamdown>
                </Suspense>
              }
            ></ChainOfThoughtStep>
          )}
          {task.messages && task.messages.length > 0 && (
            <>
              {task.messages.map((msg, idx) => {
                if (hasToolCalls(msg)) {
                  return (
                    <ChainOfThoughtStep
                      key={msg.id ?? `step-${idx}`}
                      label={explainLastToolCall(msg, t)}
                      icon={
                        idx === task.messages!.length - 1 && isActive ? (
                          <Loader2Icon className="size-4 animate-spin text-success" />
                        ) : (
                          <CheckCircleIcon className="size-4 text-success" />
                        )
                      }
                    />
                  );
                }
                const content =
                  typeof msg.content === "string" ? msg.content : "";
                if (content.trim()) {
                  return (
                    <ChainOfThoughtStep
                      key={msg.id ?? `thought-${idx}`}
                      label={
                        <MarkdownContent
                          content={
                            content.length > 400
                              ? content.slice(0, 400) + "…"
                              : content
                          }
                          isLoading={false}
                          rehypePlugins={rehypePlugins}
                        />
                      }
                    />
                  );
                }
                return null;
              })}
            </>
          )}
          {(!task.messages || task.messages.length === 0) &&
            isActive &&
            task.latestMessage &&
            hasToolCalls(task.latestMessage) && (
              <ChainOfThoughtStep
                label={getStatusLabel(task.status, t)}
                icon={<Loader2Icon className="size-4 animate-spin" />}
              >
                {explainLastToolCall(task.latestMessage, t)}
              </ChainOfThoughtStep>
            )}
          {task.status === "completed" && (
            <>
              <ChainOfThoughtStep
                label={getStatusLabel(task.status, t)}
                icon={<CheckCircleIcon className="size-4" />}
              ></ChainOfThoughtStep>
              <ChainOfThoughtStep
                label={
                  task.result ? (
                    <MarkdownContent
                      content={task.result}
                      isLoading={false}
                      rehypePlugins={rehypePlugins}
                    />
                  ) : null
                }
              ></ChainOfThoughtStep>
            </>
          )}
          {task.status === "failed" && (
            <ChainOfThoughtStep
              label={<div className="text-destructive">{task.error}</div>}
              icon={<XCircleIcon className="size-4 text-destructive" />}
            ></ChainOfThoughtStep>
          )}
          {task.status === "cancelled" && (
            <ChainOfThoughtStep
              label={
                <div className="text-warning">
                  {task.error ?? t.subtask.cancelled}
                </div>
              }
              icon={<XCircleIcon className="size-4 text-warning" />}
            ></ChainOfThoughtStep>
          )}
          {task.status === "timed_out" && (
            <ChainOfThoughtStep
              label={
                <div className="text-chart-7 dark:text-chart-7">
                  {task.error ?? t.subtask.timedOut}
                </div>
              }
              icon={<XCircleIcon className="size-4 text-chart-7" />}
            ></ChainOfThoughtStep>
          )}
          {isActive &&
            subgraphStream &&
            (() => {
              const raw = subgraphStream.content;
              const text =
                typeof raw === "string"
                  ? raw
                  : Array.isArray(raw)
                    ? raw
                        .filter(
                          (
                            b,
                          ): b is Extract<
                            (typeof raw)[number],
                            { type: "text" }
                          > =>
                            typeof b === "object" &&
                            b !== null &&
                            "type" in b &&
                            b.type === "text",
                        )
                        .map((b) => b.text)
                        .join("")
                    : "";
              if (!text.trim()) return null;
              return (
                <ChainOfThoughtStep
                  label={
                    <MarkdownContent
                      content={
                        text.length > 600 ? text.slice(0, 600) + "…" : text
                      }
                      isLoading={isActive}
                      rehypePlugins={rehypePlugins}
                    />
                  }
                  icon={<Loader2Icon className="size-4 animate-spin" />}
                />
              );
            })()}
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
