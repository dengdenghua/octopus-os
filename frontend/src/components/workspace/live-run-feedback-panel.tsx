"use client";

import {
  ActivityIcon,
  BrainCircuitIcon,
  FileTextIcon,
  MessageSquareTextIcon,
  WrenchIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import {
  stripInternalToolProtocol,
  stripLeakedRendererMarkup,
} from "@/core/messages/utils";
import { cn } from "@/lib/utils";

import type { LiveToolEvent } from "./live-tool-timeline";
import { stripTraceLabelPrefixes } from "./messages/trace-labels";
import {
  isFileMutationToolName,
  isReadToolName,
  isSearchToolName,
  isShellToolName,
} from "./tool-name-groups";

interface ReactStepDetail {
  threadId?: string | null;
  currentPhase?: string | null;
  progressSummary?: string | null;
  feedbackSummary?: string | null;
}

interface ThinkingSignalDetail {
  threadId?: string | null;
  type?: string | null;
  iteration?: number | null;
}

interface FeedbackEntry {
  id: string;
  kind: "progress" | "feedback";
  text: string;
  at: number;
}

interface LiveRunFeedbackPanelProps {
  liveToolEvents: LiveToolEvent[];
  threadId?: string | null;
  className?: string;
}

const META_TOOL_NAMES = new Set(["planning", "team_routing", "todo_write"]);

const CONTENT_PREVIEW_KEYS = [
  "content",
  "text",
  "code",
  "body",
  "patch",
  "diff",
] as const;

function textFromValue(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const trimmed = text.trim();
  return trimmed ? trimmed : undefined;
}

function compactInline(value: unknown, max = 180): string | undefined {
  const text = publicText(textFromValue(value))?.replace(/\s+/g, " ");
  if (!text) return undefined;
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function previewBlock(value: unknown): string | undefined {
  const text = publicText(textFromValue(value));
  if (!text) return undefined;
  const lines = text.split(/\r?\n/);
  const preview = lines.slice(0, 10).join("\n");
  const tooLong = lines.length > 10 || preview.length > 1100;
  return `${preview.slice(0, 1100)}${tooLong ? "\n..." : ""}`;
}

function valueAt(record: Record<string, unknown> | undefined, keys: string[]) {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && `${value}`.trim() !== "") {
      return value;
    }
  }
  return undefined;
}

function basenamePath(value: string): string {
  const normalized = value.replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized.split("/").filter(Boolean).at(-1) ?? normalized;
}

const INTERNAL_TOOL_NAME_RE =
  /\b(?:read_file|write_text_file|shell_command|exec_command|grep_text|list_cwd|apply_patch|todo_write|web_search|fetch_url|browser_[a-z0-9_]+|mcp__[a-z0-9_]+)\b/i;

const SECRET_OR_PROTOCOL_RE =
  /(?:<[^>\n]+>|\b(?:token|api[_-]?key|secret|password|authorization)\s*[=:])/i;

function publicText(value?: string): string | undefined {
  const cleaned = stripLeakedRendererMarkup(
    stripInternalToolProtocol(stripTraceLabelPrefixes(value ?? "")),
    { trim: true },
  ).trim();
  if (!cleaned) return undefined;
  if (SECRET_OR_PROTOCOL_RE.test(cleaned)) return undefined;
  return cleaned;
}

function publicTarget(value: string | undefined): string | undefined {
  const cleaned = publicText(value);
  if (!cleaned) return undefined;
  if (INTERNAL_TOOL_NAME_RE.test(cleaned)) return undefined;
  if (/^[-\w./~]+(?:\.\w+)?$/.test(cleaned) && cleaned.includes("/")) {
    return basenamePath(cleaned);
  }
  return cleaned;
}

function hostOf(value: string): string {
  try {
    const url = new URL(value);
    return url.hostname || value;
  } catch {
    return value;
  }
}

function eventPath(event: LiveToolEvent): string | undefined {
  const raw = compactInline(
    valueAt(event.input, ["path", "file_path", "target", "cwd"]),
    80,
  );
  return publicTarget(raw ? basenamePath(raw) : undefined);
}

function eventPublicTarget(event: LiveToolEvent): string | undefined {
  const input = event.input;
  const explicitSummary = publicTarget(
    compactInline(
    valueAt(input, ["description", "summary", "label", "title"]),
    80,
    ),
  );
  if (explicitSummary) return explicitSummary;
  const query = publicTarget(compactInline(valueAt(input, ["query", "pattern"]), 80));
  if (query) return query;
  const url = publicTarget(compactInline(valueAt(input, ["url"]), 80));
  if (url) return hostOf(url);
  return eventPath(event);
}

