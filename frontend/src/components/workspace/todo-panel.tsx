/**
 * TodoPanel renders the latest todo_write checklist for the active turn.
 * The model sends a full replacement list each time it updates status, so
 * the panel always shows the most recent todo_write payload.
 */

import {
  CheckCircle2Icon,
  ChevronDownIcon,
  CircleIcon,
  ListTodoIcon,
  Loader2Icon,
  XCircleIcon,
  XIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import type { LiveToolEvent } from "./live-tool-timeline";

interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed" | "blocked" | "interrupted";
  activeForm: string;
}

interface TodoPanelProps {
  liveToolEvents: LiveToolEvent[];
  className?: string;
  defaultOpen?: boolean;
}

function coerceTodoItems(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value === "string" && value.trim()) {
    try {
      return coerceTodoItems(JSON.parse(value));
    } catch (e) {
      swallow(e);
      return [];
    }
  }
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    return coerceTodoItems(record.items ?? record.todos);
  }
  return [];
}

function extractLatestTodos(events: LiveToolEvent[]): TodoItem[] {
  if (!events || events.length === 0) return [];
  const todoWrites = events
    .filter((event) => event.name === "todo_write" && event.input)
    .sort((a, b) => a.startedAt - b.startedAt);
  const sourceTodos = todoWrites.filter(
    (event) => event.input?.source !== "turn.phases",
  );
  // turn.phases is a compatibility projection, not a second checklist. Prefer
  // the latest source todo so this panel and the workbench consume the same
  // statuses even when the projection arrives later over the socket.
  const latest =
    sourceTodos[sourceTodos.length - 1] ?? todoWrites[todoWrites.length - 1];
  if (!latest?.input) return [];

  const rawItems = coerceTodoItems(latest.input.items ?? latest.input.todos);
  const cleaned: TodoItem[] = [];
  for (const raw of rawItems) {
    if (typeof raw !== "object" || raw === null) continue;
    const record = raw as Record<string, unknown>;
    const content =
      typeof record.content === "string" && record.content.trim()
        ? record.content.trim()
        : typeof record.text === "string" && record.text.trim()
          ? record.text.trim()
          : typeof record.title === "string" && record.title.trim()
            ? record.title.trim()
            : typeof record.task === "string" && record.task.trim()
              ? record.task.trim()
              : "";
    if (!content) continue;
    const status =
      record.status === "completed"
        ? "completed"
        : record.status === "in_progress" || record.status === "running"
          ? "in_progress"
          : record.status === "blocked"
            ? "blocked"
            : record.status === "interrupted" ||
                record.status === "failed" ||
                record.status === "error"
              ? "interrupted"
              : "pending";
    const activeForm =
      typeof record.activeForm === "string" && record.activeForm.trim()
        ? record.activeForm.trim()
        : typeof record.active_form === "string" && record.active_form.trim()
          ? record.active_form.trim()
          : content;
    cleaned.push({ content, status, activeForm });
  }
  return cleaned;
}

function isLiveTodoStream(events: LiveToolEvent[]): boolean {
  return events.some(
    (event) =>
      event.status === "running" || event.status === "waiting_approval",
  );
}

function TodoRow({ item, live }: { item: TodoItem; live: boolean }) {
  const displayStatus =
    item.status === "in_progress" && !live ? "pending" : item.status;
  const icon =
    displayStatus === "completed" ? (
      <CheckCircle2Icon className="size-4 shrink-0 text-success" />
    ) : displayStatus === "interrupted" || displayStatus === "blocked" ? (
      <XCircleIcon className="size-4 shrink-0 text-destructive" />
    ) : displayStatus === "in_progress" ? (
      <Loader2Icon className="size-4 shrink-0 animate-spin text-info" />
    ) : (
      <CircleIcon className="size-4 shrink-0 text-muted-foreground/50" />
    );
  const label = item.status === "in_progress" ? item.activeForm : item.content;

  return (
    <div className="flex items-start gap-2 py-1">
      <div className="mt-0.5">{icon}</div>
      <span
        className={cn(
          "text-sm leading-5",
          displayStatus === "completed" && "text-muted-foreground line-through",
          displayStatus === "in_progress" && "font-medium",
          (displayStatus === "interrupted" || displayStatus === "blocked") &&
            "font-medium text-destructive",
        )}
      >
        {label}
      </span>
    </div>
  );
}

export function TodoPanel({
  liveToolEvents,
  className,
  defaultOpen = true,
}: TodoPanelProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const [closed, setClosed] = useState(false);
  const todos = useMemo(
    () => extractLatestTodos(liveToolEvents),
    [liveToolEvents],
  );
  const live = useMemo(
    () => isLiveTodoStream(liveToolEvents),
    [liveToolEvents],
  );
  if (todos.length === 0) return null;

  const completed = todos.filter((item) => item.status === "completed").length;
  const total = todos.length;
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;

  if (closed) {
    return (
      <button
        type="button"
        className={cn(
          "ml-auto flex w-fit max-w-full items-center gap-2 rounded-full border border-border-default bg-background/90 px-3 py-1.5 text-xs shadow-[var(--shadow-xs)] backdrop-blur-xl transition-colors hover:bg-muted/60",
          className,
        )}
        onClick={() => {
          setClosed(false);
          setOpen(true);
        }}
      >
        <ListTodoIcon className="size-3.5 shrink-0 text-primary" />
        <span className="font-medium">{t.todoPanel.title}</span>
        <span className="tabular-nums text-muted-foreground">
          {completed}/{total}
        </span>
      </button>
    );
  }

  return (
    <div
      className={cn(
        "workspace-panel-subtle overflow-hidden rounded-lg border border-border-default shadow-[var(--shadow-md)] shadow-black/5 backdrop-blur-xl",
        className,
      )}
    >
      <div className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <ListTodoIcon className="size-4 shrink-0 text-primary" />
        <button
          type="button"
          className="min-w-0 flex-1 text-left"
          onClick={() => setOpen((value) => !value)}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-xs font-semibold text-foreground">
              {t.todoPanel.title}
            </div>
            <div className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {completed}/{total} · {percent}%
            </div>
          </div>
        </button>
        <button
          type="button"
          className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          onClick={() => setOpen((value) => !value)}
        >
          <span>{open ? t.todoPanel.collapse : t.todoPanel.expand}</span>
          <ChevronDownIcon
            className={cn(
              "size-3.5 transition-transform",
              open ? "rotate-180" : "rotate-0",
            )}
          />
        </button>
        <button
          type="button"
          className="flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          onClick={() => setClosed(true)}
          aria-label={t.todoPanel.closeTaskPlan}
          title={t.todoPanel.closeTaskPlan}
        >
          <XIcon className="size-3.5" />
        </button>
      </div>
      <button
        type="button"
        aria-label={
          open ? t.todoPanel.collapseTaskPlan : t.todoPanel.expandTaskPlan
        }
        className="block w-full px-3 pb-0.5"
        onClick={() => setOpen((value) => !value)}
      >
        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full bg-success transition-all duration-slow"
            style={{ width: `${percent}%` }}
          />
        </div>
      </button>
      {open && (
        <div className="max-h-44 overflow-y-auto px-3 py-2">
          {todos.map((item, index) => (
            <TodoRow
              key={`${item.status}-${index}-${item.content}`}
              item={item}
              live={live}
            />
          ))}
        </div>
      )}
    </div>
  );
}
