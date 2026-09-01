/**
 * Floating REC recorder overlay (P1 of the screen-recorder feature).
 *
 * Replaces the old window.confirm() start/stop flow with a proper floating
 * recorder: name the task -> 3·2·1 countdown -> a live recording pill (elapsed
 * timer + step counter) -> stop, which forges a reusable skill from the recorded
 * trajectory (existing teach-repeat / SkillForge backend — no backend change in
 * this phase). The richer replay-timeline + skill-card review is a later phase;
 * here the done state shows the forge result and points at the skill library.
 *
 * Chinese-only copy, matching the existing REC button (which is not i18n'd).
 */

import {
  CircleDotIcon,
  Loader2Icon,
  SparklesIcon,
  SquareIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  appendRecordingEvents,
  getRecordingStatus,
  startRecording,
  stopRecording,
} from "@/core/teach-repeat/api";
import type { StopRecordingResponse } from "@/core/teach-repeat/types";
import type { RecordingEvent } from "@/core/teach-repeat/types";
import {
  buildSemanticRecordingEvent,
  recordingEventKey,
} from "@/core/teach-repeat/semantic-events";
import { useI18n } from "@/core/i18n/hooks";
import { swallow } from "@/core/utils/log";
import { cn } from "@/lib/utils";

type Phase = "idle" | "countdown" | "recording" | "stopping" | "done";

const COUNTDOWN_FROM = 3;
const COUNTDOWN_STEP_MS = 800;

