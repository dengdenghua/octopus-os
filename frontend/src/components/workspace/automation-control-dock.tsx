import {
  ChevronDownIcon,
  ChevronUpIcon,
  CirclePauseIcon,
  CirclePlayIcon,
  HandIcon,
  HistoryIcon,
  Loader2Icon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { getRelayStatus, type RelayStatus } from "@/core/browser/api";
import {
  AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
  AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME,
  AUTOMATION_CAPSULE_SURFACE_CLASS_NAME,
} from "@/components/ui/automation-capsule";
import type { AutomationTarget } from "@/core/computer/api";
import {
  getControlSessionReplay,
  setControlSessionState,
  type ControlSessionReplay,
} from "@/core/control-session";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

type AutomationControlDockProps = {
  threadId: string;
  target: AutomationTarget;
  className?: string;
};

function timeLabel(at?: number): string {
  if (!at) return "";
  const timestamp = at > 10_000_000_000 ? at : at * 1000;
  return new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function AutomationControlDock({
  threadId,
  target,
  className,
}: AutomationControlDockProps) {
  const { t } = useI18n();
  const sessionId = `thread:${threadId || "new"}`;
  const [relay, setRelay] = useState<RelayStatus | null>(null);
  const [replay, setReplay] = useState<ControlSessionReplay | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [changingState, setChangingState] = useState(false);

  const refresh = useCallback(async () => {
    const [relayResult, replayResult] = await Promise.allSettled([
      getRelayStatus(),
      getControlSessionReplay(sessionId),
    ]);
    if (relayResult.status === "fulfilled") setRelay(relayResult.value);
    if (replayResult.status === "fulfilled") setReplay(replayResult.value);
  }, [sessionId]);

  useEffect(() => {
    setReplay(null);
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const sessionStatus = replay?.session.status || "idle";
  const paused = replay?.session.paused || sessionStatus === "paused";
  const active =
    sessionStatus === "running" || sessionStatus === "awaiting_confirmation";
  const connectionLabel =
    target.kind === "browser_tab"
      ? relay?.connection_state === "reconnecting"
        ? t.chatInputBox.automationReconnecting
        : relay?.connected
          ? t.chatInputBox.automationOnline
          : t.chatInputBox.automationOffline
      : t.chatInputBox.automationDesktop;
  const stateLabel = paused
    ? t.chatInputBox.automationPaused
    : active
      ? t.chatInputBox.automationRunning
      : t.chatInputBox.automationIdle;
  const timeline = useMemo(
    () => (replay?.timeline?.items || []).slice(-5).reverse(),
    [replay?.timeline?.items],
  );
  const latest = timeline[0];

  const changeState = useCallback(
    async (action: "pause" | "resume" | "takeover") => {
      setChangingState(true);
      try {
        await setControlSessionState(
          sessionId,
          action,
          action === "takeover" ? "user takeover" : `user ${action}`,
        );
        await refresh();
      } catch (error) {
        toast.error(
          error instanceof Error
            ? error.message
            : t.chatInputBox.automationControlFailed,
        );
      } finally {
        setChangingState(false);
      }
    },
    [refresh, sessionId, t.chatInputBox.automationControlFailed],
  );

  return (
    <div
      data-testid="automation-control-dock"
      className={cn(
        AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME,
        AUTOMATION_CAPSULE_SURFACE_CLASS_NAME,
        "mb-2",
        className,
      )}
    >
      <div
        className={cn(
          AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
          "flex min-h-10 items-center gap-2 px-2.5 py-1.5",
        )}
      >
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            target.kind === "browser_tab" && !relay?.connected
              ? "bg-destructive"
              : paused
                ? "bg-warning"
                : active
                  ? "animate-pulse bg-success"
                  : "bg-muted-foreground/45",
          )}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium text-foreground">
            {target.title}
          </div>
          <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>{connectionLabel}</span>
            <span aria-hidden="true">·</span>
            <span>{stateLabel}</span>
            {latest ? (
              <>
                <span aria-hidden="true">·</span>
                <span className="truncate">{latest.summary}</span>
              </>
            ) : null}
          </div>
        </div>
        {replay ? (
          <>
            <button
              type="button"
              disabled={changingState}
              onClick={() => void changeState(paused ? "resume" : "pause")}
              className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-background/80 hover:text-foreground disabled:opacity-50"
            >
              {changingState ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : paused ? (
                <CirclePlayIcon className="size-3.5" />
              ) : (
                <CirclePauseIcon className="size-3.5" />
              )}
              {paused
                ? t.chatInputBox.automationResume
                : t.chatInputBox.automationPause}
            </button>
            {!paused ? (
              <button
                type="button"
                disabled={changingState}
                onClick={() => void changeState("takeover")}
                className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-background/80 hover:text-foreground disabled:opacity-50"
              >
                <HandIcon className="size-3.5" />
                {t.chatInputBox.automationTakeover}
              </button>
            ) : null}
          </>
        ) : null}
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-background/80 hover:text-foreground"
          title={t.chatInputBox.automationEvidence}
          aria-label={t.chatInputBox.automationEvidence}
          aria-expanded={expanded}
        >
          {expanded ? (
            <ChevronUpIcon className="size-3.5" />
          ) : (
            <ChevronDownIcon className="size-3.5" />
          )}
        </button>
      </div>
      {expanded ? (
        <div
          className={cn(
            AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
            "border-t border-border/60 px-3 py-2",
          )}
        >
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
            <HistoryIcon className="size-3.5" />
            {t.chatInputBox.automationEvidence}
          </div>
          {timeline.length ? (
            <div className="space-y-1">
              {timeline.map((item) => (
                <div
                  key={item.cursor || item.id}
                  className="flex items-start gap-2 text-[11px] text-muted-foreground"
                >
                  <span className="w-10 shrink-0 tabular-nums">
                    {timeLabel(item.at)}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {item.summary}
                  </span>
                  <span
                    className={
                      item.status === "failed" ? "text-destructive" : ""
                    }
                  >
                    {item.status || item.phase}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[11px] text-muted-foreground">
              {t.chatInputBox.automationNoEvidence}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
