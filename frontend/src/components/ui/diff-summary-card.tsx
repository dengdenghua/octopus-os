import { ChevronDownIcon, Undo2Icon } from "lucide-react";
import { useState, type ReactNode } from "react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

/**
 * Diff summary card. Appears after the assistant has applied
 * a multi-file patch: a header with total change counts and an inline
 * undo, and a collapsed file-by-file list below.
 *
 *   4 files changed  +261  -0           ↶ Undo
 *   ─────────────────────────────
 *    frontend/src/router.tsx    +4  -0
 *    frontend/tsconfig.json     +2  -0
 *    …
 */

export interface DiffFileRow {
  path: string;
  added: number;
  removed: number;
  onClick?: () => void;
}

export interface DiffSummaryCardProps {
  files: DiffFileRow[];
  onUndo?: () => void;
  /** Override the "N files changed" header text. */
  title?: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}

export function DiffSummaryCard({
  files,
  onUndo,
  title,
  defaultOpen = false,
  className,
}: DiffSummaryCardProps) {
  const [open, setOpen] = useState(defaultOpen);
  const totalAdded = files.reduce((s, f) => s + f.added, 0);
  const totalRemoved = files.reduce((s, f) => s + f.removed, 0);
  const fileCount = files.length;

  const headerLabel = title ?? (
    <span>
      <span className="font-medium">{fileCount}</span>{" "}
      {fileCount === 1 ? "file" : "files"} changed
    </span>
  );

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div
        className={cn(
          "w-full rounded-xl border border-border/70 bg-[color:color-mix(in_oklch,var(--muted)_35%,transparent)]",
          "overflow-hidden backdrop-blur-[2px]",
          className,
        )}
      >
        <div className="flex items-center gap-3 px-3.5 py-2.5">
          <CollapsibleTrigger className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm outline-none">
            <ChevronDownIcon
              className={cn(
                "size-3.5 shrink-0 opacity-60 transition-transform duration-200",
                open && "rotate-0",
                !open && "-rotate-90",
              )}
            />
            {headerLabel}
            <span className="ml-1 inline-flex items-center gap-1.5 text-[11.5px] font-mono">
              <span className="text-emerald-600 dark:text-emerald-400">
                +{totalAdded}
              </span>
              <span className="text-red-600 dark:text-red-400">
                -{totalRemoved}
              </span>
            </span>
          </CollapsibleTrigger>
          {onUndo && (
            <button
              type="button"
              onClick={onUndo}
              className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11.5px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            >
              <Undo2Icon className="size-3" />
              Undo
            </button>
          )}
        </div>
        <CollapsibleContent>
          <ul className="divide-y divide-border/40 border-t border-border/40">
            {files.map((f) => (
              <li key={f.path}>
                <button
                  type="button"
                  onClick={f.onClick}
                  disabled={!f.onClick}
                  className={cn(
                    "flex w-full items-center gap-3 px-3.5 py-1.5 text-left",
                    "text-[12px] transition-colors",
                    f.onClick && "hover:bg-muted/50",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">
                    {f.path}
                  </span>
                  <span className="shrink-0 font-mono text-[11px]">
                    <span className="text-emerald-600 dark:text-emerald-400">
                      +{f.added}
                    </span>
                    {"  "}
                    <span className="text-red-600 dark:text-red-400">
                      -{f.removed}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}