export interface RecRecorderOverlayProps {
  open: boolean;
  threadId: string;
  defaultName: string;
  /** Jump straight to the recording pill when opened mid-recording. */
  initiallyRecording?: boolean;
  onClose: () => void;
  /** Fires when recording starts (true) / stops (false) so the REC chip can
   * reflect state. */
  onRecordingChange?: (recording: boolean) => void;
  /** Opens the durable workflow library after recording or from the idle state. */
  onOpenLibrary?: () => void;
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function RecRecorderOverlay({
  open,
  threadId,
  defaultName,
  initiallyRecording = false,
  onClose,
  onRecordingChange,
  onOpenLibrary,
}: RecRecorderOverlayProps) {
  const { t } = useI18n();
  const [phase, setPhase] = useState<Phase>("idle");
  const [name, setName] = useState(defaultName);
  const [countdown, setCountdown] = useState(COUNTDOWN_FROM);
  const [elapsed, setElapsed] = useState(0);
  const [stepCount, setStepCount] = useState(0);
  const [result, setResult] = useState<StopRecordingResponse | null>(null);
  const recStartRef = useRef<number | null>(null);
  const startingRef = useRef(false);
  const eventQueueRef = useRef<RecordingEvent[]>([]);
  const canRecord = !!threadId && threadId !== "new";

  // Sync phase to open/initiallyRecording when the overlay is (re)opened.
  useEffect(() => {
    if (!open) return;
    setPhase(initiallyRecording ? "recording" : "idle");
    setName(defaultName);
    setResult(null);
    setCountdown(COUNTDOWN_FROM);
    setElapsed(0);
    if (initiallyRecording) recStartRef.current = Date.now();
  }, [open, initiallyRecording, defaultName]);

  const beginRecording = useCallback(async () => {
    if (startingRef.current) return;
    startingRef.current = true;
    try {
      await startRecording({
        thread_id: threadId,
        name: name.trim() || "对话回放学习",
        description: "用户通过悬浮录制器开启的 REC 录制。",
        provider: "hybrid",
      });
      recStartRef.current = Date.now();
      setElapsed(0);
      setStepCount(0);
      setPhase("recording");
      onRecordingChange?.(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "REC 启动失败");
      setPhase("idle");
    } finally {
      startingRef.current = false;
    }
  }, [threadId, name, onRecordingChange]);

  const flushEvents = useCallback(async () => {
    if (!canRecord || eventQueueRef.current.length === 0) return;
    const batch = eventQueueRef.current.splice(0, 100);
    try {
      const response = await appendRecordingEvents(threadId, batch);
      setStepCount(response.step_count);
    } catch (error) {
      eventQueueRef.current.unshift(...batch);
      swallow(error, "rec-overlay-events");
    }
  }, [canRecord, threadId]);

  // Countdown driver.
  useEffect(() => {
    if (phase !== "countdown") return;
    if (countdown <= 0) {
      void beginRecording();
      return;
    }
    const t = window.setTimeout(
      () => setCountdown((c) => c - 1),
      COUNTDOWN_STEP_MS,
    );
    return () => window.clearTimeout(t);
  }, [phase, countdown, beginRecording]);

  // Elapsed timer while recording.
  useEffect(() => {
    if (phase !== "recording") return;
    const t = window.setInterval(() => {
      setElapsed(Date.now() - (recStartRef.current ?? Date.now()));
    }, 500);
    return () => window.clearInterval(t);
  }, [phase]);

  // Poll live step count while recording.
  useEffect(() => {
    if (phase !== "recording" || !canRecord) return;
    const poll = async () => {
      try {
        const s = await getRecordingStatus(threadId);
        setStepCount(s.step_count);
      } catch (error) {
        swallow(error, "rec-overlay-status");
      }
    };
    void poll();
    const t = window.setInterval(() => void poll(), 3000);
    return () => window.clearInterval(t);
  }, [phase, threadId, canRecord]);

  // Capture semantic first-party interactions while REC is active. Text typed
  // into password/OTP/payment fields is redacted before it enters the queue.
  useEffect(() => {
    if (phase !== "recording" || !canRecord) return;
    const capture = (event: Event) => {
      const recorded = buildSemanticRecordingEvent(event);
      if (!recorded) return;
      if (recorded.kind === "input") {
        const key = recordingEventKey(recorded);
        const existing = eventQueueRef.current.findIndex(
          (item) => recordingEventKey(item) === key,
        );
        if (existing >= 0) {
          eventQueueRef.current[existing] = recorded;
          return;
        }
      }
      eventQueueRef.current.push(recorded);
      if (eventQueueRef.current.length >= 20) void flushEvents();
    };
    const eventTypes = [
      "click",
      "focusin",
      "input",
      "change",
      "keydown",
    ] as const;
    eventTypes.forEach((type) =>
      document.addEventListener(type, capture, true),
    );
    const timer = window.setInterval(() => void flushEvents(), 1500);
    return () => {
      eventTypes.forEach((type) =>
        document.removeEventListener(type, capture, true),
      );
      window.clearInterval(timer);
      void flushEvents();
    };
  }, [canRecord, flushEvents, phase]);

  const handleStop = useCallback(async () => {
    setPhase("stopping");
    try {
      await flushEvents();
      const res = await stopRecording({ thread_id: threadId, use_llm: true });
      setResult(res);
      setPhase("done");
      onRecordingChange?.(false);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.recorder.stopFailed,
      );
      setPhase("recording");
    }
  }, [flushEvents, threadId, onRecordingChange, t]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-label={t.recorder.title}
      data-recorder-private="true"
      className="fixed bottom-5 right-5 z-[120] w-[260px] rounded-lg border border-border-default bg-background/95 p-4 shadow-2xl ring-1 ring-border-subtle backdrop-blur"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
          <CircleDotIcon
            className={cn(
              "size-3.5",
              phase === "recording" && "animate-pulse text-destructive",
            )}
          />
          {t.recorder.title}
        </span>
        {(phase === "idle" || phase === "done") && (
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground transition-colors hover:text-foreground"
            aria-label={t.recorder.close}
          >
            <XIcon className="size-4" />
          </button>
        )}
      </div>

      {phase === "idle" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">
              {t.recorder.taskNameLabel}
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t.recorder.taskNamePlaceholder}
              className="w-full rounded-lg border border-border bg-transparent px-2.5 py-1.5 text-xs text-foreground outline-none focus:border-border-strong"
            />
          </div>
          <button
            type="button"
            disabled={!canRecord}
            onClick={() => {
              setCountdown(COUNTDOWN_FROM);
              setPhase("countdown");
            }}
            className={cn(
              "flex w-full items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors",
              "bg-destructive/12 text-destructive hover:bg-destructive/18 dark:text-destructive",
              !canRecord && "opacity-50",
            )}
          >
            <CircleDotIcon className="size-3.5" />
            开始录制
          </button>
          {onOpenLibrary ? (
            <button
              type="button"
              onClick={onOpenLibrary}
              className="w-full rounded-lg border border-border px-3 py-2 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              查看已保存的自动化
            </button>
          ) : null}
          <p className="text-xs leading-tight text-muted-foreground">
            录制本轮操作轨迹,停止后自动提炼成可复用、可回放的技能;敏感操作会被隔离待审。
          </p>
        </div>
      )}

      {phase === "countdown" && (
        <div className="flex flex-col items-center justify-center gap-2 py-4">
          <div className="flex size-16 items-center justify-center rounded-full border-2 border-destructive/60 text-3xl font-semibold text-destructive">
            {countdown > 0 ? countdown : "·"}
          </div>
          <span className="text-xs text-muted-foreground">准备录制…</span>
        </div>
      )}

      {phase === "recording" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2.5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2">
            <span className="size-2.5 animate-pulse rounded-full bg-destructive" />
            <span className="font-mono text-sm font-semibold text-foreground">
              {formatElapsed(elapsed)}
            </span>
            <span className="text-xs text-muted-foreground">
              · {stepCount} 步
            </span>
          </div>
          <button
            type="button"
            onClick={() => void handleStop()}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-destructive/12 px-3 py-2 text-xs font-medium text-destructive transition-colors hover:bg-destructive/18 dark:text-destructive"
          >
            <SquareIcon className="size-3.5" />
            停止并提炼技能
          </button>
        </div>
      )}

      {phase === "stopping" && (
        <div className="flex flex-col items-center justify-center gap-2 py-5">
          <Loader2Icon className="size-5 animate-spin text-muted-foreground" />
          <span className="text-xs text-muted-foreground">
            正在分析录制、提炼技能…
          </span>
        </div>
      )}

      {phase === "done" && (
        <div className="space-y-3">
          <DoneSummary result={result} />
          <div className="grid grid-cols-2 gap-2">
            {onOpenLibrary ? (
              <button
                type="button"
                onClick={onOpenLibrary}
                className="rounded-lg border border-border-strong bg-transparent px-3 py-2 text-xs font-medium text-foreground transition-colors hover:bg-muted"
              >
                打开自动化库
              </button>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-border-strong bg-transparent px-3 py-2 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              完成
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function DoneSummary({ result }: { result: StopRecordingResponse | null }) {
  const forged = result?.forged ?? [];
  const status = result?.status;

  if (status === "promoted" && forged.length) {
    return (
      <div className="rounded-lg border border-success/30 bg-success/5 p-3">
        <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-success">
          <SparklesIcon className="size-3.5" />
          已学会技能
        </div>
        <div className="text-xs text-foreground">{forged.join("、")}</div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          可在技能库 /record 面板里回放与参数化复用。
        </p>
      </div>
    );
  }
  if (status === "quarantined") {
    return (
      <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-warning">
        已生成技能候选,含敏感操作,已隔离待人工审批。
      </div>
    );
  }
  if (status === "no_successful_trajectory") {
    return (
      <div className="rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
        本轮暂无可提炼的成功操作轨迹,换个更明确的任务再录一次。
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
      录制完成{result?.name ? `：${result.name}` : ""}。
    </div>
  );
}
