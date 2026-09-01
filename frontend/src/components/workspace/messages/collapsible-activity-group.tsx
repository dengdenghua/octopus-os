import {
  BrainIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ClipboardListIcon,
  Loader2Icon,
  PencilLineIcon,
  WrenchIcon,
  XCircleIcon,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { useConversationDetailLevel } from "./use-conversation-detail-level";

export type ActivityKind = "think" | "plan" | "file_ops" | "tool_calls";

export interface ActivityItem {
  id: string;
  label: string;
  detail?: string;
  status?: "done" | "running" | "error";
  meta?: Record<string, unknown>;
}

interface Props {
  kind: ActivityKind;
  items: ActivityItem[];
  isRunning?: boolean;
  defaultOpen?: boolean;
  className?: string;
}

// -------------------------------------------------------------------------
// Meta helpers
// -------------------------------------------------------------------------

const KIND_ICON: Record<ActivityKind, LucideIcon> = {
  think: BrainIcon,
  plan: ClipboardListIcon,
  file_ops: PencilLineIcon,
  tool_calls: WrenchIcon,
};

function readNumberMeta(
  meta: Record<string, unknown> | undefined,
  key: string,
): number {
  if (!meta) return 0;
  const value = meta[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number.parseInt(value, 10);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function sumMeta(items: ActivityItem[], key: string): number {
  let total = 0;
  for (const item of items) {
    total += readNumberMeta(item.meta, key);
  }
  return total;
}

// -------------------------------------------------------------------------
// Lightweight unified-diff rendering (red/green +/- lines, Claude-style)
// -------------------------------------------------------------------------

type DiffRowType = "ctx" | "add" | "del";

interface DiffRow {
  type: DiffRowType;
  text: string;
}

/** Parse a unified diff into +/-/context rows, skipping the meta headers. */
export function parseUnifiedDiff(diff: string): DiffRow[] {
  const rows: DiffRow[] = [];
  if (!diff || !diff.trim()) return rows;
  for (const line of diff.split(/\r?\n/)) {
    if (
      line.startsWith("diff --git") ||
      line.startsWith("--- ") ||
      line.startsWith("+++ ") ||
      line.startsWith("@@ ") ||
      line.startsWith("\\ No newline")
    ) {
      continue;
    }
    if (line.startsWith("+")) rows.push({ type: "add", text: line.slice(1) });
    else if (line.startsWith("-"))
      rows.push({ type: "del", text: line.slice(1) });
    else rows.push({ type: "ctx", text: line });
  }
  return rows;
}

function DiffLines({ diffs }: { diffs: unknown }) {
  const { t } = useI18n();
  const { rows, truncated } = useMemo(() => {
    if (!Array.isArray(diffs)) return { rows: [] as DiffRow[], truncated: 0 };
    const list: DiffRow[] = [];
    for (const raw of diffs) {
      if (typeof raw !== "string" || !raw.trim()) continue;
      list.push(...parseUnifiedDiff(raw));
    }
    // Truncate pathological diffs (chat perf guard); the header row in
    // the label already carries the +N/-M counts.
    if (list.length > 400) {
      return { rows: list.slice(0, 400), truncated: list.length - 400 };
    }
    return { rows: list, truncated: 0 };
  }, [diffs]);

  if (rows.length === 0) return null;

  return (
    <div className="mt-1 max-h-52 overflow-auto rounded-md border border-border bg-background/70 font-mono text-xs leading-5">
      {rows.map((row, i) => (
        <div
          key={i}
          className={cn(
            "flex break-all px-2 whitespace-pre-wrap",
            row.type === "add"
              ? "bg-green-500/10 text-green-700 dark:text-green-400"
              : row.type === "del"
                ? "bg-red-500/10 text-red-600 dark:text-red-400"
                : "text-muted-foreground",
          )}
        >
          <span className="w-4 shrink-0 select-none text-muted-foreground/60">
            {row.type === "add" ? "+" : row.type === "del" ? "-" : ""}
          </span>
          <span className="min-w-0 flex-1">{row.text}</span>
        </div>
      ))}
      {truncated > 0 && (
        <div className="px-2 py-1 text-center text-muted-foreground/80 select-none">
          {t.message.diffLinesHidden(truncated)}
        </div>
      )}
    </div>
  );
}

/**
 * Build the folded-header summary text for this activity kind.
 * Returns null when the group should not be rendered at all (e.g. empty think).
 */
export function buildHeaderSummary(
  kind: ActivityKind,
  items: ActivityItem[],
  t: ReturnType<typeof useI18n>["t"],
): string | null {
  // No leading emoji here — the header already renders the kind's lucide
  // icon (KIND_ICON); emoji + lucide side by side looked inconsistent.
  if (kind === "think") {
    if (items.length === 0) return null;
    const totalSeconds = Math.max(
      1,
      Math.round(sumMeta(items, "duration_seconds")),
    );
    return t.message.thinkingForSeconds(totalSeconds);
  }
  if (kind === "plan") {
    return t.message.planningNSteps(items.length);
  }
  if (kind === "file_ops") {
    const added = sumMeta(items, "lines_added");
    const removed = sumMeta(items, "lines_removed");
    if (added === 0 && removed === 0) {
      return t.message.fileOperationsCount(items.length);
    }
    return t.message.fileOperationsCountWithDiff(
      items.length,
      added,
      removed,
    );
  }
  // tool_calls
  return t.message.toolCallsCount(items.length);
}

// -------------------------------------------------------------------------
// Component
// -------------------------------------------------------------------------

function StatusIcon({ status }: { status?: ActivityItem["status"] }) {
  if (status === "running")
    return (
      <Loader2Icon className="size-3.5 animate-spin text-muted-foreground" />
    );
  if (status === "error")
    return <XCircleIcon className="size-3.5 text-destructive" />;
  if (status === "done")
    return (
      <CheckCircle2Icon className="size-3.5 text-success" />
    );
  return <span className="size-3.5" />;
}

export function CollapsibleActivityGroup({
  kind,
  items,
  isRunning = false,
  defaultOpen = false,
  className,
}: Props) {
  const { t } = useI18n();
  const detailConfig = useConversationDetailLevel();

  // Build a stable ID for this activity group for localStorage persistence
  const groupId = useMemo(() => {
    if (items.length === 0) return null;
    // Use first item's ID as the stable identifier for this activity group
    return `activity-${kind}-${items[0]?.id}`;
  }, [kind, items]);

  // Determine initial open state based on detail level
  const initialOpen = useMemo(() => {
    if (defaultOpen !== undefined) return defaultOpen;

    // Check localStorage for user's explicit preference for this group
    if (groupId && typeof window !== "undefined") {
      const stored = localStorage.getItem(groupId);
      if (stored !== null) {
        return stored === "true";
      }
    }

    // In "low" detail, collapse everything
    if (detailConfig.level === "low") return false;

    // In "high" detail, expand all intermediate steps
    if (detailConfig.level === "high") return true;

    // In "medium" detail: current streaming turn stays open, history collapses
    return isRunning;
  }, [defaultOpen, detailConfig.level, isRunning, groupId]);

  const [open, setOpen] = useState(initialOpen);

  // Persist user's expand/collapse choice to localStorage
  useEffect(() => {
    if (groupId && typeof window !== "undefined") {
      localStorage.setItem(groupId, open.toString());
    }
  }, [open, groupId]);

  const header = useMemo(
    () => buildHeaderSummary(kind, items, t),
    [kind, items, t],
  );
  const KindIcon = KIND_ICON[kind];
  const latest = items[items.length - 1];

  if (header === null) {
    return null;
  }

  // In "low" detail mode, hide non-essential activities entirely
  if (detailConfig.level === "low" && kind !== "plan") {
    return null;
  }

  return (
    <div
      className={cn(
        "w-full rounded-lg border border-border bg-muted/30",
        className,
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 text-left text-sm",
          "hover:bg-muted/50 transition-colors",
          "cursor-pointer",
        )}
        aria-expanded={open}
      >
        {isRunning ? (
          <Loader2Icon className="size-4 shrink-0 animate-spin text-muted-foreground" />
        ) : (
          <KindIcon className="size-4 shrink-0 text-muted-foreground" />
        )}
        <span className="font-medium text-foreground">{header}</span>
        {isRunning && latest && (
          <span className="ml-auto max-w-[360px] truncate text-xs text-muted-foreground">
            {latest.label}
          </span>
        )}
        <ChevronDownIcon
          className={cn(
            "ml-auto size-4 shrink-0 text-muted-foreground transition-transform",
            open ? "rotate-180" : "",
            isRunning && latest ? "ml-2" : "",
          )}
        />
      </button>
      {open && (
        <ul className="flex flex-col gap-1 border-t border-border px-3 py-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex items-start gap-2 rounded py-1 text-sm"
            >
              <span className="mt-0.5 shrink-0">
                {item.status === "running" ? (
                  <Loader2Icon className="size-3.5 animate-spin text-muted-foreground" />
                ) : (
                  <StatusIcon status={item.status} />
                )}
              </span>
              <div className="flex min-w-0 flex-1 flex-col">
                <span
                  className={cn(
                    "truncate",
                    item.status === "error"
                      ? "text-destructive"
                      : "text-foreground",
                  )}
                >
                  {item.label}
                </span>
                {item.detail && (
                  <span className="mt-0.5 truncate text-xs text-muted-foreground">
                    {item.detail}
                  </span>
                )}
                {/* Red/green +/- lines for file edits (Claude-style). */}
                <DiffLines diffs={item.meta?.diffs} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
