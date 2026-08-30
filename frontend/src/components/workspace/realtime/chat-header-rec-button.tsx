/**
 * ChatHeaderRecButton — extracted from `workspace/realtime/[thread_id]/page.tsx`
 * (P3 decomposition). Behavior-preserving move.
 */
import { useCallback, useEffect, useState } from "react";
import { CircleDotIcon } from "lucide-react";

import { getRecordingStatus } from "@/core/teach-repeat/api";
import type { RecordingStatus } from "@/core/teach-repeat/types";
import { useI18n } from "@/core/i18n/hooks";
import { swallow } from "@/core/utils/log";
import { cn } from "@/lib/utils";

export function ChatHeaderRecButton({
  threadId,
  onOpen,
  isRecording,
}: {
  threadId: string;
  onOpen: () => void;
  isRecording: boolean;
}) {
  const { t } = useI18n();
  const [status, setStatus] = useState<RecordingStatus>({
    recording: false,
    step_count: 0,
    name: "",
  });

  const refresh = useCallback(async () => {
    if (!threadId || threadId === "new") return;
    try {
      setStatus(await getRecordingStatus(threadId));
    } catch (error) {
      swallow(error, "teach-repeat-status");
    }
  }, [threadId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The floating RecRecorderOverlay owns start/stop now; this chip only opens it
  // and mirrors live state. ``isRecording`` (from the overlay) flips instantly;
  // the poll keeps the step counter fresh and recovers state on reload.
  const recording = isRecording || status.recording;
  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [refresh, recording]);

  const recordingTitle = recording
    ? t.realtime.recording.recording(status.step_count)
    : t.realtime.recording.idle;

  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={!threadId || threadId === "new"}
      title={recordingTitle}
      aria-label={recordingTitle}
      className={cn(
        "inline-flex h-[42px] shrink-0 items-center gap-1.5 rounded-lg border px-3 text-xs font-semibold shadow-none transition-all duration-base sm:h-8 sm:px-2.5",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        recording
          ? "border-destructive/25 bg-destructive/10 text-destructive hover:bg-destructive/16 dark:text-destructive"
          : "border-transparent bg-transparent text-muted-foreground hover:border-border-default hover:bg-muted/55 hover:text-foreground",
      )}
    >
      <CircleDotIcon className={cn("size-3.5", recording && "animate-pulse")} />
      <span>REC</span>
      {recording && status.step_count > 0 && (
        <span className="font-mono text-xs opacity-70">
          {status.step_count}
        </span>
      )}
    </button>
  );
}
