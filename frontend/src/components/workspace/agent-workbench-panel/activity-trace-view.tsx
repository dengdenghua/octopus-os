import { memo } from "react";

import { ListChecksIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { activateTimelineItem } from "@/core/threads/timeline-linkage";
import type { WorkBlock } from "../work-blocks";
import {
  statusText,
  workBlockActionLabel,
  workBlockTarget,
  workBlockTitle,
} from "../work-blocks";
import { blockIcon, compactDetail } from "../agent-workbench-utils";
import { StatusGlyph } from "../agent-workbench-pages";
import { workBlockLabelsFromI18n } from "./helpers";

function ActivityTraceViewImpl({
  blocks,
  currentBlockId,
  emptyText,
  onSelectBlock,
  subtitle,
  title,
}: {
  blocks: WorkBlock[];
  currentBlockId: string | null;
  emptyText: string;
  onSelectBlock: (blockId: string) => void;
  subtitle: string;
  title: string;
}) {
  const { t } = useI18n();
  const workBlockLabels = workBlockLabelsFromI18n(t);
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-background/35">
      <div className="mx-auto w-full max-w-2xl px-5 py-4">
        <div className="mb-3 flex min-w-0 items-center gap-2 border-b border-border-subtle pb-3">
          <ListChecksIcon className="size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs font-semibold text-foreground">
              {title}
            </div>
            <div className="mt-0.5 truncate text-xs text-muted-foreground">
              {subtitle}
            </div>
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">
            {t.agentWorkbench.stepCount(blocks.length)}
          </span>
        </div>

        {blocks.length === 0 ? (
          <div className="flex min-h-40 items-center justify-center px-4 text-center text-sm text-muted-foreground">
            {emptyText}
          </div>
        ) : (
          <div className="divide-y divide-border/30">
            {blocks.map((block, index) => {
              const Icon = blockIcon(block.kind);
              const active = currentBlockId === block.id;
              const actionLabel = workBlockActionLabel(block, workBlockLabels);
              const titleText = workBlockTitle(block, workBlockLabels);
              const target =
                workBlockTarget(block, workBlockLabels) ||
                (titleText !== actionLabel ? titleText : "");
              const detail =
                block.subtitle && block.subtitle !== target
                  ? block.subtitle
                  : "";
              const showStatusText =
                block.status === "running" ||
                block.status === "waiting_approval" ||
                block.status === "error";
              return (
                <button
                  key={block.id}
                  type="button"
                  onClick={() => {
                    onSelectBlock(block.id);
                    // 侧边栏 → 对话区联动：激活共享 id，对话区滚动定位并短暂高亮
                    activateTimelineItem(block.event.id || block.id, "sidebar");
                  }}
                  className={cn(
                    "flex w-full min-w-0 items-start gap-2 border-l-2 px-1 py-2 text-left transition-colors",
                    active
                      ? "border-l-primary bg-muted/25"
                      : "border-l-transparent hover:bg-muted/20",
                  )}
                  data-timeline-item-id={block.event.id || block.id}
                  data-timeline-lane="sidebar"
                >
                  <span className="mt-0.5 w-5 shrink-0 font-mono text-xs text-muted-foreground">
                    {index + 1}
                  </span>
                  <StatusGlyph
                    status={block.status}
                    className="mt-0.5 size-3.5"
                  />
                  <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {actionLabel}
                      </span>
                      {target ? (
                        <span className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground">
                          {target}
                        </span>
                      ) : null}
                    </div>
                    {detail ? (
                      <div className="mt-1 line-clamp-2 text-xs leading-4 text-muted-foreground">
                        {compactDetail(detail, 150)}
                      </div>
                    ) : null}
                  </div>
                  {showStatusText ? (
                    <span className="shrink-0 pt-0.5 text-xs text-muted-foreground/70">
                      {statusText(block.status, {
                        running: t.messageGrouping.liveProcessRunning,
                        waiting_approval: t.messageGrouping.liveProcessWaiting,
                        warning: t.messageGrouping.liveProcessDone,
                        error: t.messageGrouping.liveProcessError,
                        done: t.messageGrouping.liveProcessDone,
                      })}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export const ActivityTraceView = memo(ActivityTraceViewImpl);
