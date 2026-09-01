/**
 * LineChart -- a lightweight SVG line/area chart with hover tooltips.
 *
 * Supports multiple data series, stacked area mode, axis labels,
 * and an interactive tooltip that follows the cursor.  Zero external
 * dependencies beyond React.
 */

import { useCallback, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ChartSeries {
  /** Display name shown in the legend / tooltip. */
  label: string;
  /** Data values -- one per data-point along the X axis. */
  data: number[];
  /** Line / fill colour. */
  color: string;
  /** Render as a bar series instead of a line. */
  type?: "line" | "area" | "bar";
}

export interface LineChartProps {
  /** X-axis labels (same length as each series' `data`). */
  labels: string[];
  /** One or more series to render. */
  series: ChartSeries[];
  /** Width of the chart SVG. */
  width?: number;
  /** Height of the chart SVG. */
  height?: number;
  /** Extra class names on the root wrapper. */
  className?: string;
  /** Y-axis label (e.g. "% Success"). */
  yLabel?: string;
  /** Format function for Y-axis tick labels. */
  yFormat?: (v: number) => string;
  /** Format function for tooltip values. */
  tooltipFormat?: (v: number, label: string) => string;
  /** Whether Y-axis values should be inverted (lower = better). */
  invertY?: boolean;
}

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------

const PAD_LEFT = 48;
const PAD_RIGHT = 16;
const PAD_TOP = 16;
const PAD_BOTTOM = 40;
const TICK_COUNT = 5;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function LineChart({
  labels,
  series,
  width = 600,
  height = 280,
  className,
  yLabel,
  yFormat = (v) => String(Math.round(v)),
  tooltipFormat,
  invertY: _invertY = false,
}: LineChartProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(
    null,
  );

  // Chart area
  const chartW = width - PAD_LEFT - PAD_RIGHT;
  const chartH = height - PAD_TOP - PAD_BOTTOM;

  // Compute global Y range
  const { yMin, yMax } = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of series) {
      for (const v of s.data) {
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    if (!isFinite(lo)) lo = 0;
    if (!isFinite(hi)) hi = 1;
    if (lo === hi) {
      lo = lo - 1;
      hi = hi + 1;
    }
    // Add 10% padding
    const range = hi - lo;
    return { yMin: Math.max(0, lo - range * 0.05), yMax: hi + range * 0.1 };
  }, [series]);

  // Map data -> SVG coords
  const toX = useCallback(
    (i: number) => PAD_LEFT + (i / Math.max(labels.length - 1, 1)) * chartW,
    [labels.length, chartW],
  );

  const toY = useCallback(
    (v: number) => {
      const normalized = (v - yMin) / (yMax - yMin);
      return PAD_TOP + chartH * (1 - normalized);
    },
    [yMin, yMax, chartH],
  );

  // Y-axis ticks
  const yTicks = useMemo(() => {
    const step = (yMax - yMin) / TICK_COUNT;
    return Array.from({ length: TICK_COUNT + 1 }, (_, i) => yMin + step * i);
  }, [yMin, yMax]);

  // X-axis label thinning
  const xLabelStep = useMemo(() => {
    if (labels.length <= 12) return 1;
    return Math.ceil(labels.length / 10);
  }, [labels.length]);

  // Build polyline points for each line series
  const linePoints = useMemo(() => {
    return series.map((s) => {
      if (s.type === "bar") return "";
      return s.data
        .map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`)
        .join(" ");
    });
  }, [series, toX, toY]);

  // Build area paths
  const areaPaths = useMemo(() => {
    return series.map((s) => {
      if (s.type !== "area") return "";
      const pts = s.data.map((v, i) => ({ x: toX(i), y: toY(v) }));
      if (pts.length < 2) return "";

      let d = `M ${pts[0]!.x.toFixed(1)} ${pts[0]!.y.toFixed(1)}`;
      for (let i = 1; i < pts.length; i++) {
        d += ` L ${pts[i]!.x.toFixed(1)} ${pts[i]!.y.toFixed(1)}`;
      }
      const baseY = toY(yMin);
      d += ` L ${pts[pts.length - 1]!.x.toFixed(1)} ${baseY.toFixed(1)}`;
      d += ` L ${pts[0]!.x.toFixed(1)} ${baseY.toFixed(1)} Z`;
      return d;
    });
  }, [series, toX, toY, yMin]);

  // Bar width
  const barWidth = useMemo(() => {
    const barSeries = series.filter((s) => s.type === "bar");
    if (barSeries.length === 0) return 0;
    const gap = chartW / Math.max(labels.length, 1);
    return Math.min(gap * 0.6, 24);
  }, [series, labels.length, chartW]);

  // Mouse tracking
  const handleMouseMove = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;

      // Find closest data index
      const dataX = mx - PAD_LEFT;
      if (
        dataX < 0 ||
        dataX > chartW ||
        my < PAD_TOP ||
        my > PAD_TOP + chartH
      ) {
        setHoverIdx(null);
        setTooltipPos(null);
        return;
      }
      const idx = Math.round((dataX / chartW) * (labels.length - 1));
      const clamped = Math.max(0, Math.min(labels.length - 1, idx));
      setHoverIdx(clamped);
      setTooltipPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    },
    [chartW, chartH, labels.length],
  );

  const handleMouseLeave = useCallback(() => {
    setHoverIdx(null);
    setTooltipPos(null);
  }, []);

  return (
    <div className={cn("relative select-none", className)}>
      <svg
        ref={svgRef}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto"
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
      >
        {/* Grid lines */}
        {yTicks.map((tick, i) => {
          const y = toY(tick);
          return (
            <g key={`yt-${i}`}>
              <line
                x1={PAD_LEFT}
                y1={y}
                x2={width - PAD_RIGHT}
                y2={y}
                className="stroke-border"
                strokeWidth={0.5}
                strokeDasharray={i === 0 ? "none" : "3,3"}
                opacity={0.5}
              />
              <text
                x={PAD_LEFT - 6}
                y={y + 3}
                textAnchor="end"
                className="fill-muted-foreground"
                fontSize={10}
              >
                {yFormat(tick)}
              </text>
            </g>
          );
        })}

        {/* Y label */}
        {yLabel && (
          <text
            x={12}
            y={PAD_TOP + chartH / 2}
            textAnchor="middle"
            className="fill-muted-foreground"
            fontSize={10}
            transform={`rotate(-90, 12, ${PAD_TOP + chartH / 2})`}
          >
            {yLabel}
          </text>
        )}

        {/* X-axis labels */}
        {labels.map((label, i) => {
          if (i % xLabelStep !== 0 && i !== labels.length - 1) return null;
          const x = toX(i);
          return (
            <text
              key={`xl-${i}`}
              x={x}
              y={height - 8}
              textAnchor="middle"
              className="fill-muted-foreground"
              fontSize={10}
            >
              {label}
            </text>
          );
        })}

        {/* Area fills */}
        {areaPaths.map((d, i) =>
          d ? (
            <path
              key={`area-${i}`}
              d={d}
              fill={series[i]!.color}
              opacity={0.1}
            />
          ) : null,
        )}

        {/* Bar series */}
        {series.map((s, si) =>
          s.type === "bar"
            ? s.data.map((v, di) => {
                const x = toX(di) - barWidth / 2;
                const y = toY(v);
                const h = toY(yMin) - y;
                return (
                  <rect
                    key={`bar-${si}-${di}`}
                    x={x}
                    y={y}
                    width={barWidth}
                    height={Math.max(0, h)}
                    fill={s.color}
                    opacity={hoverIdx === di ? 0.9 : 0.6}
                    rx={2}
                  />
                );
              })
            : null,
        )}

        {/* Line series */}
        {linePoints.map((pts, i) =>
          pts ? (
            <polyline
              key={`line-${i}`}
              points={pts}
              fill="none"
              stroke={series[i]!.color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null,
        )}

        {/* Hover indicator line */}
        {hoverIdx !== null && (
          <line
            x1={toX(hoverIdx)}
            y1={PAD_TOP}
            x2={toX(hoverIdx)}
            y2={PAD_TOP + chartH}
            className="stroke-foreground"
            strokeWidth={1}
            opacity={0.2}
            strokeDasharray="3,2"
          />
        )}

        {/* Hover dots on lines */}
        {hoverIdx !== null &&
          series.map((s, si) =>
            s.type !== "bar" && s.data[hoverIdx] !== undefined ? (
              <circle
                key={`dot-${si}`}
                cx={toX(hoverIdx)}
                cy={toY(s.data[hoverIdx])}
                r={4}
                fill={s.color}
                stroke="var(--background)"
                strokeWidth={2}
              />
            ) : null,
          )}
      </svg>

      {/* Tooltip */}
      {hoverIdx !== null && tooltipPos && (
        <div
          className="pointer-events-none absolute z-50 rounded-lg border bg-popover px-3 py-2 shadow-[var(--shadow-md)]"
          style={{
            left: Math.min(tooltipPos.x + 12, width - 180),
            top: Math.max(tooltipPos.y - 10, 0),
          }}
        >
          <div className="mb-1 text-xs font-medium text-foreground">
            {labels[hoverIdx]}
          </div>
          {series.map((s, si) => (
            <div
              key={si}
              className="flex items-center gap-2 text-xs text-muted-foreground"
            >
              <span
                className="inline-block size-2 rounded-lg"
                style={{ backgroundColor: s.color }}
              />
              <span>{s.label}:</span>
              <span className="font-medium text-foreground">
                {tooltipFormat
                  ? tooltipFormat(s.data[hoverIdx]!, s.label)
                  : yFormat(s.data[hoverIdx]!)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Legend */}
      <div className="mt-2 flex flex-wrap items-center justify-center gap-4">
        {series.map((s, i) => (
          <div
            key={i}
            className="flex items-center gap-1.5 text-xs text-muted-foreground"
          >
            <span
              className="inline-block size-2.5 rounded-lg"
              style={{ backgroundColor: s.color }}
            />
            <span>{s.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// StackedAreaChart -- a specialised variant for memory growth
// ---------------------------------------------------------------------------

export interface StackedAreaChartProps {
  labels: string[];
  series: { label: string; data: number[]; color: string }[];
  width?: number;
  height?: number;
  className?: string;
  yFormat?: (v: number) => string;
}

export function StackedAreaChart({
  labels,
  series,
  width = 600,
  height = 260,
  className,
  yFormat = (v) => String(Math.round(v)),
}: StackedAreaChartProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(
    null,
  );

  const chartW = width - PAD_LEFT - PAD_RIGHT;
  const chartH = height - PAD_TOP - PAD_BOTTOM;

  // Compute stacked data
  const { stacked, yMax } = useMemo(() => {
    const _n = labels.length;
    const layers: number[][] = series.map((s) => [...s.data]);

    // Cumulative stacking
    const cum: number[][] = [];
    for (let si = 0; si < layers.length; si++) {
      cum.push(
        layers[si]!.map((v, di) => {
          let total = v;
          for (let prev = 0; prev < si; prev++) total += layers[prev]![di]!;
          return total;
        }),
      );
    }

    let maxVal = 0;
    for (const layer of cum) {
      for (const v of layer) {
        if (v > maxVal) maxVal = v;
      }
    }
    if (maxVal === 0) maxVal = 1;

    return { stacked: cum, yMax: maxVal * 1.1 };
  }, [series, labels.length]);

  const toX = useCallback(
    (i: number) => PAD_LEFT + (i / Math.max(labels.length - 1, 1)) * chartW,
    [labels.length, chartW],
  );

  const toY = useCallback(
    (v: number) => PAD_TOP + chartH * (1 - v / yMax),
    [yMax, chartH],
  );

  // Build area paths (bottom to top)
  const areaPaths = useMemo(() => {
    const paths: string[] = [];
    for (let si = series.length - 1; si >= 0; si--) {
      const upper = stacked[si]!;
      const lower = si > 0 ? stacked[si - 1]! : upper.map(() => 0);

      let d = `M ${toX(0).toFixed(1)} ${toY(upper[0]!).toFixed(1)}`;
      for (let i = 1; i < upper.length; i++) {
        d += ` L ${toX(i).toFixed(1)} ${toY(upper[i]!).toFixed(1)}`;
      }
      // Bottom line (reversed)
      for (let i = lower.length - 1; i >= 0; i--) {
        d += ` L ${toX(i).toFixed(1)} ${toY(lower[i]!).toFixed(1)}`;
      }
      d += " Z";
      paths[si] = d;
    }
    return paths;
  }, [series, stacked, toX, toY]);

  // Y ticks
  const yTicks = useMemo(() => {
    const step = yMax / TICK_COUNT;
    return Array.from({ length: TICK_COUNT + 1 }, (_, i) => step * i);
  }, [yMax]);

  const xLabelStep = useMemo(() => {
    if (labels.length <= 12) return 1;
    return Math.ceil(labels.length / 10);
  }, [labels.length]);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const dataX = mx - PAD_LEFT;
      if (
        dataX < 0 ||
        dataX > chartW ||
        my < PAD_TOP ||
        my > PAD_TOP + chartH
      ) {
        setHoverIdx(null);
        setTooltipPos(null);
        return;
      }
      const idx = Math.round((dataX / chartW) * (labels.length - 1));
      setHoverIdx(Math.max(0, Math.min(labels.length - 1, idx)));
      setTooltipPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    },
    [chartW, chartH, labels.length],
  );

  return (
    <div className={cn("relative select-none", className)}>
      <svg
        ref={svgRef}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => {
          setHoverIdx(null);
          setTooltipPos(null);
        }}
      >
        {/* Grid */}
        {yTicks.map((tick, i) => {
          const y = toY(tick);
          return (
            <g key={`yt-${i}`}>
              <line
                x1={PAD_LEFT}
                y1={y}
                x2={width - PAD_RIGHT}
                y2={y}
                className="stroke-border"
                strokeWidth={0.5}
                strokeDasharray={i === 0 ? "none" : "3,3"}
                opacity={0.5}
              />
              <text
                x={PAD_LEFT - 6}
                y={y + 3}
                textAnchor="end"
                className="fill-muted-foreground"
                fontSize={10}
              >
                {yFormat(tick)}
              </text>
            </g>
          );
        })}

        {/* X labels */}
        {labels.map((label, i) => {
          if (i % xLabelStep !== 0 && i !== labels.length - 1) return null;
          return (
            <text
              key={`xl-${i}`}
              x={toX(i)}
              y={height - 8}
              textAnchor="middle"
              className="fill-muted-foreground"
              fontSize={10}
            >
              {label.length > 6 ? label.slice(5) : label}
            </text>
          );
        })}

        {/* Stacked areas (render top to bottom for correct layering) */}
        {areaPaths.map((d, i) => (
          <path key={`sa-${i}`} d={d} fill={series[i]!.color} opacity={0.55} />
        ))}

        {/* Hover line */}
        {hoverIdx !== null && (
          <line
            x1={toX(hoverIdx)}
            y1={PAD_TOP}
            x2={toX(hoverIdx)}
            y2={PAD_TOP + chartH}
            className="stroke-foreground"
            strokeWidth={1}
            opacity={0.2}
            strokeDasharray="3,2"
          />
        )}
      </svg>

      {/* Tooltip */}
      {hoverIdx !== null && tooltipPos && (
        <div
          className="pointer-events-none absolute z-50 rounded-lg border bg-popover px-3 py-2 shadow-[var(--shadow-md)]"
          style={{
            left: Math.min(tooltipPos.x + 12, width - 180),
            top: Math.max(tooltipPos.y - 10, 0),
          }}
        >
          <div className="mb-1 text-xs font-medium text-foreground">
            {labels[hoverIdx]}
          </div>
          {series.map((s, si) => (
            <div
              key={si}
              className="flex items-center gap-2 text-xs text-muted-foreground"
            >
              <span
                className="inline-block size-2 rounded-lg"
                style={{ backgroundColor: s.color }}
              />
              <span>{s.label}:</span>
              <span className="font-medium text-foreground">
                {s.data[hoverIdx]}
              </span>
            </div>
          ))}
          <div className="mt-1 border-t pt-1 text-xs font-medium text-foreground">
            Total: {series.reduce((sum, s) => sum + (s.data[hoverIdx] ?? 0), 0)}
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="mt-2 flex flex-wrap items-center justify-center gap-4">
        {series.map((s, i) => (
          <div
            key={i}
            className="flex items-center gap-1.5 text-xs text-muted-foreground"
          >
            <span
              className="inline-block size-2.5 rounded-lg"
              style={{ backgroundColor: s.color }}
            />
            <span>{s.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