function contentPreviewFromEvent(event: LiveToolEvent): string | undefined {
  for (const key of CONTENT_PREVIEW_KEYS) {
    const preview = previewBlock(event.input?.[key]);
    if (preview) return preview;
  }
  if (
    typeof event.output === "object" &&
    event.output !== null &&
    !Array.isArray(event.output)
  ) {
    const output = event.output as Record<string, unknown>;
    for (const key of CONTENT_PREVIEW_KEYS) {
      const preview = previewBlock(output[key]);
      if (preview) return preview;
    }
  }
  return undefined;
}

function latestByTime(events: LiveToolEvent[]): LiveToolEvent | null {
  if (events.length === 0) return null;
  return (
    [...events].sort(
      (a, b) => (b.finishedAt ?? b.startedAt) - (a.finishedAt ?? a.startedAt),
    )[0] ?? null
  );
}

function describeToolEvent(
  event: LiveToolEvent | null,
  t: {
    liveRunFeedback: {
      updatingTodos: string;
      writingFile: string;
      writeComplete: string;
      readingFile: string;
      readingContext: string;
      runningCommand: string;
      calling: string;
    };
  },
): string | null {
  if (!event) return null;
  const path = eventPath(event);
  const isRunning = event.status === "running";
  if (isFileMutationToolName(event.name)) {
    return `${isRunning ? t.liveRunFeedback.writingFile : t.liveRunFeedback.writeComplete}${path ? ` ${path}` : ""}`;
  }
  if (isReadToolName(event.name)) {
    return `${t.liveRunFeedback.readingFile}${path ? ` ${path}` : ` ${t.liveRunFeedback.readingContext}`}`;
  }
  if (isSearchToolName(event.name)) {
    const target = eventPublicTarget(event);
    return `${t.liveRunFeedback.readingContext}${target ? ` ${target}` : ""}`;
  }
  if (isShellToolName(event.name)) {
    const target = eventPublicTarget(event);
    return `${t.liveRunFeedback.runningCommand}${target ? ` · ${target}` : ""}`;
  }
  return path ? `${t.liveRunFeedback.calling} · ${path}` : null;
}

function outputFeedback(event: LiveToolEvent | null): string | null {
  if (!event) return null;
  if (event.observation) return compactInline(event.observation, 220) ?? null;
  if (event.output === undefined || event.output === null) return null;
  if (typeof event.output === "string")
    return compactInline(event.output, 220) ?? null;
  if (typeof event.output === "object" && !Array.isArray(event.output)) {
    const record = event.output as Record<string, unknown>;
    const value = valueAt(record, [
      "error",
      "stderr",
      "stdout",
      "result",
      "message",
      "path",
    ]);
    return compactInline(value, 220) ?? null;
  }
  return compactInline(event.output, 220) ?? null;
}

function appendEntry(
  entries: FeedbackEntry[],
  entry: FeedbackEntry,
): FeedbackEntry[] {
  const previous = entries[entries.length - 1];
  if (previous?.kind === entry.kind && previous.text === entry.text) {
    return entries;
  }
  return [...entries, entry].slice(-5);
}

