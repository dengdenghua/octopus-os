/**
 * TimelineView -- horizontal timeline/Gantt visualization of tasks.
 *
 * SVG-based horizontal timeline showing tasks as coloured rectangles.
 *   - X-axis: time (auto-scales: minutes / hours / days)
 *   - Y-axis: stacked task bars
 *   - Running tasks have an animated pulsing right edge
 *   - Hover shows task details tooltip
 *   - Zoom in/out on timeline via buttons
 */

import { ClockIcon, Loader2Icon, ZoomInIcon, ZoomOutIcon } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type {
  TaskBoardTimelineResponse,
  TimelineTask,
} from "@/core/task-board/types";

import { useI18n } from "@/core/i18n/hooks";
import type { TaskType } from "@/core/task-board/types";
import { formatDurationMs } from "./task-card";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BAR_HEIGHT = 28;
const BAR_GAP = 6;
const ROW_HEIGHT = BAR_HEIGHT + BAR_GAP;
const LABEL_WIDTH = 160;
const PADDING_TOP = 40;
const PADDING_BOTTOM = 24;
const PADDING_RIGHT = 16;
const MIN_BAR_WIDTH = 4;

const STATUS_COLORS: Record<string, { fill: string; stroke: string }> = {
  queued: { fill: "#94a3b8", stroke: "#64748b" },
  running: { fill: "#f59e0b", stroke: "#d97706" },
  paused: { fill: "#fbbf24", stroke: "#f59e0b" },
  completed: { fill: "#10b981", stroke: "#059669" },
  failed: { fill: "#ef4444", stroke: "#dc2626" },
  cancelled: { fill: "#9ca3af", stroke: "#6b7280" },
};

const TYPE_ICON_EMOJI: Record<string, string> = {
  background: "\u26A1",
  quest: "\uD83D\uDE80",
  scheduled: "\u23F0",
};

// ---------------------------------------------------------------------------
// Time axis helpers
// ---------------------------------------------------------------------------

interface TickMark {
  ms: number;
  label: string;
  isMajor: boolean;
}

