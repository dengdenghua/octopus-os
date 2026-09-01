import type React from "react";
import { cn } from "@/lib/utils";

interface DotProgressProps {
  progress: number; // 0..1
  hue: number; // agent theme hue
  cols?: number;
  rows?: number;
  className?: string;
}

/**
 * Kimi-style dotted progress bar. Grey dots fill with the agent's
 * theme color from left to right as progress advances. More informative
 * than a spinner, denser than a linear bar.
 */
export function DotProgress({
  progress,
  hue,
  cols = 16,
  rows = 3,
  className,
}: DotProgressProps) {
  const total = cols * rows;
  const filled = Math.round(progress * total);
  const active = `hsl(${hue} 70% 45%)`;
  const idle = "hsl(0 0% 86%)";
  const dots: React.ReactElement[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      // Fill column-by-column, top to bottom within each column.
      const ordinal = c * rows + r;
      const on = ordinal < filled;
      dots.push(
        <span
          key={`${r}-${c}`}
          className="size-[3px] rounded-[1px] transition-colors"
          style={{ background: on ? active : idle }}
        />,
      );
    }
  }
  return (
    <div
      className={cn("grid gap-[2px]", className)}
      style={{
        gridTemplateColumns: `repeat(${cols}, 3px)`,
        gridTemplateRows: `repeat(${rows}, 3px)`,
      }}
      aria-label={`progress ${Math.round(progress * 100)}%`}
    >
      {dots}
    </div>
  );
}