export function LiveRunFeedbackPanel({
  liveToolEvents,
  threadId,
  className,
}: LiveRunFeedbackPanelProps) {
  const { t } = useI18n();
  const [entries, setEntries] = useState<FeedbackEntry[]>([]);
  const [phase, setPhase] = useState<string | null>(null);
  const [thinkingSignal, setThinkingSignal] =
    useState<ThinkingSignalDetail | null>(null);
  const lastThinkingAtRef = useRef(0);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<ReactStepDetail>).detail;
      if (!detail) return;
      if (threadId && detail.threadId && detail.threadId !== threadId) return;
      setPhase(detail.currentPhase ?? null);
      const now = Date.now();
      if (detail.progressSummary) {
        setEntries((prev) =>
          appendEntry(prev, {
            id: `progress:${now}`,
            kind: "progress",
            text: detail.progressSummary!,
            at: now,
          }),
        );
      }
      if (detail.feedbackSummary) {
        setEntries((prev) =>
          appendEntry(prev, {
            id: `feedback:${now}`,
            kind: "feedback",
            text: detail.feedbackSummary!,
            at: now,
          }),
        );
      }
    };
    window.addEventListener("echo:react_step", handler);
    return () => window.removeEventListener("echo:react_step", handler);
  }, [threadId]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<ThinkingSignalDetail>).detail;
      if (!detail) return;
      if (threadId && detail.threadId && detail.threadId !== threadId) return;
      const now = Date.now();
      if (now - lastThinkingAtRef.current < 600) return;
      lastThinkingAtRef.current = now;
      setThinkingSignal(detail);
    };
    window.addEventListener("echo:thinking_signal", handler);
    return () => window.removeEventListener("echo:thinking_signal", handler);
  }, [threadId]);

  const runningEvent = useMemo(
    () =>
      latestByTime(
        liveToolEvents.filter(
          (event) =>
            !META_TOOL_NAMES.has(event.name) && event.status === "running",
        ),
      ),
    [liveToolEvents],
  );
  const latestDoneEvent = useMemo(
    () =>
      latestByTime(
        liveToolEvents.filter(
          (event) =>
            !META_TOOL_NAMES.has(event.name) && event.status !== "running",
        ),
      ),
    [liveToolEvents],
  );
  const focusEvent = runningEvent ?? latestDoneEvent;
  const toolSummary = describeToolEvent(focusEvent, t);
  const fallbackFeedback =
    entries.length === 0 ? outputFeedback(latestDoneEvent) : null;
  const contentEvent = useMemo(
    () =>
      latestByTime(
        liveToolEvents.filter(
          (event) =>
            isFileMutationToolName(event.name) &&
            contentPreviewFromEvent(event),
        ),
      ),
    [liveToolEvents],
  );
  const contentPreview = contentEvent
    ? contentPreviewFromEvent(contentEvent)
    : undefined;
  const phaseLabels: Record<string, string> = {
    understand: t.liveRunFeedback.phaseUnderstand,
    execute: t.liveRunFeedback.phaseExecute,
    verify: t.liveRunFeedback.phaseVerify,
  };
  const phaseLabel = phase ? (phaseLabels[phase] ?? phase) : null;
  const thinkingLabel =
    thinkingSignal?.type === "text_delta"
      ? t.liveRunFeedback.generatingActionDraft
      : thinkingSignal
        ? t.liveRunFeedback.generatingReasoning
        : null;
  const hasSignal =
    entries.length > 0 ||
    Boolean(toolSummary) ||
    Boolean(fallbackFeedback) ||
    Boolean(contentPreview);

  if (!hasSignal) return null;

  return (
    <div
      className={cn(
        "workspace-panel-subtle my-2 w-full rounded-lg border border-border-default p-3 text-xs",
        className,
      )}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 font-semibold text-foreground">
          <BrainCircuitIcon className="size-3.5 text-primary" />
          {t.liveRunFeedback.title}
        </span>
        {phaseLabel && (
          <span className="rounded-md bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
            {phaseLabel}
          </span>
        )}
      </div>

      <div className="space-y-2">
        {thinkingLabel && (
          <div className="flex gap-2 text-muted-foreground">
            <BrainCircuitIcon className="mt-0.5 size-3.5 shrink-0 text-primary" />
            <span>
              {thinkingLabel}
              {thinkingSignal?.iteration
                ? ` · ${t.liveRunFeedback.iteration(thinkingSignal.iteration)}`
                : ""}
            </span>
          </div>
        )}

        {entries.map((entry) => (
          <div key={entry.id} className="flex gap-2">
            {entry.kind === "progress" ? (
              <ActivityIcon className="mt-0.5 size-3.5 shrink-0 text-info" />
            ) : (
              <MessageSquareTextIcon className="mt-0.5 size-3.5 shrink-0 text-success" />
            )}
            <p
              className={cn(
                "min-w-0 leading-5",
                entry.kind === "feedback"
                  ? "text-success"
                  : "text-foreground/85",
              )}
            >
              {entry.text}
            </p>
          </div>
        ))}

        {toolSummary && (
          <div className="flex gap-2 text-muted-foreground">
            <WrenchIcon className="mt-0.5 size-3.5 shrink-0" />
            <span className="min-w-0 break-words">{toolSummary}</span>
          </div>
        )}

        {fallbackFeedback && (
          <div className="rounded-md border border-success/20 bg-success/5 px-2 py-1.5 text-xs leading-5 text-success">
            {fallbackFeedback}
          </div>
        )}

        {contentPreview && (
          <div className="overflow-hidden rounded-md border border-border-default bg-background/70">
            <div className="flex items-center gap-1.5 border-b border-border-subtle px-2 py-1 text-xs font-medium text-muted-foreground">
              <FileTextIcon className="size-3" />
              {t.liveRunFeedback.contentPreview}
              {eventPath(contentEvent!) ? ` · ${eventPath(contentEvent!)}` : ""}
            </div>
            <pre className="max-h-44 overflow-hidden whitespace-pre-wrap break-words px-2 py-1.5 font-mono text-xs leading-4 text-foreground/80">
              {contentPreview}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