function computeTicks(
  startMs: number,
  endMs: number,
  _viewWidth: number,
  locale: string,
): TickMark[] {
  const rangeMs = endMs - startMs;
  if (rangeMs <= 0) return [];

  // Choose tick interval based on range
  const intervals = [
    {
      thresholdMs: 5 * 60_000,
      intervalMs: 60_000,
      majorEvery: 5,
      format: "mm:ss",
    },
    {
      thresholdMs: 30 * 60_000,
      intervalMs: 5 * 60_000,
      majorEvery: 6,
      format: "HH:mm",
    },
    {
      thresholdMs: 2 * 3_600_000,
      intervalMs: 15 * 60_000,
      majorEvery: 4,
      format: "HH:mm",
    },
    {
      thresholdMs: 12 * 3_600_000,
      intervalMs: 3_600_000,
      majorEvery: 3,
      format: "HH:mm",
    },
    {
      thresholdMs: 48 * 3_600_000,
      intervalMs: 6 * 3_600_000,
      majorEvery: 4,
      format: "HH:mm",
    },
    {
      thresholdMs: Infinity,
      intervalMs: 24 * 3_600_000,
      majorEvery: 1,
      format: "MMM dd",
    },
  ];

  const cfg =
    intervals.find((i) => rangeMs <= i.thresholdMs) ??
    intervals[intervals.length - 1]!;

  const ticks: TickMark[] = [];
  const firstTick = Math.ceil(startMs / cfg.intervalMs) * cfg.intervalMs;
  let tickIndex = 0;

  for (let ms = firstTick; ms <= endMs; ms += cfg.intervalMs) {
    const d = new Date(ms);
    let label: string;

    if (cfg.format === "mm:ss") {
      label = `${d.getMinutes().toString().padStart(2, "0")}:${d.getSeconds().toString().padStart(2, "0")}`;
    } else if (cfg.format === "HH:mm") {
      label = `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
    } else {
      label = new Intl.DateTimeFormat(locale, {
        month: "short",
        day: "numeric",
      }).format(d);
    }

    ticks.push({
      ms,
      label,
      isMajor: tickIndex % cfg.majorEvery === 0,
    });
    tickIndex++;
  }

  return ticks;
}

// ---------------------------------------------------------------------------
// Tooltip state
// ---------------------------------------------------------------------------

interface TooltipState {
  task: TimelineTask;
  x: number;
  y: number;
}

// ---------------------------------------------------------------------------
// TimelineView
// ---------------------------------------------------------------------------

export function TimelineView({
  data,
  loading,
}: {
  data: TaskBoardTimelineResponse;
  loading?: boolean;
}) {
  const { locale, t } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const [_scrollLeft, setScrollLeft] = useState(0);

  const TYPE_LABELS: Record<TaskType, string> = {
    background: t.taskBoard.background,
    quest: t.taskBoard.quest,
    scheduled: t.taskBoard.scheduled,
    intelligence: t.taskBoard.intelligence,
  };

  const handleZoomIn = useCallback(
    () => setZoom((z) => Math.min(z * 1.5, 8)),
    [],
  );
  const handleZoomOut = useCallback(
    () => setZoom((z) => Math.max(z / 1.5, 0.5)),
    [],
  );
  const handleZoomReset = useCallback(() => setZoom(1), []);

  const tasks = data.tasks;

  // Compute layout
  const layout = useMemo(() => {
    if (tasks.length === 0) return null;

    const earliest = data.earliest_ms;
    const latest = data.latest_ms;
    const rangeMs = Math.max(latest - earliest, 60_000); // minimum 1 minute range
    const padding = rangeMs * 0.05; // 5% padding on each side

    const startMs = earliest - padding;
    const endMs = latest + padding;

    const baseWidth = 800;
    const timelineWidth = Math.max(baseWidth * zoom, 400);
    const totalWidth = LABEL_WIDTH + timelineWidth + PADDING_RIGHT;
    const totalHeight =
      PADDING_TOP + tasks.length * ROW_HEIGHT + PADDING_BOTTOM;

    return {
      startMs,
      endMs,
      rangeMs: endMs - startMs,
      timelineWidth,
      totalWidth,
      totalHeight,
    };
  }, [tasks, data, zoom]);

  const ticks = useMemo(() => {
    if (!layout) return [];
    return computeTicks(
      layout.startMs,
      layout.endMs,
      layout.timelineWidth,
      locale,
    );
  }, [layout, locale]);

  const STATUS_LABELS: Record<string, string> = {
    queued: t.taskBoard.queued,
    running: t.taskBoard.running,
    paused: t.taskBoard.paused,
    completed: t.taskBoard.completed,
    failed: t.taskBoard.failed,
    cancelled: t.taskBoard.cancelled,
  };

  const msToX = useCallback(
    (ms: number) => {
      if (!layout) return 0;
      return (
        LABEL_WIDTH +
        ((ms - layout.startMs) / layout.rangeMs) * layout.timelineWidth
      );
    },
    [layout],
  );

  if (loading) {
    return (
      <div role="status" className="flex items-center justify-center py-20">
        <Loader2Icon className="size-5 animate-spin text-muted-foreground" />
        <span className="sr-only">{t.common.loading}</span>
      </div>
    );
  }

  if (tasks.length === 0 || !layout) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <ClockIcon className="size-8 mb-2 opacity-40" />
        <p className="text-sm">{t.taskBoard.noTimelineTasks}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Zoom controls */}
      <div className="flex items-center justify-end gap-1">
        <Button
          variant="outline"
          size="icon"
          className="size-7"
          onClick={handleZoomOut}
          title={t.taskBoard.zoomOut}
          aria-label={t.taskBoard.zoomOut}
        >
          <ZoomOutIcon className="size-3.5" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7 px-2 text-xs tabular-nums"
          onClick={handleZoomReset}
          aria-label={t.taskBoard.zoomReset(Math.round(zoom * 100))}
        >
          {Math.round(zoom * 100)}%
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="size-7"
          onClick={handleZoomIn}
          title={t.taskBoard.zoomIn}
          aria-label={t.taskBoard.zoomIn}
        >
          <ZoomInIcon className="size-3.5" />
        </Button>
      </div>

      {/* Timeline container */}
      <div
        ref={containerRef}
        className="relative overflow-x-auto max-w-full overflow-y-auto rounded-lg border bg-card"
        style={{ maxHeight: "480px" }}
        onScroll={(e) => setScrollLeft((e.target as HTMLDivElement).scrollLeft)}
      >
        <svg
          width={layout.totalWidth}
          height={layout.totalHeight}
          className="select-none"
          role="img"
          aria-label={t.taskBoard.timelineChart}
        >
          {/* Defs for animations & gradients */}
          <defs>
            {/* Pulse animation for running tasks */}
            <linearGradient id="runningPulse" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="#f59e0b" stopOpacity="1" />
              <stop offset="85%" stopColor="#f59e0b" stopOpacity="1" />
              <stop offset="100%" stopColor="#f59e0b" stopOpacity="0.3">
                <animate
                  attributeName="stopOpacity"
                  values="0.3;0.8;0.3"
                  dur="1.5s"
                  repeatCount="indefinite"
                />
              </stop>
            </linearGradient>

            {/* Striped pattern for running tasks */}
            <pattern
              id="runningStripes"
              width="8"
              height="8"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width="4" height="8" fill="#f59e0b" opacity="0.8" />
              <rect x="4" width="4" height="8" fill="#fbbf24" opacity="0.6" />
              <animateTransform
                attributeName="patternTransform"
                type="translate"
                from="0 0"
                to="8 0"
                dur="0.8s"
                repeatCount="indefinite"
                additive="sum"
              />
            </pattern>
          </defs>

          {/* Background grid */}
          <g>
            {ticks.map((tick, i) => {
              const x = msToX(tick.ms);
              return (
                <line
                  key={i}
                  x1={x}
                  y1={PADDING_TOP - 4}
                  x2={x}
                  y2={layout.totalHeight - PADDING_BOTTOM}
                  stroke={tick.isMajor ? "currentColor" : "currentColor"}
                  strokeOpacity={tick.isMajor ? 0.12 : 0.06}
                  strokeWidth={1}
                  strokeDasharray={tick.isMajor ? undefined : "2,4"}
                />
              );
            })}
          </g>

          {/* Time axis labels */}
          <g>
            {ticks
              .filter((t) => t.isMajor)
              .map((tick, i) => {
                const x = msToX(tick.ms);
                return (
                  <text
                    key={i}
                    x={x}
                    y={PADDING_TOP - 10}
                    textAnchor="middle"
                    className="fill-muted-foreground text-xs"
                    style={{ fontSize: "10px" }}
                  >
                    {tick.label}
                  </text>
                );
              })}
          </g>

          {/* Task rows */}
          {tasks.map((task, rowIndex) => {
            const y = PADDING_TOP + rowIndex * ROW_HEIGHT;
            const x1 = msToX(task.start_ms);
            const x2 = msToX(task.end_ms);
            const barWidth = Math.max(x2 - x1, MIN_BAR_WIDTH);
            const colors = STATUS_COLORS[task.status] ?? STATUS_COLORS.queued!;

            return (
              <g
                key={task.id}
                className="cursor-pointer"
                onMouseEnter={(e) => {
                  const rect = containerRef.current?.getBoundingClientRect();
                  if (rect) {
                    setTooltip({
                      task,
                      x:
                        e.clientX -
                        rect.left +
                        (containerRef.current?.scrollLeft ?? 0),
                      y:
                        e.clientY -
                        rect.top +
                        (containerRef.current?.scrollTop ?? 0),
                    });
                  }
                }}
                onMouseLeave={() => setTooltip(null)}
              >
                <title>{`${task.name} · ${STATUS_LABELS[task.status] ?? task.status} · ${formatDurationMs(task.duration_ms)}`}</title>
                {/* Row background on hover */}
                <rect
                  x={0}
                  y={y - 1}
                  width={layout.totalWidth}
                  height={ROW_HEIGHT}
                  fill="currentColor"
                  opacity={0}
                  className="transition-opacity hover:opacity-[0.02]"
                />

                {/* Task label */}
                <text
                  x={8}
                  y={y + BAR_HEIGHT / 2 + 1}
                  dominantBaseline="middle"
                  className="fill-foreground text-xs"
                  style={{ fontSize: "11px" }}
                >
                  <tspan className="fill-muted-foreground">
                    {TYPE_ICON_EMOJI[task.type] ?? ""}{" "}
                  </tspan>
                  {task.name.length > 18
                    ? task.name.slice(0, 18) + "..."
                    : task.name}
                </text>

                {/* Task bar */}
                <rect
                  x={x1}
                  y={y + 2}
                  width={barWidth}
                  height={BAR_HEIGHT - 4}
                  rx={4}
                  ry={4}
                  fill={task.is_running ? "url(#runningStripes)" : colors.fill}
                  stroke={colors.stroke}
                  strokeWidth={1}
                  opacity={0.85}
                  className="transition-opacity hover:opacity-100"
                />

                {/* Duration label inside bar (if wide enough) */}
                {barWidth > 50 && (
                  <text
                    x={x1 + barWidth / 2}
                    y={y + BAR_HEIGHT / 2 + 1}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fill="white"
                    style={{ fontSize: "10px", fontWeight: 500 }}
                  >
                    {formatDurationMs(task.duration_ms)}
                  </text>
                )}

                {/* Pulsing right edge for running tasks */}
                {task.is_running && (
                  <rect
                    x={x1 + barWidth - 3}
                    y={y + 2}
                    width={6}
                    height={BAR_HEIGHT - 4}
                    rx={3}
                    fill="#f59e0b"
                    opacity={0.9}
                  >
                    <animate
                      attributeName="opacity"
                      values="0.4;1;0.4"
                      dur="1.2s"
                      repeatCount="indefinite"
                    />
                    <animate
                      attributeName="width"
                      values="4;8;4"
                      dur="1.2s"
                      repeatCount="indefinite"
                    />
                  </rect>
                )}
              </g>
            );
          })}
        </svg>

        {/* Tooltip overlay */}
        {tooltip && (
          <div
            className="pointer-events-none absolute z-50 rounded-lg border bg-popover px-3 py-2 shadow-[var(--shadow-md)]"
            style={{
              left: Math.min(tooltip.x + 12, layout.totalWidth - 200),
              top: tooltip.y - 60,
            }}
          >
            <p className="text-sm font-medium">{tooltip.task.name}</p>
            <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              <p>
                <span className="font-medium text-foreground">
                  {TYPE_LABELS[tooltip.task.type]}
                </span>
                {" -- "}
                {STATUS_LABELS[tooltip.task.status] ?? tooltip.task.status}
              </p>
              <p>
                {t.taskBoard.duration}:{" "}
                {formatDurationMs(tooltip.task.duration_ms)}
              </p>
              {tooltip.task.is_running && (
                <p className="text-warning font-medium">
                  {t.taskBoard.inProgress}
                </p>
              )}
            </div>
          </div>
        )}
      </div>
      <ul className="sr-only">
        {tasks.map((task) => (
          <li key={task.id}>
            {task.name}, {TYPE_LABELS[task.type]},{" "}
            {STATUS_LABELS[task.status] ?? task.status},{" "}
            {formatDurationMs(task.duration_ms)}
          </li>
        ))}
      </ul>
    </div>
  );
}
