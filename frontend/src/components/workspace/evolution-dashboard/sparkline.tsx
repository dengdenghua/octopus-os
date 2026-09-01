/**
 * Sparkline -- a tiny inline SVG chart for metric cards.
 *
 * Renders a smooth polyline (with optional area fill) from an array
 * of numeric values.  No axes, no labels -- just the shape of the
 * data so users can see trends at a glance.
 */

import { useMemo } from "react";

import { cn } from "@/lib/utils";

export interface SparklineProps {
  /** Numeric data points. At least 2 values are needed. */
  data: number[];
  /** Stroke colour -- CSS colour string or Tailwind-compatible. */
  color?: string;
  /** Fill colour beneath the line (defaults to translucent `color`). */
  fillColor?: string;
  /** Width of the SVG element in pixels. */
  width?: number;
  /** Height of the SVG element in pixels. */
  height?: number;
  /** Stroke width in SVG units. */
  strokeWidth?: number;
  /** Extra class names on the root SVG. */
  className?: string;
  /** Whether to show a filled area beneath the line. */
  showArea?: boolean;
}

export function Sparkline({
  data,
  color = "currentColor",
  fillColor,
  width = 80,
  height = 24,
  strokeWidth = 1.5,
  className,
  showArea = true,
}: SparklineProps) {
  const points = useMemo(() => {
    if (data.length < 2) return "";

    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const padY = 2;
    const usableH = height - padY * 2;

    return data
      .map((v, i) => {
        const x = (i / (data.length - 1)) * width;
        const y = padY + usableH - ((v - min) / range) * usableH;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }, [data, width, height]);

  const areaPath = useMemo(() => {
    if (!showArea || data.length < 2) return "";

    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const padY = 2;
    const usableH = height - padY * 2;

    const pts = data.map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = padY + usableH - ((v - min) / range) * usableH;
      return { x, y };
    });

    let d = `M ${pts[0]!.x.toFixed(1)} ${pts[0]!.y.toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) {
      d += ` L ${pts[i]!.x.toFixed(1)} ${pts[i]!.y.toFixed(1)}`;
    }
    // Close area to bottom-right / bottom-left
    d += ` L ${width} ${height} L 0 ${height} Z`;
    return d;
  }, [data, width, height, showArea]);

  if (data.length < 2) {
    return (
      <svg
        width={width}
        height={height}
        className={cn("shrink-0", className)}
        viewBox={`0 0 ${width} ${height}`}
      />
    );
  }

  const resolvedFill = fillColor ?? color;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("shrink-0", className)}
      aria-hidden="true"
    >
      {showArea && areaPath && (
        <path d={areaPath} fill={resolvedFill} opacity={0.12} />
      )}
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
