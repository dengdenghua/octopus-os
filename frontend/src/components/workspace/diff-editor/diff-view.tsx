/* Implementation note. */

import { useMemo } from "react";
import { diffLines, type Change } from "diff";
import { cn } from "@/lib/utils";

interface DiffViewProps {
  oldValue: string;
  newValue: string;
  filename?: string;
  className?: string;
}

export function DiffView({
  oldValue,
  newValue,
  filename,
  className,
}: DiffViewProps) {
  const diff = useMemo(() => {
    return diffLines(oldValue, newValue);
  }, [oldValue, newValue]);

  return (
    <div className={cn("font-mono text-sm overflow-auto", className)}>
      {filename && (
        <div className="sticky top-0 z-10 border-b bg-muted px-4 py-2 text-sm font-medium">
          {filename}
        </div>
      )}
      <div className="min-w-full">
        {diff.map((part, index) => (
          <DiffLine key={index} part={part} index={index} />
        ))}
      </div>
    </div>
  );
}

function DiffLine({ part, index }: { part: Change; index: number }) {
  const lines = part.value.split("\n").filter((line, i, arr) => {
    // Remove trailing empty line
    if (i === arr.length - 1 && line === "") return false;
    return true;
  });

  const bgColor = part.added
    ? "bg-success/10"
    : part.removed
      ? "bg-destructive/10"
      : "bg-transparent";

  const textColor = part.added
    ? "text-success"
    : part.removed
      ? "text-destructive"
      : "text-foreground";

  const prefix = part.added ? "+" : part.removed ? "-" : " ";

  return (
    <>
      {lines.map((line, lineIndex) => (
        <div
          key={`${index}-${lineIndex}`}
          className={cn("flex px-4 py-0.5 whitespace-pre", bgColor, textColor)}
        >
          <span className="select-none w-4 mr-2 text-muted-foreground">
            {prefix}
          </span>
          <span className="flex-1">{line || " "}</span>
        </div>
      ))}
    </>
  );
}
