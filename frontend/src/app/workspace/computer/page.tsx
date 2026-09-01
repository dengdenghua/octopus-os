import {
  type MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ActivityIcon,
  CheckCircle2Icon,
  ClockIcon,
  EyeIcon,
  ListChecksIcon,
  KeyboardIcon,
  MonitorCheckIcon,
  ScanSearchIcon,
  MousePointerClickIcon,
  PlayIcon,
  RadioIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  SquareIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import {
  askVisionModelForComputerActions,
  captureComputerScreen,
  executeComputerAction,
  getComputerStatus,
  groundComputerActions,
  planComputerActions,
  previewComputerAction,
  releaseComputerLease,
  type ComputerActionPlan,
  type ComputerAction,
  type ComputerCapability,
  type ComputerExecuteResult,
  type ComputerLease,
  type ComputerLeaseOwner,
  type ComputerMatchedControl,
  type ComputerPreview,
  type ComputerScreenshot,
  type ComputerStatus,
} from "@/core/computer/api";
import {
  runControlSessionAction,
  type ControlEvidence,
  type ControlIndicatorMode,
  type ControlSessionOptions,
} from "@/core/control-session";
import { swallow } from "@/core/utils/log";
import { loadModels } from "@/core/models/api";
import type { Model } from "@/core/models/types";
import { useI18n } from "@/core/i18n/hooks";
import {
  getPcScreenStats,
  startPcScreenCapture,
  stopPcScreenCapture,
  type PcScreenStats,
} from "@/core/tentacle/api";
import { usePcScreenStream } from "@/core/tentacle/use-pc-screen-stream";
import { cn } from "@/lib/utils";

type ActionKind = "click" | "move" | "type" | "key" | "wait";
type ObservationMode = "snapshot" | "live";
type LogItem = {
  id: string;
  title: string;
  detail: string;
  tone: "ok" | "warn" | "error";
};
type ScreenPoint = { x: number; y: number };
type VisualTarget = ScreenPoint & {
  label: string;
  tone: "preview" | "candidate" | "selected";
};
type ScreenshotImageBox = {
  left: number;
  top: number;
  width: number;
  height: number;
  naturalWidth: number;
  naturalHeight: number;
};

export default function ComputerAutomationPage() {
  const { t } = useI18n();
  const tc = useCallback(
    (source: string) => t.workspaceComputer[source] ?? source,
    [t.workspaceComputer],
  );
  const [status, setStatus] = useState<ComputerStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [screenshot, setScreenshot] = useState<ComputerScreenshot | null>(null);
  const [observationMode, setObservationMode] =
    useState<ObservationMode>("snapshot");
  const [pcScreenStats, setPcScreenStats] = useState<PcScreenStats | null>(
    null,
  );
  const [pcScreenError, setPcScreenError] = useState<string | null>(null);
  const [controlIndicator, setControlIndicator] = useState<{
    mode: ControlIndicatorMode;
    detail?: Record<string, unknown>;
    updatedAt: number;
  }>({ mode: "idle", updatedAt: Date.now() });
  const [controlEvidence, setControlEvidence] = useState<ControlEvidence[]>([]);
  const [actionKind, setActionKind] = useState<ActionKind>("click");
  const [x, setX] = useState("400");
  const [y, setY] = useState("300");
  const [text, setText] = useState("");
  const [keys, setKeys] = useState("ctrl+l");
  const [waitMs, setWaitMs] = useState("500");
  const [goal, setGoal] = useState("");
  const [visionModelId, setVisionModelId] = useState("");
  const [models, setModels] = useState<Model[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [visionOutput, setVisionOutput] = useState("");
  const [plan, setPlan] = useState<ComputerActionPlan | null>(null);
  const [preview, setPreview] = useState<ComputerPreview | null>(null);
  const [previewExpiresAt, setPreviewExpiresAt] = useState<number | null>(null);
  const [selectedPoint, setSelectedPoint] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [highlightedAction, setHighlightedAction] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [screenshotImageBox, setScreenshotImageBox] =
    useState<ScreenshotImageBox | null>(null);
  const [liveCanvasBox, setLiveCanvasBox] = useState<ScreenshotImageBox | null>(
    null,
  );
  const [leaseOwner, setLeaseOwner] = useState<ComputerLeaseOwner | null>(null);
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [busy, setBusy] = useState<
    | "status"
    | "capture"
    | "ground"
    | "vision"
    | "plan"
    | "preview"
    | "execute"
    | "release"
    | "stream"
    | null
  >(null);
  const screenshotFrameRef = useRef<HTMLDivElement | null>(null);
  const screenshotImageRef = useRef<HTMLImageElement | null>(null);
  const liveCanvasFrameRef = useRef<HTMLDivElement | null>(null);
  const liveCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const pcStream = usePcScreenStream(liveCanvasRef, {
    enabled: observationMode === "live",
  });

  const addLog = useCallback((item: Omit<LogItem, "id">) => {
    setLogs((prev) =>
      [{ ...item, id: crypto.randomUUID() }, ...prev].slice(0, 12),
    );
  }, []);

  useEffect(() => {
    const key = "echo:computer-lease-owner";
    let ownerId = window.localStorage.getItem(key);
    if (!ownerId) {
      ownerId = crypto.randomUUID();
      window.localStorage.setItem(key, ownerId);
    }
    setLeaseOwner({
      owner_id: ownerId,
      owner_label: tc("Local computer automation"),
    });
  }, [tc]);

  // ── Preview lifecycle ────────────────────────────────────────
  // The backend issues a token with a 90 s TTL (_PENDING_TTL_SECONDS
  // in computer_router.py). We mirror the deadline locally so the UI
  // can show a countdown chip and auto-clear once the server would
  // 404 anyway. ``apiSeconds`` is the value the backend returns in
  // ``expires_in_seconds`` — kept as the source of truth so a future
  // tweak on the server (eg. a per-action TTL) flows through here
  // without a frontend change.
  const applyPreview = (next: ComputerPreview) => {
    setPreview(next);
    const ttl = Math.max(1, Number(next.expires_in_seconds || 90));
    setPreviewExpiresAt(Date.now() + ttl * 1000);
  };

  const clearPreview = useCallback(() => {
    setPreview(null);
    setPreviewExpiresAt(null);
  }, []);

  // Tick state for the preview-token countdown chip. Re-rendered
  // every second while a preview is queued; null otherwise so we
  // don't burn a timer on the idle case.
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (previewExpiresAt === null) return;
    setNow(Date.now());
    const handle = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(handle);
  }, [previewExpiresAt]);

  const previewSecondsLeft =
    previewExpiresAt === null
      ? null
      : Math.max(0, Math.ceil((previewExpiresAt - now) / 1000));
  const previewExpired = previewExpiresAt !== null && previewSecondsLeft === 0;
  const runtimeState = getRuntimeState(status, tc);
  const deviceState = getDeviceState(status, tc);
  const activeAction = getActiveAction({
    busy,
    preview,
    previewSecondsLeft,
    plan,
    screenshot,
  }, tc);
  const cursorPoint = getCursorPoint(status);
  const visualTarget = getVisualTarget({
    highlightedAction,
    plan,
    preview,
    selectedPoint,
  }, tc);
  const leaseState = getLeaseState(status?.lease, leaseOwner, tc);
  const leaseBlocked = leaseState.tone === "blocked";
  const computerUnavailable = runtimeState.blocksActions;
  const computerActionDisabled = busy !== null || computerUnavailable;

  // computerControlSession is captured once per action by
  // runComputerControlAction and held for that action's whole
  // duration. If getStopped() closed over `leaseBlocked` by value, an
  // in-flight action would keep evaluating the lease state from
  // whenever the memo was last recomputed — a lease lost mid-action
  // wouldn't be observed until the NEXT action starts. Track it via a
  // ref so getStopped() always reads the current value.
  const leaseBlockedRef = useRef(leaseBlocked);
  useEffect(() => {
    leaseBlockedRef.current = leaseBlocked;
  }, [leaseBlocked]);

  const computerControlSession = useMemo<ControlSessionOptions>(
    () => ({
      sessionId: leaseOwner?.owner_id,
      ownerId: leaseOwner?.owner_id,
      ownerLabel: leaseOwner?.owner_label ?? tc("Local computer automation"),
      surface: "computer",
      targetId: "local-pc",
      getStopped: () => (leaseBlockedRef.current ? "lease_lost" : false),
      setIndicator: (mode, detail) => {
        setControlIndicator({
          mode,
          detail,
          updatedAt: Date.now(),
        });
      },
      recordEvidence: (evidence) => {
        setControlEvidence((prev) =>
          [
            {
              ...evidence,
              id:
                evidence.id ??
                `control-${Date.now()}-${Math.random()
                  .toString(36)
                  .slice(2, 8)}`,
              at: evidence.at ?? Date.now(),
            },
            ...prev,
          ].slice(0, 12),
        );
      },
    }),
    [leaseOwner, tc],
  );

  const runComputerControlAction = useCallback(
    async <T,>(action: string, run: () => Promise<T>): Promise<T> =>
      runControlSessionAction(action, run, {
        control: computerControlSession,
        interrupted: (reason) => {
          throw new Error(`computer control interrupted: ${reason}`);
        },
      }),
    [computerControlSession],
  );

  const mergeLease = useCallback((lease?: ComputerLease) => {
    if (!lease) return;
    setStatus((current) => (current ? { ...current, lease } : current));
  }, []);

  // Auto-clear once the server-side token would be 404. We log it so
  // the operator knows why the confirm card vanished — silently
  // dropping it would feel like a bug.
  useEffect(() => {
    if (!previewExpired) return;
    addLog({
      title: tc("Confirmation expired"),
      detail: tc("The server cleared the token. Preview the action again."),
      tone: "warn",
    });
    clearPreview();
  }, [addLog, clearPreview, previewExpired, tc]);

  const openModelSettings = () => {
    window.dispatchEvent(
      new CustomEvent("echo:open-settings", {
        detail: { tab: "models" },
      }),
    );
  };

  const refreshStatus = useCallback(async () => {
    setBusy("status");
    setStatusError(null);
    let timeoutId: number | undefined;
    try {
      const timeout = new Promise<never>((_, reject) => {
        timeoutId = window.setTimeout(
          () => reject(new Error(tc("Connection timed out. Please retry."))),
          8000,
        );
      });
      const data = await Promise.race([getComputerStatus(), timeout]);
      const nextRuntimeState = getRuntimeState(data, tc);
      setStatus(data);
      addLog({
        title: nextRuntimeState.logTitle,
        detail: nextRuntimeState.detail,
        tone:
          nextRuntimeState.tone === "error"
            ? "error"
            : nextRuntimeState.tone === "warn"
              ? "warn"
              : "ok",
      });
    } catch (error) {
      swallow(error);
      setStatusError(error instanceof Error ? error.message : tc("Unable to check this computer."));
      addLog({
        title: tc("Failed to read status"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      setBusy(null);
    }
  }, [addLog, tc]);

  const refreshPcStreamStats = useCallback(async () => {
    try {
      const data = await getPcScreenStats();
      setPcScreenStats(data);
      setPcScreenError(null);
    } catch (error) {
      swallow(error);
      setPcScreenError(String(error));
    }
  }, []);

  const startLiveScreen = async () => {
    setBusy("stream");
    setPcScreenError(null);
    try {
      const data = await startPcScreenCapture({
        fps: 10,
        scale: 1,
        quality: 72,
      });
      setPcScreenStats(data.stats);
      setObservationMode("live");
      addLog({
        title: tc("Live screen started"),
        detail: tc(
          "The computer view is now live. Clicking the view only selects a point; it does not execute anything.",
        ),
        tone: "ok",
      });
    } catch (error) {
      swallow(error);
      setPcScreenError(String(error));
      addLog({
        title: tc("Failed to start live screen"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const stopLiveScreen = async () => {
    setBusy("stream");
    try {
      const data = await stopPcScreenCapture();
      setPcScreenStats(data.last_stats);
      addLog({
        title: tc("Live screen stopped"),
        detail: tc("The current workspace layout is preserved. Restart the live screen when needed."),
        tone: "ok",
      });
    } catch (error) {
      swallow(error);
      setPcScreenError(String(error));
      addLog({
        title: tc("Failed to stop live screen"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    if (observationMode !== "live") return;
    void refreshPcStreamStats();
    const handle = window.setInterval(() => {
      void refreshPcStreamStats();
    }, 3000);
    return () => window.clearInterval(handle);
  }, [observationMode, refreshPcStreamStats]);

  const measureScreenshotImage = useCallback(() => {
    const frame = screenshotFrameRef.current;
    const image = screenshotImageRef.current;
    if (!frame || !image || !image.naturalWidth || !image.naturalHeight) return;

    const frameRect = frame.getBoundingClientRect();
    const imageRect = image.getBoundingClientRect();
    setScreenshotImageBox({
      left: imageRect.left - frameRect.left,
      top: imageRect.top - frameRect.top,
      width: imageRect.width,
      height: imageRect.height,
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
    });
  }, []);

  const measureLiveCanvas = useCallback(() => {
    const frame = liveCanvasFrameRef.current;
    const canvas = liveCanvasRef.current;
    if (!frame || !canvas || !canvas.width || !canvas.height) return;

    const frameRect = frame.getBoundingClientRect();
    const canvasRect = canvas.getBoundingClientRect();
    const next: ScreenshotImageBox = {
      left: canvasRect.left - frameRect.left,
      top: canvasRect.top - frameRect.top,
      width: canvasRect.width,
      height: canvasRect.height,
      naturalWidth: canvas.width,
      naturalHeight: canvas.height,
    };
    // Re-runs on every incoming stream frame (up to 10x/sec) via the
    // effect below, but the canvas's on-screen position/size only
    // actually changes on resize/layout events. Skip the setState
    // (and the full-page re-render it triggers) when nothing moved.
    setLiveCanvasBox((current) =>
      current &&
      current.left === next.left &&
      current.top === next.top &&
      current.width === next.width &&
      current.height === next.height &&
      current.naturalWidth === next.naturalWidth &&
      current.naturalHeight === next.naturalHeight
        ? current
        : next,
    );
  }, []);

  useEffect(() => {
    if (!screenshot?.data_url) {
      setScreenshotImageBox(null);
      return;
    }

    const frame = screenshotFrameRef.current;
    const image = screenshotImageRef.current;
    if (!frame || !image) return;

    const resizeObserver = new ResizeObserver(measureScreenshotImage);
    resizeObserver.observe(frame);
    resizeObserver.observe(image);
    window.addEventListener("resize", measureScreenshotImage);
    const frameId = window.requestAnimationFrame(measureScreenshotImage);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", measureScreenshotImage);
      window.cancelAnimationFrame(frameId);
    };
  }, [measureScreenshotImage, screenshot?.data_url]);

  useEffect(() => {
    if (observationMode !== "live") {
      setLiveCanvasBox(null);
      return;
    }
    const frame = liveCanvasFrameRef.current;
    const canvas = liveCanvasRef.current;
    if (!frame || !canvas) return;

    const resizeObserver = new ResizeObserver(measureLiveCanvas);
    resizeObserver.observe(frame);
    resizeObserver.observe(canvas);
    window.addEventListener("resize", measureLiveCanvas);
    const frameId = window.requestAnimationFrame(measureLiveCanvas);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", measureLiveCanvas);
      window.cancelAnimationFrame(frameId);
    };
  }, [measureLiveCanvas, observationMode]);

  useEffect(() => {
    if (observationMode !== "live") return;
    const frameId = window.requestAnimationFrame(measureLiveCanvas);
    return () => window.cancelAnimationFrame(frameId);
  }, [measureLiveCanvas, observationMode, pcStream.frameCount]);

  const capture = async () => {
    setBusy("capture");
    try {
      const data = await captureComputerScreen({
        controlSessionId: computerControlSession.sessionId,
      });
      setScreenshot(data);
      setHighlightedAction(null);
      await computerControlSession.recordEvidence?.({
        kind: "screenshot",
        action: "capture",
        ok: data.ok,
        summary: data.ok
          ? `${data.size_bytes || 0} bytes`
          : data.error || "capture failed",
        detail: {
          path: data.path,
          size_bytes: data.size_bytes,
          created_at: data.created_at,
        },
      });
      addLog({
        title: data.ok ? tc("Current screen captured") : tc("Screenshot failed"),
        detail: data.ok ? `${data.size_bytes || 0} bytes` : data.error || "",
        tone: data.ok ? "ok" : "error",
      });
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Screenshot request failed"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const action = useMemo<ComputerAction>(() => {
    if (actionKind === "click" || actionKind === "move") {
      return {
        action: actionKind,
        x: Number(x),
        y: Number(y),
        button: "left",
        clicks: 1,
      };
    }
    if (actionKind === "type") {
      return { action: "type", text, interval: 0.01 };
    }
    if (actionKind === "key") {
      return { action: "key", keys };
    }
    return { action: "wait", ms: Number(waitMs) };
  }, [actionKind, keys, text, waitMs, x, y]);

  const visionModels = useMemo(
    () => models.filter((model) => model.supports_vision === true),
    [models],
  );

  const selectedVisionModel = useMemo(
    () => models.find((model) => model.id === visionModelId),
    [models, visionModelId],
  );

  // Agent 循环预演 · preview-confirm-execute loop:
  // 生成候选动作 → 人工确认 → 执行 (control_session_id + preview_token)
  const previewAction = async () => {
    setBusy("preview");
    clearPreview();
    try {
      const data = await runComputerControlAction("preview", () =>
        previewComputerAction(action, {
          leaseOwner,
          controlSessionId: computerControlSession.sessionId,
        }),
      );
      mergeLease(data.lease);
      applyPreview(data);
      addLog({
        title: tc("Action added to confirmation queue"),
        detail: `${data.action.action} · ${data.risk.level}`,
        tone: data.risk.level === "high" ? "warn" : "ok",
      });
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Action preview failed"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const previewSelectedPoint = async () => {
    if (!selectedPoint) return;
    setBusy("preview");
    clearPreview();
    setHighlightedAction(null);
    try {
      const data = await runComputerControlAction(
        "preview_selected_point",
        () =>
          previewComputerAction(
            {
              action: "click",
              x: selectedPoint.x,
              y: selectedPoint.y,
              button: "left",
              clicks: 1,
            },
            {
              leaseOwner,
              controlSessionId: computerControlSession.sessionId,
            },
          ),
      );
      mergeLease(data.lease);
      applyPreview(data);
      addLog({
        title: tc("Screen point added to confirmation queue"),
        detail: `${selectedPoint.x}, ${selectedPoint.y} · ${data.risk.level}`,
        tone: data.risk.level === "high" ? "warn" : "ok",
      });
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Point-click preview failed"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const planNextActions = async () => {
    setBusy("plan");
    setPlan(null);
    setHighlightedAction(null);
    clearPreview();
    try {
      const data = await runComputerControlAction("plan", () =>
        planComputerActions(goal, {
          capture: true,
          leaseOwner,
          controlSessionId: computerControlSession.sessionId,
        }),
      );
      setPlan(data);
      mergeLease(data.lease);
      if (data.screenshot) setScreenshot(data.screenshot);
      addLog({
        title: data.suggestions.length
          ? tc("Next-step plan generated")
          : tc("No plan available"),
        detail: data.suggestions.length
          ? `${data.suggestions.length} ${tc("candidate actions awaiting confirmation")}`
          : tc("Describe the goal more specifically."),
        tone: data.suggestions.length ? "ok" : "warn",
      });
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Failed to generate plan"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const acceptSuggestion = (
    suggestion: ComputerActionPlan["suggestions"][number],
  ) => {
    setHighlightedAction(null);
    applyPreview({
      ok: true,
      token: suggestion.token,
      action: suggestion.action,
      risk: suggestion.risk,
      expires_in_seconds: suggestion.expires_in_seconds,
    });
    addLog({
      title: tc("Candidate action added to confirmation queue"),
      detail: `${suggestion.action.action} · ${suggestion.risk.level}`,
      tone: suggestion.risk.level === "high" ? "warn" : "ok",
    });
  };

  const runAgentLoopPreview = async () => {
    setBusy("plan");
    setPlan(null);
    setHighlightedAction(null);
    clearPreview();
    try {
      const data = await runComputerControlAction("agent_loop_preview", () =>
        planComputerActions(goal, {
          capture: true,
          leaseOwner,
          controlSessionId: computerControlSession.sessionId,
        }),
      );
      setPlan(data);
      mergeLease(data.lease);
      if (data.screenshot) setScreenshot(data.screenshot);
      const first = data.suggestions[0];
      if (first) {
        acceptSuggestion(first);
        addLog({
          title: tc("Agent preview complete"),
          detail: tc("The first step is awaiting your confirmation before it runs."),
          tone: "ok",
        });
      } else {
        addLog({
          title: tc("Agent has no executable next step"),
          detail: tc("No candidate action was generated. Describe the goal more specifically."),
          tone: "warn",
        });
      }
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Agent loop preview failed"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const groundVisionOutput = async () => {
    setBusy("ground");
    setPlan(null);
    setHighlightedAction(null);
    clearPreview();
    try {
      const data = await runComputerControlAction("ground", () =>
        groundComputerActions(goal, visionOutput, {
          capture: true,
          leaseOwner,
          controlSessionId: computerControlSession.sessionId,
        }),
      );
      setPlan(data);
      mergeLease(data.lease);
      if (data.screenshot) setScreenshot(data.screenshot);
      addLog({
        title: data.suggestions.length
          ? tc("Vision output validated")
          : tc("No action found"),
        detail: data.suggestions.length
          ? `${data.suggestions.length} ${tc("actions awaiting confirmation")}`
          : tc("Paste a valid JSON action."),
        tone: data.suggestions.length ? "ok" : "warn",
      });
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Failed to parse vision output"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const askVisionModel = async () => {
    setBusy("vision");
    setPlan(null);
    setHighlightedAction(null);
    clearPreview();
    try {
      const data = await runComputerControlAction("vision", () =>
        askVisionModelForComputerActions(goal, visionModelId, {
          leaseOwner,
          controlSessionId: computerControlSession.sessionId,
        }),
      );
      setPlan(data);
      mergeLease(data.lease);
      if (data.screenshot) setScreenshot(data.screenshot);
      addLog({
        title: data.ok
          ? tc("Vision model returned actions")
          : tc("Vision model is not ready"),
        detail: data.ok
          ? `${data.suggestions.length} ${tc("actions awaiting confirmation")}`
          : (data as unknown as { error?: string }).error ||
            tc("Configure a vision model."),
        tone: data.ok ? "ok" : "warn",
      });
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Vision model request failed"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const selectScreenshotPoint = (event: MouseEvent<HTMLImageElement>) => {
    const img = screenshotImageRef.current;
    if (!img) return;
    const rect = img.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const realX = Math.round(
      ((event.clientX - rect.left) / rect.width) * img.naturalWidth,
    );
    const realY = Math.round(
      ((event.clientY - rect.top) / rect.height) * img.naturalHeight,
    );
    const clampedX = Math.max(0, Math.min(realX, img.naturalWidth - 1));
    const clampedY = Math.max(0, Math.min(realY, img.naturalHeight - 1));
    setScreenshotImageBox((current) =>
      current
        ? {
            ...current,
            naturalWidth: img.naturalWidth,
            naturalHeight: img.naturalHeight,
          }
        : current,
    );
    setSelectedPoint({ x: clampedX, y: clampedY });
    setActionKind("click");
    setX(String(clampedX));
    setY(String(clampedY));
    addLog({
      title: tc("Point selected from screenshot"),
      detail: `${clampedX}, ${clampedY}`,
      tone: "ok",
    });
  };

  const selectLiveCanvasPoint = (event: MouseEvent<HTMLCanvasElement>) => {
    const canvas = liveCanvasRef.current;
    if (!canvas || !canvas.width || !canvas.height) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const realX = Math.round(
      ((event.clientX - rect.left) / rect.width) * canvas.width,
    );
    const realY = Math.round(
      ((event.clientY - rect.top) / rect.height) * canvas.height,
    );
    const clampedX = Math.max(0, Math.min(realX, canvas.width - 1));
    const clampedY = Math.max(0, Math.min(realY, canvas.height - 1));
    setLiveCanvasBox((current) =>
      current
        ? {
            ...current,
            naturalWidth: canvas.width,
            naturalHeight: canvas.height,
          }
        : current,
    );
    setSelectedPoint({ x: clampedX, y: clampedY });
    setActionKind("click");
    setX(String(clampedX));
    setY(String(clampedY));
    addLog({
      title: tc("Point selected from live screen"),
      detail: `${clampedX}, ${clampedY}`,
      tone: "ok",
    });
  };

  const executePreview = async () => {
    if (!preview) return;
    setBusy("execute");
    try {
      const data = await runComputerControlAction("execute", () =>
        executeComputerAction(preview.token, {
          leaseOwner,
          controlSessionId: computerControlSession.sessionId,
          controlActionId: `computer-preview-${preview.token}`,
        }),
      );
      mergeLease(data.lease);
      clearPreview();
      addLog({
        title: data.ok ? tc("Action executed") : tc("Action failed"),
        detail: summarizeResult(data),
        tone: data.ok ? "ok" : "error",
      });
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Execution request failed"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  const releaseLease = async () => {
    if (!leaseOwner) return;
    setBusy("release");
    try {
      const data = await releaseComputerLease(leaseOwner, {
        controlSessionId: computerControlSession.sessionId,
      });
      mergeLease(data.lease);
      addLog({
        title: tc("Computer control released"),
        detail: tc("Other projects can now take control of this computer."),
        tone: "ok",
      });
    } catch (error) {
      swallow(error);
      addLog({
        title: tc("Failed to release control"),
        detail: String(error),
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    void refreshStatus();
    void loadModels()
      .then((data) => {
        setModels(data);
        const firstVisionModel = data.find(
          (model) => model.supports_vision === true,
        );
        if (firstVisionModel) {
          setVisionModelId((current) => current || firstVisionModel.id);
        }
      })
      .catch((error) => setModelsError(String(error)));
  }, [refreshStatus]);

  const liveScreenRunning = pcScreenStats?.running === true;
  const liveScreenDetail = pcScreenError
    ? tc("Connection error")
    : pcStream.isConnected
      ? `${pcStream.fps} fps · ${pcStream.frameCount} ${tc("frames")}`
      : liveScreenRunning
        ? tc("Waiting for screen")
        : tc("Not started");

  return (
    <WorkspaceContainer>
      <WorkspaceBody>
        <div className="mx-auto flex size-full max-w-7xl flex-col gap-4 py-2">
          <section className="workspace-panel flex flex-col gap-4 p-5">
            {statusError && (
              <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-destructive/25 bg-destructive/8 px-3 py-2 text-sm">
                <span className="min-w-0 text-destructive">{statusError}</span>
                <Button size="sm" variant="outline" onClick={() => void refreshStatus()} disabled={busy !== null}>
                  {tc("Retry")}
                </Button>
              </div>
            )}
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-3">
                <div className="flex size-11 items-center justify-center rounded-lg bg-success text-white shadow-[var(--shadow-xs)]">
                  <MonitorCheckIcon className="size-5" />
                </div>
                <div>
                  <h1 className="text-xl font-semibold tracking-tight">
                    {tc("Computer assistant")}
                  </h1>
                  <p className="text-sm text-muted-foreground">
                    {tc(
                      "Let the Agent see and operate this computer. Every step is previewed before you confirm it.",
                    )}
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={refreshStatus}
                  disabled={busy !== null}
                >
                  <RefreshCwIcon className="size-4" />
                  {tc("Refresh status")}
                </Button>
                <Button onClick={capture} disabled={computerActionDisabled}>
                  <EyeIcon className="size-4" />
                  {tc("Capture screen")}
                </Button>
              </div>
            </div>

            <div className="grid gap-3 lg:grid-cols-4">
              <DeviceStatePanel state={deviceState} />
              <PermissionGuardPanel
                hasScreenshot={Boolean(screenshot?.data_url)}
                hasPreview={Boolean(preview)}
                leaseState={leaseState}
                onReleaseLease={releaseLease}
                releaseDisabled={busy !== null}
                previewSecondsLeft={previewSecondsLeft}
              />
              <CurrentActionPanel action={activeAction} />
              <ControlSessionPanel
                evidence={controlEvidence}
                indicator={controlIndicator}
                session={computerControlSession}
              />
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
              <StatusTile
                label={tc("Runtime health")}
                value={getHealthLabel(status?.health, status?.ready, tc)}
              />
              <StatusTile
                label={tc("Confirmation mode")}
                  value={
                  statusError
                    ? tc("Unavailable")
                    : status?.mode
                      ? tc("Preview, then confirm")
                      : tc("Checking")
                }
              />
              <StatusTile
                label={tc("Screen")}
                value={
                  status?.screen.width
                    ? `${status.screen.width} × ${status.screen.height}`
                    : status?.screen.error || "-"
                }
              />
              <StatusTile
                label={tc("Cursor position")}
                value={
                  status?.screen.cursor_x != null
                    ? `${status.screen.cursor_x}, ${status.screen.cursor_y}`
                    : "-"
                }
              />
              <StatusTile
                label={tc("Computer control")}
                value={
                  status?.pyautogui_available
                    ? tc("Ready")
                    : tc("Not ready")
                }
              />
              <StatusTile
                label={tc("Semantic targeting")}
                value={
                  status?.uia_available ? tc("Ready") : tc("Available with limits")
                }
              />
              <StatusTile label={tc("Control lease")} value={leaseState.label} />
            </div>

            {status && <RuntimeReadinessPanel status={status} />}

            {computerUnavailable && status && (
              <div className="rounded-lg border border-warning/70 bg-warning/5 px-4 py-3 text-sm leading-6 text-warning dark:border-warning/60">
                <div className="flex items-start gap-2">
                  <ShieldAlertIcon className="mt-0.5 size-4 shrink-0" />
                  <div>
                    <div className="font-medium">
                      {tc("Local runtime is blocked")}
                    </div>
                    <p className="mt-1">
                      {runtimeState.detail ||
                        tc(
                          "The backend is running, but required automation capabilities are not ready. Screenshots, previews, mouse, and keyboard actions are temporarily unavailable.",
                        )}
                    </p>
                    {runtimeState.actions.length ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {runtimeState.actions.map((action) => (
                          <span
                            key={action}
                            className="rounded-md border border-warning/80 px-2 py-0.5 font-mono text-xs"
                          >
                            {action}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-1 font-mono text-xs">
                        python -m pip install pyautogui
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}
          </section>

          <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-[1.35fr_0.95fr]">
            <section className="workspace-panel flex min-h-0 flex-col overflow-hidden p-4">
              <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-sm font-semibold">
                    {tc("Screen observation")}
                  </h2>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    {observationMode === "snapshot" &&
                    screenshot?.created_at ? (
                      <span>
                        {new Date(
                          screenshot.created_at * 1000,
                        ).toLocaleTimeString()}
                      </span>
                    ) : null}
                    {observationMode === "live" ? (
                      <>
                        <span>{liveScreenDetail}</span>
                        {pcScreenStats?.config?.backend ? (
                          <span>{pcScreenStats.config.backend}</span>
                        ) : null}
                      </>
                    ) : null}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="inline-flex overflow-hidden rounded-lg border border-border bg-background p-0.5">
                    <button
                      type="button"
                      onClick={() => setObservationMode("snapshot")}
                      className={cn(
                        "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                        observationMode === "snapshot"
                          ? "bg-primary text-primary-foreground"
                          : "text-muted-foreground hover:bg-muted",
                      )}
                    >
                      {tc("Snapshot")}
                    </button>
                    <button
                      type="button"
                      onClick={() => setObservationMode("live")}
                      className={cn(
                        "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                        observationMode === "live"
                          ? "bg-primary text-primary-foreground"
                          : "text-muted-foreground hover:bg-muted",
                      )}
                    >
                      {tc("Live")}
                    </button>
                  </div>
                  {observationMode === "live" ? (
                    <Button
                      size="sm"
                      variant={liveScreenRunning ? "outline" : "default"}
                      onClick={
                        liveScreenRunning ? stopLiveScreen : startLiveScreen
                      }
                      disabled={busy !== null || computerUnavailable}
                    >
                      {liveScreenRunning ? (
                        <>
                          <SquareIcon className="size-4" />
                          {tc("Stop live")}
                        </>
                      ) : (
                        <>
                          <RadioIcon className="size-4" />
                          {tc("Start live")}
                        </>
                      )}
                    </Button>
                  ) : null}
                </div>
              </div>
              <div
                ref={
                  observationMode === "live"
                    ? liveCanvasFrameRef
                    : screenshotFrameRef
                }
                className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg border border-border bg-muted/30"
              >
                {observationMode === "live" ? (
                  <>
                    <p id="live-screen-help" className="sr-only">
                      {tc(
                        "Click the live screen to select a point. Press Enter to select the center.",
                      )}
                    </p>
                    <canvas
                      ref={liveCanvasRef}
                      aria-describedby="live-screen-help"
                      aria-label={tc("Live computer screen")}
                      role="button"
                      tabIndex={0}
                      className="max-h-full max-w-full cursor-crosshair bg-black object-contain focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      onClick={selectLiveCanvasPoint}
                      onKeyDown={(e) => {
                        if (e.key !== "Enter" && e.key !== " ") return;
                        e.preventDefault();
                        const canvas = liveCanvasRef.current;
                        if (!canvas || !canvas.width || !canvas.height) return;
                        const cx = Math.floor(canvas.width / 2);
                        const cy = Math.floor(canvas.height / 2);
                        setSelectedPoint({ x: cx, y: cy });
                        setActionKind("click");
                        setX(String(cx));
                        setY(String(cy));
                        addLog({
                          title: tc("Point selected from live screen"),
                          detail: `${cx}, ${cy}`,
                          tone: "ok",
                        });
                      }}
                    />
                    <ScreenshotActionOverlay
                      cursor={cursorPoint}
                      imageBox={liveCanvasBox}
                      target={visualTarget}
                    />
                    {visualTarget && (
                      <div className="pointer-events-none absolute left-3 top-3 rounded-full border border-border bg-background/90 px-3 py-1 text-xs font-medium shadow-[var(--shadow-xs)]">
                        {visualTarget.label} · {Math.round(visualTarget.x)},{" "}
                        {Math.round(visualTarget.y)}
                      </div>
                    )}
                    <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap items-center gap-2 rounded-full border border-border bg-background/90 px-3 py-1 text-xs shadow-[var(--shadow-xs)]">
                      <span
                        className={cn(
                          "size-1.5 rounded-full",
                          pcStream.isConnected
                            ? "bg-success"
                            : pcScreenError
                              ? "bg-destructive"
                              : "bg-warning",
                        )}
                      />
                      <span>{liveScreenDetail}</span>
                    </div>
                    {pcScreenError ? (
                      <div className="pointer-events-none absolute inset-x-6 top-6 rounded-lg border border-destructive/30 bg-background/95 px-3 py-2 text-xs leading-5 text-destructive shadow-[var(--shadow-xs)]">
                        {pcScreenError}
                      </div>
                    ) : null}
                    {!pcStream.frameCount ? (
                      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/70 text-sm text-muted-foreground">
                        <RadioIcon className="size-7" />
                        {liveScreenRunning
                          ? tc("Waiting for the live screen")
                          : tc("Select “Start live” to open the computer view.")}
                      </div>
                    ) : null}
                  </>
                ) : screenshot?.data_url ? (
                  <>
                    <p id="screenshot-help" className="sr-only">
                      {tc("Click the screenshot to select a point. Press Enter to select the center.")}
                    </p>
                    <img
                      ref={screenshotImageRef}
                      src={screenshot.data_url}
                      alt={tc("Current screen screenshot")}
                      aria-describedby="screenshot-help"
                      role="button"
                      tabIndex={0}
                      className="max-h-full max-w-full cursor-crosshair object-contain focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      onLoad={measureScreenshotImage}
                      onClick={selectScreenshotPoint}
                      onKeyDown={(e) => {
                        if (e.key !== "Enter" && e.key !== " ") return;
                        e.preventDefault();
                        const img = screenshotImageRef.current;
                        if (!img || !img.naturalWidth || !img.naturalHeight)
                          return;
                        const cx = Math.floor(img.naturalWidth / 2);
                        const cy = Math.floor(img.naturalHeight / 2);
                        setSelectedPoint({ x: cx, y: cy });
                        setActionKind("click");
                        setX(String(cx));
                        setY(String(cy));
                        addLog({
                          title: tc("Point selected from screenshot"),
                          detail: `${cx}, ${cy}`,
                          tone: "ok",
                        });
                      }}
                    />
                    <ScreenshotActionOverlay
                      cursor={cursorPoint}
                      imageBox={screenshotImageBox}
                      target={visualTarget}
                    />
                    {visualTarget && (
                      <div className="pointer-events-none absolute left-3 top-3 rounded-full border border-border bg-background/90 px-3 py-1 text-xs font-medium shadow-[var(--shadow-xs)]">
                        {visualTarget.label} · {Math.round(visualTarget.x)},{" "}
                        {Math.round(visualTarget.y)}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
                    <EyeIcon className="size-7" />
                    {tc("Select “Capture screen” to take a desktop screenshot.")}
                  </div>
                )}
              </div>
            </section>

            <aside className="flex min-h-0 flex-col gap-4">
              <section className="workspace-panel p-4">
                <div className="mb-3 flex items-center gap-2">
                  <ListChecksIcon className="size-4 text-primary" />
                  <h2 className="text-sm font-semibold">{tc("Task plan")}</h2>
                </div>
                <div className="flex flex-col gap-3">
                  <Textarea
                    value={goal}
                    onChange={(e) => setGoal(e.target.value)}
                    placeholder={tc(
                      "For example: open Edge and visit https://gemini.google.com",
                    )}
                    className="min-h-20"
                  />
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <Button
                      onClick={runAgentLoopPreview}
                      disabled={computerActionDisabled || !goal.trim()}
                    >
                      <PlayIcon className="size-4" />
                      {tc("Preview agent loop")}
                    </Button>
                    <Button
                      variant="outline"
                      onClick={planNextActions}
                      disabled={computerActionDisabled}
                    >
                      <ListChecksIcon className="size-4" />
                      {tc("Observe and plan next step")}
                    </Button>
                  </div>
                  {plan?.suggestions.length ? (
                    <div className="flex max-h-56 flex-col gap-2 overflow-y-auto pr-1">
                      {plan.suggestions.map((item) => (
                        <div
                          key={item.id}
                          className={cn(
                            "rounded-lg border border-border bg-background/70 p-3 transition-colors",
                            highlightedAction === item.action &&
                              "border-success/40 bg-success/5 dark:border-success/70",
                          )}
                          onMouseEnter={() => setHighlightedAction(item.action)}
                          onMouseLeave={() => setHighlightedAction(null)}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="text-sm font-semibold">
                                {item.title}
                              </div>
                              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                                {item.rationale}
                              </p>
                            </div>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => acceptSuggestion(item)}
                            >
                              {tc("Add for confirmation")}
                            </Button>
                          </div>
                          <pre className="mt-2 max-h-24 overflow-auto rounded-lg bg-black/5 p-2 text-xs dark:bg-white/10">
                            {JSON.stringify(item.action, null, 2)}
                          </pre>
                          <MatchedControlSummary action={item.action} />
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {selectedPoint && (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <Button
                        variant="outline"
                        onClick={() => {
                          setActionKind("click");
                          setX(String(selectedPoint.x));
                          setY(String(selectedPoint.y));
                        }}
                      >
                        <MousePointerClickIcon className="size-4" />
                        {tc("Use selected point")}
                      </Button>
                      <Button
                        onClick={previewSelectedPoint}
                        disabled={computerActionDisabled}
                      >
                        <ShieldAlertIcon className="size-4" />
                        {tc("Add for confirmation")}
                      </Button>
                    </div>
                  )}
                </div>
              </section>

              <section className="workspace-panel p-4">
                <div className="mb-3 flex items-center gap-2">
                  <ScanSearchIcon className="size-4 text-primary" />
                  <h2 className="text-sm font-semibold">{tc("Vision output")}</h2>
                </div>
                <div className="flex flex-col gap-3">
                  <div className="flex flex-col gap-2 sm:grid sm:grid-cols-[1fr_auto]">
                    {visionModels.length > 0 ? (
                      <Select
                        value={visionModelId}
                        onValueChange={setVisionModelId}
                      >
                        <SelectTrigger className="w-full">
                          <SelectValue placeholder={tc("Select a vision model")} />
                        </SelectTrigger>
                        <SelectContent>
                          {visionModels.map((model) => (
                            <SelectItem key={model.id} value={model.id}>
                              {model.display_name || model.name || model.id}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Input
                        value={visionModelId}
                        onChange={(e) => setVisionModelId(e.target.value)}
                        placeholder={tc("Vision model ID, for example glm-vision")}
                      />
                    )}
                    <Button
                      onClick={askVisionModel}
                      disabled={
                        computerActionDisabled ||
                        !goal.trim() ||
                        !visionModelId.trim()
                      }
                    >
                      <ScanSearchIcon className="size-4" />
                      {tc("Run model")}
                    </Button>
                  </div>
                  {visionModels.length > 0 ? (
                    <div className="flex items-center justify-between rounded-lg border border-border bg-background/70 px-3 py-2 text-xs text-muted-foreground">
                      <span>
                        {tc("Current: ")}
                        {selectedVisionModel?.display_name ||
                          selectedVisionModel?.name ||
                          visionModelId}
                      </span>
                      <span>
                        {visionModels.length} {tc("vision models")}
                      </span>
                    </div>
                  ) : (
                    <div className="flex items-center justify-between gap-3 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs leading-5 text-warning dark:border-warning/60">
                      <span>
                        {modelsError
                          ? tc("Could not load the model list. You can enter a model ID manually.")
                          : tc(
                              "No model is marked supports_vision. Enable vision for a custom model in Settings.",
                            )}
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={openModelSettings}
                      >
                        {tc("Open model settings")}
                      </Button>
                    </div>
                  )}
                  <p className="text-xs leading-5 text-muted-foreground">
                    {tc(
                      "The current screenshot is sent to the selected vision model. Returned actions still require confirmation.",
                    )}
                  </p>
                  <Textarea
                    value={visionOutput}
                    onChange={(e) => setVisionOutput(e.target.value)}
                    placeholder='{"action":"click","x":420,"y":320,"button":"left"}'
                    className="min-h-24 font-mono text-xs"
                  />
                  <Button
                    variant="outline"
                    onClick={groundVisionOutput}
                    disabled={computerActionDisabled || !visionOutput.trim()}
                  >
                    <ScanSearchIcon className="size-4" />
                    {tc("Parse and add candidates")}
                  </Button>
                </div>
              </section>

              <section className="workspace-panel p-4">
                <div className="mb-3 flex items-center gap-2">
                  <KeyboardIcon className="size-4 text-primary" />
                  <h2 className="text-sm font-semibold">{tc("Action preview")}</h2>
                </div>
                <div className="flex flex-col gap-3">
                  <Select
                    value={actionKind}
                    onValueChange={(value) =>
                      setActionKind(value as ActionKind)
                    }
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="click">{tc("Click point")}</SelectItem>
                      <SelectItem value="move">{tc("Move cursor")}</SelectItem>
                      <SelectItem value="type">{tc("Type text")}</SelectItem>
                      <SelectItem value="key">{tc("Keyboard shortcut")}</SelectItem>
                      <SelectItem value="wait">{tc("Wait")}</SelectItem>
                    </SelectContent>
                  </Select>

                  {(actionKind === "click" || actionKind === "move") && (
                    <div className="grid grid-cols-2 gap-2">
                      <Input
                        value={x}
                        onChange={(e) => setX(e.target.value)}
                        placeholder="x"
                      />
                      <Input
                        value={y}
                        onChange={(e) => setY(e.target.value)}
                        placeholder="y"
                      />
                    </div>
                  )}
                  {actionKind === "type" && (
                    <Textarea
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      placeholder={tc("Text to type into the focused control")}
                      className="min-h-24"
                    />
                  )}
                  {actionKind === "key" && (
                    <Input
                      value={keys}
                      onChange={(e) => setKeys(e.target.value)}
                      placeholder={tc("ctrl+l or enter")}
                    />
                  )}
                  {actionKind === "wait" && (
                    <Input
                      value={waitMs}
                      onChange={(e) => setWaitMs(e.target.value)}
                      placeholder={tc("Milliseconds")}
                    />
                  )}

                  <Button
                    onClick={previewAction}
                    disabled={computerActionDisabled}
                  >
                    <ShieldAlertIcon className="size-4" />
                    {tc("Generate confirmation")}
                  </Button>
                </div>
              </section>

              <section className="workspace-panel p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">{tc("Confirmation queue")}</h2>
                  {preview && previewSecondsLeft !== null ? (
                    <CountdownChip secondsLeft={previewSecondsLeft} />
                  ) : null}
                </div>
                {preview ? (
                  <div className="flex flex-col gap-3">
                    <div
                      className={cn(
                        "rounded-lg border p-3 text-sm",
                        preview.risk.level === "high"
                          ? "border-warning/40 bg-warning/5 text-warning"
                          : "border-border bg-background",
                      )}
                    >
                      <div className="font-semibold">
                        {tc("Risk: ")}
                        {preview.risk.level}
                      </div>
                      <p className="mt-1 leading-6">{preview.risk.reason}</p>
                      <pre className="mt-2 overflow-auto rounded-lg bg-black/5 p-2 text-xs dark:bg-white/10">
                        {JSON.stringify(preview.action, null, 2)}
                      </pre>
                      <MatchedControlSummary action={preview.action} />
                      {leaseBlocked ? (
                        <div className="mt-2 rounded-lg border border-warning/40 bg-warning/5 px-3 py-2 text-xs leading-5 text-warning dark:border-warning/60">
                          {leaseState.detail}
                        </div>
                      ) : null}
                    </div>
                    <Button
                      onClick={executePreview}
                      disabled={
                        computerActionDisabled || previewExpired || leaseBlocked
                      }
                    >
                      <PlayIcon className="size-4" />
                      {tc("Confirm and run")}
                    </Button>
                  </div>
                ) : (
                  <p className="text-sm leading-6 text-muted-foreground">
                    {tc(
                      "Mouse and keyboard actions awaiting confirmation appear here. Nothing touches the system before confirmation.",
                    )}
                  </p>
                )}
              </section>

              <section className="workspace-panel min-h-0 flex-1 p-4">
                <h2 className="mb-3 text-sm font-semibold">
                  {tc("Activity log")}
                </h2>
                <div className="flex max-h-72 flex-col gap-2 overflow-y-auto pr-1">
                  {logs.length === 0 ? (
                    <Empty className="gap-3 border-0 bg-transparent p-4 shadow-none">
                      <EmptyHeader>
                        <EmptyMedia variant="icon">
                          <ListChecksIcon />
                        </EmptyMedia>
                        <EmptyTitle className="text-sm">
                          {tc("No activity yet")}
                        </EmptyTitle>
                      </EmptyHeader>
                    </Empty>
                  ) : (
                    logs.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-lg border border-border bg-background/70 p-3"
                      >
                        <div className="flex items-center gap-2 text-sm font-medium">
                          <CheckCircle2Icon
                            className={cn(
                              "size-4",
                              item.tone === "ok" && "text-success",
                              item.tone === "warn" && "text-warning",
                              item.tone === "error" && "text-destructive",
                            )}
                          />
                          {item.title}
                        </div>
                        {item.detail && (
                          <p className="mt-1 break-words text-xs leading-5 text-muted-foreground">
                            {item.detail}
                          </p>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </section>
            </aside>
          </div>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

function StatusTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-background/70 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  );
}

function RuntimeReadinessPanel({ status }: { status: ComputerStatus }) {
  const { t } = useI18n();
  const tc = (source: string) => t.workspaceComputer[source] ?? source;
  const state = getRuntimeState(status, tc);
  const blockers = getCriticalBlockers(status);
  const degraded = getDegradedCapabilities(status);
  const evidence = status.replay_evidence ?? status.readiness?.replay_evidence;
  const visibleItems =
    blockers.length || degraded.length
      ? [...blockers, ...degraded]
      : getCapabilities(status)
          .filter((item) => item.critical)
          .slice(0, 3);
  const toneClass = {
    ok: "border-success/30 bg-success/5 text-success dark:border-success/60",
    warn: "border-warning/30 bg-warning/5 text-warning dark:border-warning/60",
    error: "border-destructive/30 bg-destructive/10 text-destructive",
    loading: "border-border bg-background/70 text-foreground",
  }[state.tone];

  return (
    <div className={cn("rounded-lg border px-4 py-3", toneClass)}>
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold">
            {state.tone === "ok" ? (
              <ShieldCheckIcon className="size-4 shrink-0" />
            ) : (
              <ShieldAlertIcon className="size-4 shrink-0" />
            )}
            {tc("Runtime")} · {state.label}
          </div>
          <p className="mt-1 text-xs leading-5 opacity-85">{state.detail}</p>
        </div>
        {evidence?.case_id || evidence?.fingerprint ? (
          <div className="shrink-0 rounded-lg border border-current/20 px-3 py-2 text-xs leading-5">
            <div className="font-medium">{tc("Replay evidence")}</div>
            <div className="mt-0.5 font-mono opacity-80">
              {evidence.case_id || evidence.fingerprint}
            </div>
          </div>
        ) : null}
      </div>

      {visibleItems.length ? (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {visibleItems.map((item) => (
            <div
              key={item.id}
              className="rounded-lg border border-current/15 bg-background/45 px-3 py-2 text-xs leading-5 text-foreground"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">{item.title}</span>
                <span
                  className={cn(
                    "shrink-0 rounded-md border px-1.5 py-0.5 font-mono text-xs",
                    item.available
                      ? "border-success/40 text-success"
                      : item.critical
                        ? "border-destructive/40 text-destructive"
                        : "border-warning/40 text-warning",
                  )}
                >
                  {item.available
                    ? "ready"
                    : item.critical
                      ? "blocked"
                      : "degraded"}
                </span>
              </div>
              <div className="mt-1 text-muted-foreground">
                {item.reason || item.recommended_action || item.mode}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {state.actions.length ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {state.actions.map((action) => (
            <span
              key={action}
              className="rounded-md border border-current/20 bg-background/45 px-2 py-0.5 font-mono text-xs text-foreground"
            >
              {action}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

type DeviceState = {
  label: string;
  detail: string;
  tone: "ok" | "warn" | "error" | "loading";
};

type RuntimeState = DeviceState & {
  health: string;
  logTitle: string;
  blocksActions: boolean;
  actions: string[];
};

type ActiveAction = {
  label: string;
  detail: string;
  tone: "idle" | "active" | "warn" | "ok";
};

type LeaseState = {
  label: string;
  detail: string;
  tone: "idle" | "own" | "blocked";
  canRelease: boolean;
};

function getLeaseState(
  lease: ComputerLease | null | undefined,
  owner: ComputerLeaseOwner | null,
  tc: (source: string) => string,
): LeaseState {
  if (!lease?.held) {
    return {
      label: tc("Idle"),
      detail: tc("No project currently controls the physical mouse or keyboard."),
      tone: "idle",
      canRelease: false,
    };
  }
  const ttl =
    typeof lease.ttl_seconds === "number" ? Math.max(0, lease.ttl_seconds) : 0;
  const ownerLabel = lease.owner_label || tc("Another project");
  if (owner && lease.owner_id === owner.owner_id) {
    return {
      label: `${tc("This project")} · ${ttl}s`,
      detail: `${tc("This project controls the physical input; ")}${ttl}s ${tc(
        "remaining before another project can take over.",
      )}`,
      tone: "own",
      canRelease: true,
    };
  }
  return {
    label: `${ownerLabel} · ${ttl}s`,
    detail: `${ownerLabel}${tc(" controls the physical input; ")}${ttl}s ${tc(
      "remaining before automatic release.",
    )}`,
    tone: "blocked",
    canRelease: false,
  };
}

function getDeviceState(
  status: ComputerStatus | null,
  tc: (source: string) => string,
): DeviceState {
  const runtime = getRuntimeState(status, tc);
  if (runtime.tone !== "ok") {
    return {
      label: runtime.label,
      detail: runtime.detail,
      tone: runtime.tone,
    };
  }
  if (!status) {
    return {
      label: tc("Checking"),
      detail: tc("Checking whether the Agent can observe and operate this computer."),
      tone: "loading",
    };
  }
  if (!status.ok) {
    return {
      label: tc("Unavailable"),
      detail:
        status.screen.error || tc("This environment cannot read the screen or perform computer actions."),
      tone: "error",
    };
  }
  if (!status.pyautogui_available) {
    return {
      label: tc("Capabilities required"),
      detail: tc("The backend responded, but computer control capabilities are not ready."),
      tone: "warn",
    };
  }
  const screen = status.screen.width
    ? `${status.screen.width} × ${status.screen.height}`
    : tc("Screen connected");
  return {
    label: tc("Connected"),
    detail: `${tc("The screen can be observed and actions can run after confirmation. ")}${screen}`,
    tone: "ok",
  };
}

function getRuntimeState(
  status: ComputerStatus | null,
  tc: (source: string) => string,
): RuntimeState {
  if (!status) {
    return {
      health: "loading",
      label: tc("Checking"),
      logTitle: tc("Checking computer assistant"),
      detail: tc("Checking whether the Agent can observe and operate this computer."),
      tone: "loading",
      blocksActions: true,
      actions: [],
    };
  }
  const health = status.health ?? legacyHealth(status);
  const blockers = getCriticalBlockers(status);
  const degraded = getDegradedCapabilities(status);
  const actions = getRecommendedActions(status);
  const firstIssue = blockers[0] ?? degraded[0] ?? null;
  const fallbackError =
    status.screen.error ||
    (!status.pyautogui_available ? tc("pyautogui is unavailable") : "");
  if (
    health === "blocked" ||
    status.ready === false ||
    !status.ok ||
    !status.pyautogui_available
  ) {
    const detail =
      firstIssue?.reason ||
      firstIssue?.recommended_action ||
      fallbackError ||
      tc("Required computer automation capabilities failed runtime checks.");
    return {
      health,
      label: tc("Blocked"),
      logTitle: tc("Computer assistant blocked"),
      detail,
      tone: "error",
      blocksActions: true,
      actions,
    };
  }
  if (health === "degraded" || degraded.length > 0) {
    const names = degraded.map((item) => item.title).join("、");
    return {
      health,
      label: tc("Available with limits"),
      logTitle: tc("Computer assistant available with limits"),
      detail: names
        ? `${names}${tc(" unavailable; observation, preview, and confirmed execution still work.")}`
        : tc("Some optional capabilities are unavailable. Observation, preview, and confirmed execution still work."),
      tone: "warn",
      blocksActions: false,
      actions,
    };
  }
  const screen = status.screen.width
    ? `${status.screen.width} × ${status.screen.height}`
    : tc("Screen connected");
  return {
    health,
    label: tc("Ready"),
    logTitle: tc("Computer assistant ready"),
    detail: `${tc("Required capabilities passed runtime checks. ")}${screen}`,
    tone: "ok",
    blocksActions: false,
    actions,
  };
}

function legacyHealth(status: ComputerStatus) {
  return status.ok && status.pyautogui_available ? "ready" : "blocked";
}

function getHealthLabel(
  health: ComputerStatus["health"] | undefined,
  ready: boolean | undefined,
  tc: (source: string) => string,
) {
  if (health === "blocked" || ready === false) return tc("Blocked");
  if (health === "degraded") return tc("Available with limits");
  if (health === "ready" || ready === true) return tc("Ready");
  return tc("Loading");
}

function getCapabilities(status: ComputerStatus): ComputerCapability[] {
  return status.capabilities ?? status.readiness?.capabilities ?? [];
}

function getCriticalBlockers(status: ComputerStatus): ComputerCapability[] {
  return status.critical_blockers ?? status.readiness?.critical_blockers ?? [];
}

function getDegradedCapabilities(status: ComputerStatus): ComputerCapability[] {
  return (
    status.degraded_capabilities ??
    status.readiness?.degraded_capabilities ??
    []
  );
}

function getRecommendedActions(status: ComputerStatus) {
  return (
    status.recommended_actions ?? status.readiness?.recommended_actions ?? []
  );
}

function getActiveAction({
  busy,
  preview,
  previewSecondsLeft,
  plan,
  screenshot,
}: {
  busy: string | null;
  preview: ComputerPreview | null;
  previewSecondsLeft: number | null;
  plan: ComputerActionPlan | null;
  screenshot: ComputerScreenshot | null;
}, tc: (source: string) => string): ActiveAction {
  if (preview) {
    return {
      label: tc("Waiting for confirmation"),
      detail: `${formatActionLabel(preview.action, tc)} · ${preview.risk.reason}${
        previewSecondsLeft !== null
          ? ` · ${previewSecondsLeft}s ${tc("until expiry")}`
          : ""
      }`,
      tone: preview.risk.level === "high" ? "warn" : "active",
    };
  }
  if (busy) {
    const labelMap: Record<string, string> = {
      status: tc("Check connection"),
      capture: tc("Capture screen"),
      ground: tc("Parse actions"),
      vision: tc("Request vision model"),
      plan: tc("Generate plan"),
      preview: tc("Generate confirmation"),
      execute: tc("Execute action"),
      release: tc("Release control"),
      stream: tc("Switch live screen"),
    };
    return {
      label: labelMap[busy] || tc("Working"),
      detail: tc("New computer actions wait until the current action finishes."),
      tone: "active",
    };
  }
  if (plan?.suggestions.length) {
    return {
      label: tc("Candidate actions available"),
      detail: `${plan.suggestions.length} ${tc(
        "candidate actions. Select one to add it to the confirmation queue.",
      )}`,
      tone: "ok",
    };
  }
  if (screenshot?.data_url) {
    return {
      label: tc("Screen observed"),
      detail: tc("Select a point on the screenshot or ask the vision model for the next step."),
      tone: "idle",
    };
  }
  return {
    label: tc("Waiting for a task"),
    detail: tc("Capture the screen or describe what you want the Agent to do."),
    tone: "idle",
  };
}

function formatActionLabel(
  action: Record<string, unknown>,
  tc: (source: string) => string,
) {
  const kind = String(action.action || tc("Action"));
  if (
    (kind === "click" || kind === "move") &&
    action.x != null &&
    action.y != null
  ) {
    const target = getControlIdentity(getMatchedControl(action));
    const coordinate = `${action.x}, ${action.y}`;
    return target
      ? `${tc(kind === "click" ? "Click" : "Move")} ${target} · ${coordinate}`
      : `${tc(kind === "click" ? "Click" : "Move")} ${coordinate}`;
  }
  if (kind === "type") return tc("Type text");
  if (kind === "key")
    return `${tc("Keyboard shortcut")} ${Array.isArray(action.keys) ? action.keys.join("+") : action.keys || ""}`;
  if (kind === "wait") return `${tc("Wait")} ${action.ms || 0}ms`;
  return kind;
}

function ScreenshotActionOverlay({
  cursor,
  imageBox,
  target,
}: {
  cursor: ScreenPoint | null;
  imageBox: ScreenshotImageBox | null;
  target: VisualTarget | null;
}) {
  const { t } = useI18n();
  const tc = (source: string) => t.workspaceComputer[source] ?? source;
  if (!imageBox || (!cursor && !target)) return null;

  const cursorPosition = cursor ? projectScreenPoint(cursor, imageBox) : null;
  const targetPosition = target ? projectScreenPoint(target, imageBox) : null;
  const shouldDrawPath =
    cursorPosition &&
    targetPosition &&
    Math.hypot(
      cursorPosition.left - targetPosition.left,
      cursorPosition.top - targetPosition.top,
    ) > 12;

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {shouldDrawPath ? (
        <svg className="absolute inset-0 size-full" aria-hidden="true">
          <line
            x1={cursorPosition.left}
            y1={cursorPosition.top}
            x2={targetPosition.left}
            y2={targetPosition.top}
            stroke="rgb(16 185 129 / 0.85)"
            strokeDasharray="7 6"
            strokeLinecap="round"
            strokeWidth="2.5"
          />
        </svg>
      ) : null}
      {cursorPosition ? (
        <div
          className="absolute -translate-x-1/2 -translate-y-1/2"
          style={{ left: cursorPosition.left, top: cursorPosition.top }}
        >
          <span className="block size-2.5 rounded-full bg-foreground shadow-[var(--shadow-xs)] ring-2 ring-white dark:bg-white dark:ring-background" />
          <span className="absolute left-3 top-2 whitespace-nowrap rounded-md bg-background/90 px-1.5 py-0.5 text-xs font-medium text-foreground shadow-[var(--shadow-xs)]">
            {tc("Current cursor")}
          </span>
        </div>
      ) : null}
      {target && targetPosition ? (
        <div
          className="absolute -translate-x-1/2 -translate-y-1/2"
          style={{ left: targetPosition.left, top: targetPosition.top }}
        >
          <span
            className={cn(
              "absolute -inset-4 rounded-full border-2 animate-ping",
              target.tone === "preview"
                ? "border-info/80"
                : target.tone === "candidate"
                  ? "border-success/80"
                  : "border-warning/80",
            )}
          />
          <span
            className={cn(
              "relative grid size-8 place-items-center rounded-full text-white shadow-[var(--shadow-md)] ring-2 ring-white/90",
              target.tone === "preview"
                ? "bg-info"
                : target.tone === "candidate"
                  ? "bg-success"
                  : "bg-warning",
            )}
          >
            <MousePointerClickIcon className="size-4" />
          </span>
          <span className="absolute left-6 top-6 max-w-56 truncate whitespace-nowrap rounded-md border border-border bg-background/95 px-2 py-1 text-xs font-medium text-foreground shadow-[var(--shadow-xs)]">
            {target.label}
          </span>
        </div>
      ) : null}
    </div>
  );
}

function getCursorPoint(status: ComputerStatus | null): ScreenPoint | null {
  if (
    typeof status?.screen.cursor_x !== "number" ||
    typeof status.screen.cursor_y !== "number"
  ) {
    return null;
  }
  return { x: status.screen.cursor_x, y: status.screen.cursor_y };
}

function getVisualTarget({
  highlightedAction,
  plan,
  preview,
  selectedPoint,
}: {
  highlightedAction: Record<string, unknown> | null;
  plan: ComputerActionPlan | null;
  preview: ComputerPreview | null;
  selectedPoint: ScreenPoint | null;
}, tc: (source: string) => string): VisualTarget | null {
  if (preview) {
    return getActionVisualTarget(
      preview.action,
      formatActionLabel(preview.action, tc),
      "preview",
    );
  }

  if (highlightedAction) {
    return getActionVisualTarget(
      highlightedAction,
      formatActionLabel(highlightedAction, tc),
      "candidate",
    );
  }

  const firstPointSuggestion = plan?.suggestions.find((item) =>
    getActionPoint(item.action),
  );
  if (firstPointSuggestion) {
    return getActionVisualTarget(
      firstPointSuggestion.action,
      firstPointSuggestion.title,
      "candidate",
    );
  }

  if (selectedPoint) {
    return {
      ...selectedPoint,
      label: tc("Selected point"),
      tone: "selected",
    };
  }

  return null;
}

function getActionVisualTarget(
  action: Record<string, unknown>,
  label: string,
  tone: VisualTarget["tone"],
): VisualTarget | null {
  const point = getActionPoint(action);
  if (!point) return null;
  return {
    ...point,
    label,
    tone,
  };
}

function getActionPoint(
  action: Record<string, unknown> | null | undefined,
): ScreenPoint | null {
  if (!action) return null;
  const kind = String(action.action || "");
  if (kind !== "click" && kind !== "move") return null;
  const x = toFiniteNumber(action.x);
  const y = toFiniteNumber(action.y);
  if (x === null || y === null) return null;
  return { x, y };
}

function projectScreenPoint(point: ScreenPoint, imageBox: ScreenshotImageBox) {
  const x = clamp(point.x, 0, Math.max(0, imageBox.naturalWidth - 1));
  const y = clamp(point.y, 0, Math.max(0, imageBox.naturalHeight - 1));
  return {
    left: imageBox.left + (x / imageBox.naturalWidth) * imageBox.width,
    top: imageBox.top + (y / imageBox.naturalHeight) * imageBox.height,
  };
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function toFiniteNumber(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function MatchedControlSummary({
  action,
}: {
  action: Record<string, unknown>;
}) {
  const { t } = useI18n();
  const tc = (source: string) => t.workspaceComputer[source] ?? source;
  const control = getMatchedControl(action);
  if (!control) return null;

  const identity = getControlIdentity(control) || tc("Unnamed control");
  const typeText = [control.control_type, control.class_name]
    .filter(Boolean)
    .join(" · ");
  const centerText = formatPoint(control.center);
  const scoreText =
    typeof control.score === "number" && Number.isFinite(control.score)
      ? Math.round(control.score)
      : null;

  return (
    <div className="mt-2 rounded-lg border border-success/30 bg-success/5 p-2.5 text-xs leading-5 text-success dark:border-success/60">
      <div className="flex items-start gap-2">
        <ScanSearchIcon className="mt-0.5 size-3.5 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-semibold">
            <span className="break-words">
              {tc("UIA match: ")}
              {identity}
            </span>
            {scoreText !== null ? (
              <span className="rounded-md border border-success/80 px-1.5 py-0.5 font-mono text-xs">
                {scoreText}
              </span>
            ) : null}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-success/80">
            {typeText ? <span>{typeText}</span> : null}
            {centerText ? (
              <span>
                {tc("Center")} {centerText}
              </span>
            ) : null}
            {control.query ? (
              <span>
                {tc("Query")} {control.query}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function getMatchedControl(
  action: Record<string, unknown>,
): ComputerMatchedControl | null {
  const value = action.matched_control;
  if (!isRecord(value)) return null;
  return value as ComputerMatchedControl;
}

function getControlIdentity(control: ComputerMatchedControl | null) {
  if (!control) return "";
  return firstText(control.name, control.automation_id, control.id);
}

function firstText(...values: Array<unknown>) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value))
      return String(value);
  }
  return "";
}

function formatPoint(value: unknown) {
  if (!isRecord(value)) return "";
  const x = formatNumber(value.x);
  const y = formatNumber(value.y);
  return x && y ? `${x}, ${y}` : "";
}

function formatNumber(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return String(Math.round(value));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function DeviceStatePanel({ state }: { state: DeviceState }) {
  const { t } = useI18n();
  const tc = (source: string) => t.workspaceComputer[source] ?? source;
  const toneClass = {
    ok: "border-success/30 bg-success/5 text-success dark:border-success/60",
    warn: "border-warning/30 bg-warning/5 text-warning dark:border-warning/60",
    error: "border-destructive/30 bg-destructive/10 text-destructive",
    loading: "border-border bg-background text-foreground",
  }[state.tone];
  return (
    <div className={cn("rounded-lg border p-4", toneClass)}>
      <div className="flex items-center gap-2 text-sm font-semibold">
        <ActivityIcon className="size-4" />
        {tc("This computer")} · {state.label}
      </div>
      <p className="mt-2 text-xs leading-5 opacity-80">{state.detail}</p>
    </div>
  );
}

function PermissionGuardPanel({
  hasScreenshot,
  hasPreview,
  leaseState,
  onReleaseLease,
  previewSecondsLeft,
  releaseDisabled,
}: {
  hasScreenshot: boolean;
  hasPreview: boolean;
  leaseState: LeaseState;
  onReleaseLease: () => void;
  previewSecondsLeft: number | null;
  releaseDisabled: boolean;
}) {
  const { t } = useI18n();
  const tc = (source: string) => t.workspaceComputer[source] ?? source;
  const rows = [
    {
      label: tc("Observe"),
      value: hasScreenshot
        ? tc("Screen screenshot captured")
        : tc("Only observes the screen when you request it"),
    },
    {
      label: tc("Confirm"),
      value: hasPreview
        ? tc("An action is awaiting confirmation")
        : tc("Mouse and keyboard actions never run automatically"),
    },
    {
      label: tc("Expiry"),
      value:
        previewSecondsLeft !== null
          ? `${previewSecondsLeft}s ${tc("until automatic removal")}`
          : tc("Confirmation tokens are short-lived"),
    },
    {
      label: tc("Control"),
      value: leaseState.detail,
    },
  ];
  return (
    <div className="rounded-lg border border-border bg-background/70 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <ShieldCheckIcon className="size-4 text-success" />
          {tc("Permission guard")}
        </div>
        {leaseState.canRelease ? (
          <Button
            size="sm"
            variant="outline"
            onClick={onReleaseLease}
            disabled={releaseDisabled}
          >
            {tc("Release control")}
          </Button>
        ) : null}
      </div>
      <div className="mt-3 grid gap-2">
        {rows.map((row) => (
          <div
            key={row.label}
            className="grid grid-cols-[3.5rem_1fr] gap-2 text-xs leading-5"
          >
            <span className="text-muted-foreground">{row.label}</span>
            <span className="font-medium">{row.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CurrentActionPanel({ action }: { action: ActiveAction }) {
  const { t } = useI18n();
  const tc = (source: string) => t.workspaceComputer[source] ?? source;
  const toneClass = {
    idle: "border-border bg-background/70",
    active:
      "border-info/30 bg-info/10 text-info",
    warn: "border-warning/30 bg-warning/5 text-warning dark:border-warning/60",
    ok: "border-success/30 bg-success/5 text-success dark:border-success/60",
  }[action.tone];
  return (
    <div className={cn("rounded-lg border p-4", toneClass)}>
      <div className="flex items-center gap-2 text-sm font-semibold">
        <MousePointerClickIcon className="size-4" />
        {tc("Current action")} · {action.label}
      </div>
      <p className="mt-2 text-xs leading-5 opacity-80">{action.detail}</p>
    </div>
  );
}

function ControlSessionPanel({
  evidence,
  indicator,
  session,
}: {
  evidence: ControlEvidence[];
  indicator: {
    mode: ControlIndicatorMode;
    detail?: Record<string, unknown>;
    updatedAt: number;
  };
  session: ControlSessionOptions;
}) {
  const { t } = useI18n();
  const tc = (source: string) => t.workspaceComputer[source] ?? source;
  const toneClass = {
    idle: "border-border bg-background/70",
    action:
      "border-info/30 bg-info/10 text-info",
    paused:
      "border-warning/30 bg-warning/5 text-warning dark:border-warning/60",
  }[indicator.mode];
  const action =
    typeof indicator.detail?.action === "string"
      ? indicator.detail.action
      : null;
  const latest = evidence.slice(0, 3);

  return (
    <div className={cn("rounded-lg border p-4", toneClass)}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <ShieldCheckIcon className="size-4" />
          {tc("Control session")} · {formatControlMode(indicator.mode, tc)}
        </div>
        <span className="rounded-md border border-current/20 px-1.5 py-0.5 font-mono text-xs">
          {session.surface || "surface"}
        </span>
      </div>
      <div className="mt-2 grid gap-1 text-xs leading-5 opacity-85">
        <div className="flex justify-between gap-3">
          <span>{tc("Owner")}</span>
          <span className="truncate font-medium">
            {session.ownerLabel || tc("Local computer automation")}
          </span>
        </div>
        <div className="flex justify-between gap-3">
          <span>{tc("Target")}</span>
          <span className="truncate font-medium">
            {session.targetId != null ? String(session.targetId) : "local-pc"}
          </span>
        </div>
        <div className="flex justify-between gap-3">
          <span>{tc("Action")}</span>
          <span className="truncate font-medium">{action || "-"}</span>
        </div>
      </div>
      {latest.length ? (
        <div className="mt-3 space-y-1.5">
          {latest.map((item) => (
            <div
              key={item.id || `${item.kind}-${item.at}`}
              className="rounded-lg border border-current/15 bg-background/45 px-2 py-1.5 text-xs leading-4 text-foreground"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{item.action || item.kind}</span>
                <span
                  className={cn(
                    item.ok === false
                      ? "text-destructive"
                      : item.ok === true
                        ? "text-success"
                        : "text-muted-foreground",
                  )}
                >
                  {item.ok === false ? "failed" : item.ok === true ? "ok" : ""}
                </span>
              </div>
              {item.summary ? (
                <div className="mt-0.5 truncate text-muted-foreground">
                  {item.summary}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 text-xs leading-5 opacity-80">
          {tc("Previews, executions, and screenshots are retained as control evidence.")}
        </p>
      )}
    </div>
  );
}

function formatControlMode(
  mode: ControlIndicatorMode,
  tc: (source: string) => string,
) {
  if (mode === "action") return tc("Running");
  if (mode === "paused") return tc("Paused");
  return tc("Idle");
}

// Token-TTL countdown chip. Color shifts from neutral → amber as the
// 90 s window narrows so the operator notices before the server-side
// token 404s. ``secondsLeft === 0`` is rendered explicitly because
// the auto-clear effect only runs after the next render cycle, so the
// UI may briefly show the expired state.
function CountdownChip({ secondsLeft }: { secondsLeft: number }) {
  const { t } = useI18n();
  const tc = (source: string) => t.workspaceComputer[source] ?? source;
  const tone =
    secondsLeft === 0
      ? "border-destructive/40 bg-destructive/10 text-destructive"
      : secondsLeft <= 15
        ? "border-warning/40 bg-warning/5 text-warning"
        : "border-border bg-background text-muted-foreground";
  const label = secondsLeft === 0 ? tc("Expired") : `${secondsLeft}s`;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-mono",
        tone,
      )}
      aria-live="polite"
    >
      <ClockIcon className="size-3" />
      {label}
    </span>
  );
}

function summarizeResult(data: ComputerExecuteResult) {
  const result = data.result || {};
  if (typeof result.error === "string") return result.error;
  return JSON.stringify(result);
}
