import { AlertCircleIcon, CheckCircleIcon, HistoryIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";

type IntelligenceReport = {
  id: string;
  subscription_id: string;
  topic: string;
  title: string;
  summary: string;
  created_at: string;
  items_analyzed: number;
  skills_created: number;
  sources_scanned: number;
  source_errors: string[];
  findings?: string[];
  recommendations?: string[];
  markdown?: string;
};

const historyKey = ["intelligence", "history"] as const;
const EMPTY_REPORTS: IntelligenceReport[] = [];

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

function stripMarkdownBold(text: string): string {
  return text.replace(/\*\*(.*?)\*\*/g, "$1");
}

function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSeconds = Math.floor(diffMs / 1000);
  const diffMinutes = Math.floor(diffSeconds / 60);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffSeconds < 60) {
    return "刚刚";
  }
  if (diffMinutes < 60) {
    return `${diffMinutes}分钟前`;
  }

  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000);
  const dateDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());

  const timeStr = date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

  if (dateDay.getTime() === today.getTime()) {
    return `今天 ${timeStr}`;
  }
  if (dateDay.getTime() === yesterday.getTime()) {
    return `昨天 ${timeStr}`;
  }
  if (diffDays < 7) {
    return `${diffDays}天前`;
  }

  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function HistoryItem({
  report,
  isLast,
  compact = false,
}: {
  report: IntelligenceReport;
  isLast: boolean;
  compact?: boolean;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const hasErrors = report.source_errors.length > 0;
  const title = report.title || report.topic;
  const hasFindings = report.findings && report.findings.length > 0;

  // 紧凑模式：用于窄容器（如助理右侧面板），去时间轴、改信息卡布局。
  if (compact) {
    return (
      <div className="rounded-xl border border-border-default bg-card/50 p-3">
        <div className="flex items-start gap-2">
          <div
            className={cn(
              "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border",
              hasErrors
                ? "border-destructive/30 bg-destructive/10 text-destructive"
                : "border-primary/20 bg-primary/10 text-primary",
            )}
          >
            {hasErrors ? (
              <AlertCircleIcon className="size-3.5" />
            ) : (
              <CheckCircleIcon className="size-3.5" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                {title}
              </h3>
              {hasErrors && (
                <Badge
                  variant="destructive"
                  className="shrink-0 rounded px-1.5 py-0 text-micro font-normal"
                >
                  {t.intelligence.historyErrors(report.source_errors.length)}
                </Badge>
              )}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <span>{formatRelativeTime(report.created_at)}</span>
              <span className="text-muted-foreground/40">·</span>
              <span>
                {t.intelligence.historyItemsAnalyzed(report.items_analyzed)}
              </span>
            </div>
            <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-muted-foreground">
              {report.summary}
            </p>
            {hasFindings && expanded && (
              <ul className="mt-2 space-y-1">
                {report.findings!.slice(0, 3).map((f, i) => (
                  <li key={i} className="text-xs leading-5 text-foreground/80">
                    • {stripMarkdownBold(f)}
                  </li>
                ))}
              </ul>
            )}
            <button
              className="mt-1.5 text-xs text-primary hover:underline"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded
                ? t.intelligence.historyCollapse
                : t.intelligence.historyViewDetails}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex gap-4 pb-6">
      <div className="relative flex flex-col items-center">
        <div
          className={cn(
            "relative z-10 flex size-7 items-center justify-center rounded-full border",
            hasErrors
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : "border-primary/20 bg-primary/10 text-primary",
          )}
        >
          {hasErrors ? (
            <AlertCircleIcon className="size-3.5" />
          ) : (
            <CheckCircleIcon className="size-3.5" />
          )}
        </div>
        {!isLast && (
          <div className="absolute left-1/2 top-7 bottom-0 w-px -translate-x-1/2 bg-border-default" />
        )}
      </div>

      <div className="min-w-0 flex-1 -mt-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{title}</span>
          <Badge
            variant="outline"
            className="rounded px-1.5 py-0 text-micro font-normal"
          >
            {t.intelligence.historyItemsAnalyzed(report.items_analyzed)}
          </Badge>
          {hasErrors && (
            <Badge
              variant="destructive"
              className="rounded px-1.5 py-0 text-micro font-normal"
            >
              {t.intelligence.historyErrors(report.source_errors.length)}
            </Badge>
          )}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {formatRelativeTime(report.created_at)}
        </p>
        <div className="mt-2 rounded-md bg-muted/40 px-3 py-2">
          <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
            {report.summary}
          </p>
          {hasFindings && expanded && (
            <ul className="mt-2 space-y-1">
              {report.findings!.slice(0, 3).map((f, i) => (
                <li key={i} className="text-xs leading-5 text-foreground/80">
                  • {stripMarkdownBold(f)}
                </li>
              ))}
            </ul>
          )}
          <button
            className="mt-1 text-xs text-primary hover:underline"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? t.intelligence.historyCollapse : t.intelligence.historyViewDetails}
          </button>
        </div>
      </div>
    </div>
  );
}

export function AutomationHistoryTab({
  compact = false,
}: {
  /** 紧凑模式：用于窄容器（如助理右侧面板），改用信息卡列表布局。 */
  compact?: boolean;
}) {
  const { t } = useI18n();

  const reportsQuery = useQuery({
    queryKey: historyKey,
    queryFn: async () => {
      const data = await apiFetch<{ reports?: IntelligenceReport[] }>(
        "/api/intelligence/reports",
      );
      return data.reports ?? [];
    },
  });

  const reports = reportsQuery.data ?? EMPTY_REPORTS;
  const loading = reportsQuery.isLoading;

  if (loading) {
    return (
      <div className="space-y-0">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="relative flex gap-4 pb-6">
            <div className="relative flex flex-col items-center">
              <Skeleton className="codex-skeleton relative z-10 size-7 rounded-full" />
              {i < 3 && <div className="absolute left-1/2 top-7 bottom-0 w-px -translate-x-1/2 bg-border-default/40" />}
            </div>
            <div className="min-w-0 flex-1 -mt-1 space-y-2">
              <div className="flex items-center gap-2">
                <Skeleton className="codex-skeleton h-4 w-40 rounded" />
                <Skeleton className="codex-skeleton h-4 w-12 rounded" />
              </div>
              <Skeleton className="codex-skeleton h-3 w-24 rounded" />
              <Skeleton className="codex-skeleton h-16 w-full rounded-md" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (reports.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border-default bg-card/50 p-12 text-center">
        <HistoryIcon className="mx-auto size-10 text-muted-foreground/60" />
        <p className="mt-3 text-sm font-medium">{t.intelligence.historyEmptyTitle}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {t.intelligence.historyEmptyDescription}
        </p>
      </div>
    );
  }

  const sortedReports = [...reports].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );

  return (
    <div className={compact ? "space-y-2" : undefined}>
      {sortedReports.map((report, index) => (
        <HistoryItem
          key={report.id}
          report={report}
          isLast={index === sortedReports.length - 1}
          compact={compact}
        />
      ))}
    </div>
  );
}
