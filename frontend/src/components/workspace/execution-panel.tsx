import {
  ActivityIcon,
  CheckCircle2Icon,
  ClockIcon,
  Loader2Icon,
  WrenchIcon,
} from "lucide-react";
import { useMemo } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { ExecutionMetrics } from "@/core/threads/types";
import { cn } from "@/lib/utils";

const TOOL_COLORS: Record<string, string> = {
  read_file: "text-muted-foreground",
  glob: "text-muted-foreground",
  grep: "text-muted-foreground",
  ls: "text-muted-foreground",
  list_dir: "text-muted-foreground",
  git_diff: "text-muted-foreground",
  git_status: "text-muted-foreground",
  lsp: "text-muted-foreground",
  lsp_diagnostics: "text-muted-foreground",
  write_file: "text-primary",
  str_replace: "text-primary",
  create_file: "text-primary",
  git_commit: "text-primary",
  notebook_edit: "text-primary",
  bash: "text-chart-8",
  execute: "text-chart-8",
  task: "text-chart-6",
  route_agents: "text-chart-6",
  run: "text-chart-8",
  web_search: "text-chart-7",
  browse: "text-chart-7",
  fetch: "text-chart-7",
};

function getToolLabels(t: {
  executionPanel: Record<string, string | undefined>;
}): Record<string, string> {
  return {
    read_file: t.executionPanel.readFile ?? "Read File",
    glob: t.executionPanel.searchFiles ?? "Search Files",
    grep: t.executionPanel.searchContent ?? "Search Content",
    ls: t.executionPanel.listDir ?? "List Dir",
    list_dir: t.executionPanel.listDir ?? "List Dir",
    git_diff: t.executionPanel.gitDiff ?? "Git Diff",
    git_status: t.executionPanel.gitStatus ?? "Git Status",
    lsp: "LSP",
    lsp_diagnostics: t.executionPanel.diagnostics ?? "Diagnostics",
    write_file: t.executionPanel.writeFile ?? "Write File",
    str_replace: t.executionPanel.editFile ?? "Edit File",
    create_file: t.executionPanel.createFile ?? "Create File",
    git_commit: t.executionPanel.gitCommit ?? "Git Commit",
    notebook_edit: t.executionPanel.editNotebook ?? "Edit Notebook",
    bash: t.executionPanel.terminal ?? "Terminal",
    execute: t.executionPanel.execute ?? "Execute",
    task: t.executionPanel.subAgent ?? "Sub-agent",
    route_agents: t.executionPanel.assignMember ?? "Assign Member",
    run: t.executionPanel.run ?? "Run",
    web_search: t.executionPanel.search ?? "Search",
    browse: t.executionPanel.browse ?? "Browse",
    fetch: t.executionPanel.fetch ?? "Fetch",
  };
}

function getToolColor(name: string): string {
  return TOOL_COLORS[name] ?? "text-muted-foreground";
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

export function ExecutionPanel({
  metrics,
  isLoading,
  className,
  showPrimaryStatus = true,
}: {
  metrics?: ExecutionMetrics | null;
  isLoading: boolean;
  className?: string;
  showPrimaryStatus?: boolean;
}) {
  const { t } = useI18n();
  const toolLabels = useMemo(() => getToolLabels(t), [t]);
  const hasMetrics = metrics && (metrics.iteration ?? 0) > 0;

  if (!hasMetrics && !isLoading) {
    return null;
  }

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-colors duration-slow",
        isLoading
          ? "border-primary/15 bg-primary/[0.03] text-primary/70"
          : "border-border-default bg-muted/20 text-muted-foreground",
        className,
      )}
    >
      {isLoading ? (
        <Loader2Icon className="size-3 animate-spin shrink-0" />
      ) : (
        <CheckCircle2Icon className="size-3 text-success shrink-0" />
      )}

      <div className="flex items-center gap-2">
        {hasMetrics ? (
          <div className="flex items-center gap-1">
            <ActivityIcon className="size-3" />
            <span>{t.streaming.iteration(metrics!.iteration!)}</span>
          </div>
        ) : isLoading && showPrimaryStatus ? (
          // Localized + single-source — the message body also renders a
          // reasoning spinner in its header, so we don't want a second
          // English "Thinking..." duplicate. Using i18n keeps this label
          // in sync with the streaming indicator next to us.
          <span className="text-muted-foreground">{t.streaming.thinking}</span>
        ) : null}

        {(metrics?.tool_calls_count ?? 0) > 0 && (
          <div className="flex items-center gap-1">
            <WrenchIcon className="size-3" />
            <span>{t.streaming.toolCalls(metrics!.tool_calls_count!)}</span>
          </div>
        )}
      </div>

      {isLoading && metrics?.last_tools && metrics.last_tools.length > 0 && (
        <div className="flex items-center gap-1">
          {metrics.last_tools.slice(0, 3).map((tool, i) => (
            <span
              key={`${tool}-${i}`}
              className={cn(
                "rounded-lg bg-background/60 px-2 py-0.5 text-xs font-medium border border-border-subtle",
                getToolColor(tool),
              )}
            >
              {toolLabels[tool] ?? tool}
            </span>
          ))}
        </div>
      )}

      {(metrics?.last_duration_ms ?? 0) > 0 && (
        <div className="flex items-center gap-1 ml-0.5">
          <ClockIcon className="size-3" />
          <span>{formatDuration(metrics!.last_duration_ms!)}</span>
        </div>
      )}
    </div>
  );
}
