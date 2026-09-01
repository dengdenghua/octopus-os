"use client";

import { useCallback, useEffect, useRef } from "react";

import { AlertCircleIcon, Loader2Icon, RadioIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { LiveToolTimeline } from "@/components/workspace/live-tool-timeline";
import {
  useSubAgentBusStream,
  type SubAgentBusStatus,
} from "./use-subagent-bus-stream";

/**
 * Independent sub-agent event stream panel.
 *
 * Subscribes to the typed backend event bus for one coordination root and
 * renders the full run (spawn → tools → conclude/fail) through the same
 * `LiveToolTimeline` the workbench already uses — but fed purely from the
 * bus, so a sub-agent's stream survives independently of the parent
 * conversation's own event feed. This is the UI half of the
 * "sub-agent = independent thread" model: the main conversation only keeps a
 * progress card, while this panel shows the complete child stream.
 */
export function SubAgentBusStreamPanel({
  rootThreadId,
  className,
  showAll = false,
  dispatchFailed = false,
}: {
  rootThreadId?: string | null;
  className?: string;
  /** When true, show every bus event; otherwise show the live + recent
   * window the main workbench timeline defaults to. */
  showAll?: boolean;
  dispatchFailed?: boolean;
}) {
  const { events, status } = useSubAgentBusStream(rootThreadId);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);

  // Follow the live stream, but only when the user is already near the
  // bottom — never yank the view away from something they're reading.
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [events.length]);

  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <PanelHeader status={status} eventCount={events.length} />
      {events.length === 0 ? (
        <EmptyState status={status} dispatchFailed={dispatchFailed} />
      ) : (
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="min-h-0 flex-1 overflow-y-auto px-3 pb-4"
        >
          <LiveToolTimeline events={events} groupByAgent showAll={showAll} />
        </div>
      )}
    </div>
  );
}

const STATUS_BADGE: Record<SubAgentBusStatus, string> = {
  idle: "bg-muted text-muted-foreground",
  connecting: "bg-amber-500/15 text-amber-600",
  live: "bg-emerald-500/15 text-emerald-600",
  error: "bg-red-500/15 text-red-600",
};

function PanelHeader({
  status,
  eventCount,
}: {
  status: SubAgentBusStatus;
  eventCount: number;
}) {
  const { t } = useI18n();
  const statusText =
    status === "live"
      ? t.agentWorkbenchPanel.subagentBusStreamLive
      : status === "error"
        ? t.agentWorkbenchPanel.subagentBusStreamError
        : status === "connecting"
          ? t.agentWorkbenchPanel.subagentBusStreamConnecting
          : status;

  return (
    <div className="flex items-center gap-2 border-b border-border-default px-3 py-2">
      <RadioIcon className="size-4 text-muted-foreground" />
      <span className="text-sm font-medium">
        {t.agentWorkbenchPanel.subagentBusStreamTitle}
      </span>
      <span
        className={cn(
          "ml-auto flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
          STATUS_BADGE[status],
        )}
      >
        {status === "connecting" || status === "idle" ? (
          <Loader2Icon className="size-3 animate-spin" />
        ) : null}
        {statusText}
      </span>
      {eventCount > 0 ? (
        <span className="text-xs text-muted-foreground">
          {t.agentWorkbenchPanel.subagentBusStreamEvents(eventCount)}
        </span>
      ) : null}
    </div>
  );
}

function EmptyState({
  status,
  dispatchFailed,
}: {
  status: SubAgentBusStatus;
  dispatchFailed: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center px-5 text-sm text-muted-foreground">
      {status === "error" || dispatchFailed ? (
        <span className="flex items-center gap-2">
          <AlertCircleIcon className="size-4 text-red-500" />
          {dispatchFailed
            ? t.agentWorkbenchPanel.subagentDispatchFailed
            : t.agentWorkbenchPanel.subagentBusStreamError}
        </span>
      ) : (
        t.agentWorkbenchPanel.subagentBusStreamEmpty
      )}
    </div>
  );
}
