import {
  ArrowRightIcon,
  BellRingIcon,
  ChevronDownIcon,
  ClockIcon,
  Loader2Icon,
  NewspaperIcon,
  PlusIcon,
  RadarIcon,
  RefreshCwIcon,
  SparklesIcon,
  Trash2Icon,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import { cn } from "@/lib/utils";
import { MarkdownContent } from "./messages/markdown-content";

type IntelligenceSubscription = {
  id: string;
  topic: string;
  display_name?: string;
  keywords?: string[];
  enabled?: boolean;
  last_run?: string | null;
  cadence?: string;
  schedule_time?: string;
  schedule_day?: string;
  timezone?: string;
  instructions?: string;
  sources?: string[];
};

type IntelligenceReport = {
  id?: string;
  topic?: string;
  title?: string;
  summary?: string;
  created_at?: string;
  items_analyzed?: number;
  skills_created?: number;
  findings?: string[];
  recommendations?: string[];
  source_errors?: string[];
  markdown?: string;
  items?: Array<{
    id?: string;
    title?: string;
    url?: string;
    snippet?: string;
    source?: string;
    score?: number;
  }>;
};

type SubscriptionDraft = {
  topic: string;
  display_name: string;
  keywords: string[];
  cadence: string;
  schedule_time: string;
  schedule_day: string;
  timezone: string;
  instructions: string;
  sources: string[];
};

const subscriptionsKey = ["intelligence", "subscriptions"] as const;
const reportsKey = ["intelligence", "reports"] as const;
const EMPTY_SUBSCRIPTIONS: IntelligenceSubscription[] = [];
const EMPTY_REPORTS: IntelligenceReport[] = [];
const MONTHDAY_OPTIONS = Array.from({ length: 31 }, (_, index) =>
  String(index + 1),
);

function localTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
}

function normalizeDraft(
  draft: SubscriptionDraft,
  defaultCadence: string,
): SubscriptionDraft {
  return {
    ...draft,
    cadence: draft.cadence || defaultCadence,
    schedule_time: draft.schedule_time || "09:00",
    schedule_day: draft.schedule_day || "1",
    timezone: draft.timezone || localTimezone(),
  };
}

function inferScheduleFromGoal(
  goal: string,
  t: Translations,
): Partial<SubscriptionDraft> {
  const patch: Partial<SubscriptionDraft> = {};
  const timeMatch = goal.match(/\b([01]?\d|2[0-3])[:：]([0-5]\d)\b/);
  if (timeMatch) {
    patch.schedule_time = `${String(Number(timeMatch[1])).padStart(2, "0")}:${timeMatch[2]}`;
  } else if (/晚上|晚间|夜间/i.test(goal)) {
    patch.schedule_time = "20:00";
  } else if (/下午/i.test(goal)) {
    patch.schedule_time = "15:00";
  } else if (/中午/i.test(goal)) {
    patch.schedule_time = "12:00";
  }

  if (/每周|周报|weekly/i.test(goal)) {
    patch.cadence = t.intelligencePanel.cadenceWeekly;
    const weekdayMatch = goal.match(
      /(?:周|星期|礼拜)\s*([一二三四五六日天1-7])/,
    );
    const weekdayMap: Record<string, string> = {
      一: "1",
      "1": "1",
      二: "2",
      "2": "2",
      三: "3",
      "3": "3",
      四: "4",
      "4": "4",
      五: "5",
      "5": "5",
      六: "6",
      "6": "6",
      日: "7",
      天: "7",
      "7": "7",
    };
    const weekdayToken = weekdayMatch?.[1];
    if (weekdayToken) patch.schedule_day = weekdayMap[weekdayToken] ?? "1";
  } else if (/每月|月报|monthly/i.test(goal)) {
    patch.cadence = t.intelligencePanel.cadenceMonthly;
    const monthDayMatch = goal.match(/(?:每月|月)\s*(\d{1,2})\s*(?:号|日)?/);
    if (monthDayMatch) {
      patch.schedule_day = String(
        Math.max(1, Math.min(Number(monthDayMatch[1]), 31)),
      );
    }
  } else if (/高频|实时|hourly|real-time/i.test(goal)) {
    patch.cadence = t.intelligencePanel.cadenceHighFrequency;
  } else if (/每天|每日|daily/i.test(goal)) {
    patch.cadence = t.intelligencePanel.cadenceDaily;
  }

  return patch;
}

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

function fmtDate(value: string | null | undefined, locale: string) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString(locale);
}

function safeDate(value?: string | null) {
  const parsed = value ? new Date(value) : new Date();
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
}

function reportDateParts(value: string | null | undefined, t: Translations) {
  const d = safeDate(value);
  const weekday = [
    t.intelligencePanel.reportWeekdaySunday,
    t.intelligencePanel.reportWeekdayMonday,
    t.intelligencePanel.reportWeekdayTuesday,
    t.intelligencePanel.reportWeekdayWednesday,
    t.intelligencePanel.reportWeekdayThursday,
    t.intelligencePanel.reportWeekdayFriday,
    t.intelligencePanel.reportWeekdaySaturday,
  ][d.getDay()];
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hours = String(d.getHours()).padStart(2, "0");
  const minutes = String(d.getMinutes()).padStart(2, "0");
  return {
    day,
    monthDay: `${month}.${day}`,
    shortDate: `${month}/${day}`,
    time: `${hours}:${minutes}`,
    weekday,
  };
}

function stripMd(text?: string) {
  return (text ?? "")
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/#+\s*/g, "")
    .trim();
}

function reportHeadline(report: IntelligenceReport, fallback: string) {
  return stripMd(report.title || report.topic || fallback);
}

function reportTopic(report: IntelligenceReport, fallback: string) {
  return stripMd(report.topic || report.title || fallback);
}

function reportPreview(report: IntelligenceReport) {
  const text =
    report.summary ||
    report.findings?.find(Boolean) ||
    report.recommendations?.find(Boolean) ||
    report.items?.find((item) => item.snippet)?.snippet ||
    "";
  return stripMd(text);
}

function scheduleText(
  item: Pick<
    IntelligenceSubscription,
    "cadence" | "schedule_time" | "schedule_day" | "timezone"
  >,
  t: Translations,
) {
  const cadence = item.cadence || t.intelligencePanel.cadenceDaily;
  const time = item.schedule_time || "09:00";
  const timezone = item.timezone || localTimezone();
  if (cadence.includes("高频") || cadence.toLowerCase().includes("hour")) {
    return t.intelligencePanel.scheduleHighFrequency(timezone);
  }
  if (cadence.includes("周") || cadence.toLowerCase().includes("week")) {
    const weekdayMap: Record<string, string> = {
      "1": t.intelligencePanel.weekdayMonday,
      "2": t.intelligencePanel.weekdayTuesday,
      "3": t.intelligencePanel.weekdayWednesday,
      "4": t.intelligencePanel.weekdayThursday,
      "5": t.intelligencePanel.weekdayFriday,
      "6": t.intelligencePanel.weekdaySaturday,
      "7": t.intelligencePanel.weekdaySunday,
    };
    const weekday =
      weekdayMap[String(item.schedule_day || "1")] ??
      t.intelligencePanel.weekdayMonday;
    return t.intelligencePanel.scheduleWeekly(weekday, time, timezone);
  }
  if (cadence.includes("月") || cadence.toLowerCase().includes("month")) {
    return t.intelligencePanel.scheduleMonthly(
      item.schedule_day || "1",
      time,
      timezone,
    );
  }
  return t.intelligencePanel.scheduleDaily(time, timezone);
}

function articleContent(report: IntelligenceReport, t: Translations) {
  if (report.markdown?.trim()) return report.markdown.trim();
  const sections: string[] = [];
  if (report.summary) sections.push(report.summary);
  if (report.findings?.length) {
    sections.push(
      [
        `## ${t.intelligencePanel.keyFindingsHeading}`,
        ...report.findings.map((item) => `- ${item}`),
      ].join("\n"),
    );
  }
  if (report.recommendations?.length) {
    sections.push(
      [
        `## ${t.intelligencePanel.recommendationsHeading}`,
        ...report.recommendations.map((item) => `- ${item}`),
      ].join("\n"),
    );
  }
  return sections.join("\n\n");
}

function reportKey(report: IntelligenceReport, index: number) {
  return report.id ?? `${report.topic ?? "report"}-${index}`;
}

function ReportCover({
  label,
  compact = false,
}: {
  label: string;
  compact?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div
      className={cn(
        "relative isolate overflow-hidden rounded-lg border border-border-default bg-foreground text-white shadow-[var(--shadow-xs)]",
        compact ? "h-20" : "h-36",
      )}
    >
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(56,189,248,0.55),transparent_28%),radial-gradient(circle_at_78%_35%,rgba(99,102,241,0.42),transparent_30%),linear-gradient(135deg,rgba(15,23,42,1),rgba(17,24,39,0.92))]" />
      <div className="absolute inset-0 opacity-35 [background-image:linear-gradient(rgba(255,255,255,0.12)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.12)_1px,transparent_1px)] [background-size:28px_28px]" />
      <div className="absolute left-2 top-2 rounded-md bg-black/45 px-1.5 py-0.5 text-xs font-medium text-white/90">
        {t.intelligencePanel.aiGenerated}
      </div>
      <div className="absolute bottom-2 left-2 right-2 line-clamp-2 text-xs font-semibold leading-5">
        {label}
      </div>
    </div>
  );
}

function ReportTimelineItem({
  report,
  index,
  selected,
  fallback,
  onSelect,
}: {
  report: IntelligenceReport;
  index: number;
  selected: boolean;
  fallback: string;
  onSelect: () => void;
}) {
  const { t } = useI18n();
  const date = reportDateParts(report.created_at, t);
  const headline = reportHeadline(report, fallback);
  const topic = reportTopic(report, fallback);
  const preview = reportPreview(report);
  const first = index === 0;
  return (
    <div className="grid grid-cols-[3.75rem_minmax(0,1fr)] gap-2.5">
      <div className="relative flex flex-col items-center pt-1 text-center">
        <div className="text-base font-semibold leading-none text-foreground">
          {date.time}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">
          {date.shortDate}
        </div>
        <span className="mt-2 size-2.5 rounded-full bg-foreground" />
        <span className="mt-1 h-full min-h-10 w-px bg-border" />
      </div>
      <button
        type="button"
        onClick={onSelect}
        className={cn(
          "group rounded-lg border p-2.5 text-left transition-colors",
          selected
            ? "border-primary/35 bg-primary/5"
            : "border-border-default bg-card/70 hover:border-border hover:bg-card",
        )}
      >
        <div
          className={cn(
            "grid gap-2.5",
            first
              ? "md:grid-cols-[10rem_minmax(0,1fr)]"
              : "grid-cols-[4.8rem_minmax(0,1fr)]",
          )}
        >
          <ReportCover label={topic} compact={!first} />
          <div className="min-w-0">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div
                  className={cn(
                    "line-clamp-2 font-semibold leading-6",
                    first ? "text-base" : "text-sm",
                  )}
                >
                  {headline}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  <span>
                    {date.monthDay} {date.weekday}
                  </span>
                  {typeof report.items_analyzed === "number" && (
                    <span>
                      {t.intelligencePanel.itemsCount(report.items_analyzed)}
                    </span>
                  )}
                  {typeof report.skills_created === "number" && (
                    <span>
                      {t.intelligencePanel.skillsCount(report.skills_created)}
                    </span>
                  )}
                </div>
              </div>
              <span className="inline-flex h-6 shrink-0 items-center gap-1 rounded-md bg-muted px-2 text-xs text-muted-foreground transition-colors group-hover:text-foreground">
                {t.intelligencePanel.view}
                <ArrowRightIcon className="size-3" />
              </span>
            </div>
            {preview && (
              <p
                className={cn(
                  "mt-2 text-xs leading-5 text-muted-foreground",
                  first ? "line-clamp-3" : "line-clamp-2",
                )}
              >
                {preview}
              </p>
            )}
          </div>
        </div>
      </button>
    </div>
  );
}

export function IntelligencePanel() {
  const { locale, t } = useI18n();
  const queryClient = useQueryClient();
  const [goal, setGoal] = useState("");
  const [draft, setDraft] = useState<SubscriptionDraft | null>(null);
  const [builderOpen, setBuilderOpen] = useState(true);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [subscriptionToDelete, setSubscriptionToDelete] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const builderRegionId = useId();
  const goalId = useId();
  const draftNameId = useId();
  const draftKeywordsId = useId();
  const draftCadenceId = useId();
  const draftSourcesId = useId();
  const draftTimeId = useId();
  const draftDayId = useId();
  const draftTimezoneId = useId();
  const draftInstructionsId = useId();

  const cadenceOptions = useMemo(
    () => [
      t.intelligencePanel.cadenceHighFrequency,
      t.intelligencePanel.cadenceDaily,
      t.intelligencePanel.cadenceWeekly,
      t.intelligencePanel.cadenceMonthly,
    ],
    [t],
  );
  const weekdayOptions = useMemo(
    () => [
      { value: "1", label: t.intelligencePanel.weekdayMonday },
      { value: "2", label: t.intelligencePanel.weekdayTuesday },
      { value: "3", label: t.intelligencePanel.weekdayWednesday },
      { value: "4", label: t.intelligencePanel.weekdayThursday },
      { value: "5", label: t.intelligencePanel.weekdayFriday },
      { value: "6", label: t.intelligencePanel.weekdaySaturday },
      { value: "7", label: t.intelligencePanel.weekdaySunday },
    ],
    [t],
  );

  const subscriptionsQuery = useQuery({
    queryKey: subscriptionsKey,
    queryFn: async () => {
      const data = await apiFetch<{
        subscriptions?: IntelligenceSubscription[];
      }>("/api/intelligence/subscriptions");
      return data.subscriptions ?? [];
    },
  });

  const reportsQuery = useQuery({
    queryKey: reportsKey,
    queryFn: async () => {
      const data = await apiFetch<{ reports?: IntelligenceReport[] }>(
        "/api/intelligence/reports",
      );
      return data.reports ?? [];
    },
  });

  const createSubscription = useMutation({
    mutationFn: (payload: {
      topic: string;
      display_name?: string;
      keywords?: string[];
      cadence?: string;
      schedule_time?: string;
      schedule_day?: string;
      timezone?: string;
      instructions?: string;
      sources?: string[];
    }) =>
      apiFetch<IntelligenceSubscription>("/api/intelligence/subscriptions", {
        method: "POST",
        body: JSON.stringify({
          topic: payload.topic,
          display_name: payload.display_name ?? payload.topic,
          keywords: payload.keywords?.length
            ? payload.keywords
            : [payload.topic],
          cadence: payload.cadence,
          schedule_time: payload.schedule_time,
          schedule_day: payload.schedule_day,
          timezone: payload.timezone,
          instructions: payload.instructions,
          sources: payload.sources,
          enabled: true,
        }),
      }),
    onSuccess: () => {
      setGoal("");
      setDraft(null);
      setBuilderOpen(false);
      toast.success(t.intelligence.subscriptionAdded);
      void queryClient.invalidateQueries({ queryKey: subscriptionsKey });
    },
    onError: () => {
      toast.error(t.intelligence.addFailed);
    },
  });

  const draftSubscription = useMutation({
    mutationFn: (nextGoal: string) =>
      apiFetch<{ draft: SubscriptionDraft }>(
        "/api/intelligence/subscriptions/draft",
        {
          method: "POST",
          body: JSON.stringify({ goal: nextGoal }),
        },
      ),
    onSuccess: (data, nextGoal) =>
      setDraft(
        normalizeDraft(
          {
            ...data.draft,
            ...inferScheduleFromGoal(nextGoal, t),
          },
          t.intelligencePanel.cadenceDaily,
        ),
      ),
    onError: () => toast.error(t.intelligence.addFailed),
  });

  const updateSubscription = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      apiFetch<IntelligenceSubscription>(
        `/api/intelligence/subscriptions/${encodeURIComponent(id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ enabled }),
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: subscriptionsKey });
    },
    onError: () => toast.error(t.intelligence.updateFailed),
  });

  const deleteSubscription = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ ok: boolean; id: string }>(
        `/api/intelligence/subscriptions/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      ),
    onSuccess: () => {
      toast.success(t.intelligence.subscriptionDeleted);
      void queryClient.invalidateQueries({ queryKey: subscriptionsKey });
      setSubscriptionToDelete(null);
    },
    onError: () => toast.error(t.intelligence.deleteFailed),
  });

  const runSubscription = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{
        ok: boolean;
        subscription: IntelligenceSubscription;
        report: IntelligenceReport;
      }>(`/api/intelligence/subscriptions/${encodeURIComponent(id)}/run`, {
        method: "POST",
        body: JSON.stringify({}),
      }),
    onSuccess: (data) => {
      toast.success(
        t.intelligence.reportGenerated(data.report.items_analyzed ?? 0),
      );
      void queryClient.invalidateQueries({ queryKey: subscriptionsKey });
      void queryClient.invalidateQueries({ queryKey: reportsKey });
      if (data.report.id) setSelectedReportId(data.report.id);
    },
    onError: () => toast.error(t.intelligence.runSubscriptionFailed),
  });

  const runAllSubscriptions = useMutation({
    mutationFn: () =>
      apiFetch<{ ok: boolean; reports_count: number }>(
        "/api/intelligence/run",
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      ),
    onSuccess: (data) => {
      toast.success(t.intelligence.reportsGenerated(data.reports_count));
      void queryClient.invalidateQueries({ queryKey: subscriptionsKey });
      void queryClient.invalidateQueries({ queryKey: reportsKey });
    },
    onError: () => toast.error(t.intelligence.runAllSubscriptionsFailed),
  });

  const subscriptions = subscriptionsQuery.data ?? EMPTY_SUBSCRIPTIONS;
  const reports = reportsQuery.data ?? EMPTY_REPORTS;
  const loading = subscriptionsQuery.isLoading || reportsQuery.isLoading;
  const loadError = subscriptionsQuery.isError || reportsQuery.isError;
  const enabledCount = subscriptions.filter(
    (item) => item.enabled !== false,
  ).length;
  const runningSubscriptionId = runSubscription.isPending
    ? runSubscription.variables
    : null;
  const updatingSubscriptionId = updateSubscription.isPending
    ? updateSubscription.variables?.id
    : null;
  const deletingSubscriptionId = deleteSubscription.isPending
    ? deleteSubscription.variables
    : null;
  const draftReady = Boolean(
    draft?.topic.trim() &&
    draft.display_name.trim() &&
    draft.keywords.length > 0 &&
    draft.timezone.trim(),
  );
  useEffect(() => {
    if (loading || loadError || subscriptions.length > 0) return;
    setBuilderOpen(true);
  }, [loadError, loading, subscriptions.length]);

  const selectedReport = useMemo(() => {
    if (!reports.length) return null;
    return (
      reports.find((report, index) => {
        return reportKey(report, index) === selectedReportId;
      }) ??
      reports[0] ??
      null
    );
  }, [reports, selectedReportId]);

  const selectedReportKey = selectedReport
    ? reportKey(selectedReport, reports.indexOf(selectedReport))
    : null;

  const reportBody = useMemo(() => {
    if (!selectedReport) return "";
    return articleContent(selectedReport, t);
  }, [selectedReport, t]);

  const handleCreateDraft = () => {
    if (!draft) return;
    createSubscription.mutate(draft);
  };

  const handleUseExample = (example: string) => {
    setGoal(example);
    setDraft(null);
    setBuilderOpen(true);
  };

  if (loading) {
    return (
      <div
        role="status"
        className="flex min-h-64 items-center justify-center gap-2 text-muted-foreground"
      >
        <Loader2Icon className="size-5 animate-spin" />
        <span>{t.common.loading}</span>
      </div>
    );
  }

  if (loadError) {
    return (
      <div
        role="alert"
        className="flex flex-col items-start justify-between gap-3 rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive sm:flex-row sm:items-center"
      >
        <span>{t.intelligence.loadFailed}</span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void subscriptionsQuery.refetch();
            void reportsQuery.refetch();
          }}
        >
          <RefreshCwIcon className="mr-1.5 size-3.5" />
          {t.intelligence.retry}
        </Button>
      </div>
    );
  }

  return (
    <div data-testid="intelligence-panel" className="space-y-3.5">
      <section className="rounded-lg border border-border-default bg-card/40">
        <button
          type="button"
          onClick={() => setBuilderOpen((value) => !value)}
          aria-expanded={builderOpen}
          aria-controls={builderRegionId}
          className="flex w-full items-center justify-between gap-2 px-2.5 py-1 text-left"
        >
          <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <SparklesIcon className="size-3 text-primary/70" />
            {t.intelligence.aiCustomSubscription}
          </div>
          <ChevronDownIcon
            className={cn(
              "size-3.5 text-muted-foreground transition-transform",
              builderOpen && "rotate-180",
            )}
          />
        </button>

        {builderOpen && (
          <div
            id={builderRegionId}
            className="grid gap-2.5 border-t border-border-default px-3 pb-3 pt-2.5 lg:grid-cols-[minmax(0,1fr)_minmax(17rem,0.68fr)]"
          >
            <div className="space-y-2">
              <Label htmlFor={goalId} className="text-xs">
                {t.intelligencePanel.goalLabel}
              </Label>
              <Textarea
                id={goalId}
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                placeholder={t.intelligencePanel.goalPlaceholder}
                className="min-h-24 resize-none bg-background/60 text-sm leading-5"
              />
              <div className="flex flex-wrap gap-2">
                {t.intelligencePanel.examplePrompts.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => handleUseExample(example)}
                    aria-label={example}
                    className="rounded-full border border-border-default bg-background/70 px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/30 hover:text-foreground"
                  >
                    {example.slice(0, 18)}...
                  </button>
                ))}
              </div>
              <Button
                size="sm"
                className="h-7 gap-1.5 rounded-lg px-3"
                disabled={!goal.trim() || draftSubscription.isPending}
                onClick={() => draftSubscription.mutate(goal.trim())}
              >
                {draftSubscription.isPending ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <SparklesIcon className="size-3.5" />
                )}
                {t.intelligence.generateDraft}
              </Button>
            </div>

            <div className="rounded-lg border border-border-default bg-muted/20 p-2.5">
              {draft ? (
                <div className="space-y-3">
                  <div className="space-y-1">
                    <Label htmlFor={draftNameId} className="text-xs">
                      {t.intelligencePanel.subscriptionName}
                    </Label>
                    <Input
                      id={draftNameId}
                      value={draft.display_name}
                      onChange={(event) =>
                        setDraft((current) =>
                          current
                            ? {
                                ...current,
                                display_name: event.target.value,
                                topic: event.target.value,
                              }
                            : current,
                        )
                      }
                      className="h-7 bg-background/75 text-sm font-medium"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={draftKeywordsId} className="text-xs">
                      {t.intelligencePanel.keywords}
                    </Label>
                    <Input
                      id={draftKeywordsId}
                      value={draft.keywords.join(", ")}
                      onChange={(event) =>
                        setDraft((current) =>
                          current
                            ? {
                                ...current,
                                keywords: event.target.value
                                  .split(/[,，]/)
                                  .map((item) => item.trim())
                                  .filter(Boolean),
                              }
                            : current,
                        )
                      }
                      className="h-7 bg-background/75 text-xs"
                    />
                  </div>
                  <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                    <div className="space-y-1">
                      <Label htmlFor={draftCadenceId} className="text-xs">
                        {t.intelligencePanel.cadence}
                      </Label>
                      <Select
                        value={draft.cadence}
                        onValueChange={(value) =>
                          setDraft((current) =>
                            current ? { ...current, cadence: value } : current,
                          )
                        }
                      >
                        <SelectTrigger
                          id={draftCadenceId}
                          className="h-7 w-full bg-background/75 text-xs"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {cadenceOptions.map((option) => (
                            <SelectItem key={option} value={option}>
                              {option}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor={draftSourcesId} className="text-xs">
                        {t.intelligencePanel.sources}
                      </Label>
                      <Input
                        id={draftSourcesId}
                        value={draft.sources.join(", ")}
                        onChange={(event) =>
                          setDraft((current) =>
                            current
                              ? {
                                  ...current,
                                  sources: event.target.value
                                    .split(/[,，]/)
                                    .map((item) => item.trim())
                                    .filter(Boolean),
                                }
                              : current,
                          )
                        }
                        className="h-7 bg-background/75 text-xs"
                      />
                    </div>
                  </div>
                  <div className="grid gap-1.5 md:grid-cols-3">
                    <div className="space-y-1">
                      <Label
                        htmlFor={draftTimeId}
                        className="flex items-center gap-1 text-xs text-muted-foreground"
                      >
                        <ClockIcon className="size-3" />
                        {t.intelligencePanel.runTime}
                      </Label>
                      <Input
                        id={draftTimeId}
                        type="time"
                        value={draft.schedule_time}
                        onChange={(event) =>
                          setDraft((current) =>
                            current
                              ? {
                                  ...current,
                                  schedule_time: event.target.value,
                                }
                              : current,
                          )
                        }
                        disabled={
                          draft.cadence ===
                          t.intelligencePanel.cadenceHighFrequency
                        }
                        className="h-7 bg-background/75 text-xs"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label
                        htmlFor={draftDayId}
                        className="text-xs text-muted-foreground"
                      >
                        {draft.cadence === t.intelligencePanel.cadenceMonthly
                          ? t.intelligencePanel.monthlyDate
                          : t.intelligencePanel.weeklyDate}
                      </Label>
                      {draft.cadence === t.intelligencePanel.cadenceMonthly ? (
                        <Select
                          value={draft.schedule_day}
                          onValueChange={(value) =>
                            setDraft((current) =>
                              current
                                ? { ...current, schedule_day: value }
                                : current,
                            )
                          }
                        >
                          <SelectTrigger
                            id={draftDayId}
                            className="h-7 w-full bg-background/75 text-xs"
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {MONTHDAY_OPTIONS.map((day) => (
                              <SelectItem key={day} value={day}>
                                {t.intelligencePanel.monthDayLabel(day)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Select
                          value={draft.schedule_day}
                          onValueChange={(value) =>
                            setDraft((current) =>
                              current
                                ? { ...current, schedule_day: value }
                                : current,
                            )
                          }
                          disabled={
                            draft.cadence !== t.intelligencePanel.cadenceWeekly
                          }
                        >
                          <SelectTrigger
                            id={draftDayId}
                            className="h-7 w-full bg-background/75 text-xs"
                          >
                            <SelectValue placeholder="-" />
                          </SelectTrigger>
                          <SelectContent>
                            {weekdayOptions.map((option) => (
                              <SelectItem
                                key={option.value}
                                value={option.value}
                              >
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </div>
                    <div className="space-y-1">
                      <Label
                        htmlFor={draftTimezoneId}
                        className="text-xs text-muted-foreground"
                      >
                        {t.intelligencePanel.timezone}
                      </Label>
                      <Input
                        id={draftTimezoneId}
                        value={draft.timezone}
                        onChange={(event) =>
                          setDraft((current) =>
                            current
                              ? { ...current, timezone: event.target.value }
                              : current,
                          )
                        }
                        className="h-7 bg-background/75 text-xs"
                      />
                    </div>
                  </div>
                  <div className="rounded-md border border-border-default bg-background/50 px-2.5 py-1.5 text-xs text-muted-foreground">
                    {t.intelligencePanel.expectedRun(scheduleText(draft, t))}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={draftInstructionsId} className="text-xs">
                      {t.intelligencePanel.instructions}
                    </Label>
                    <Textarea
                      id={draftInstructionsId}
                      value={draft.instructions}
                      onChange={(event) =>
                        setDraft((current) =>
                          current
                            ? { ...current, instructions: event.target.value }
                            : current,
                        )
                      }
                      className="min-h-[4.5rem] resize-none bg-background/75 text-xs leading-5"
                    />
                  </div>
                  <Button
                    size="sm"
                    className="h-7 w-full gap-1.5 rounded-lg px-3"
                    disabled={!draftReady || createSubscription.isPending}
                    onClick={handleCreateDraft}
                  >
                    {createSubscription.isPending ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <PlusIcon className="size-3.5" />
                    )}
                    {t.intelligence.createSubscription}
                  </Button>
                </div>
              ) : (
                <div className="flex h-full min-h-32 flex-col justify-center rounded-md border border-dashed border-border-default px-4 py-3 text-sm text-muted-foreground">
                  <div className="font-medium text-foreground">
                    {t.intelligence.draftPlaceholder}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      <div className="grid gap-3.5 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
        <section className="flex min-w-0 flex-col gap-2.5">
          <div className="flex flex-col items-stretch justify-between gap-3 md:flex-row md:items-center">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <RadarIcon className="size-4 text-primary" />
                {t.intelligence.subscriptionsHeader}
              </div>
              <div className="text-xs text-muted-foreground">
                {enabledCount} {t.intelligence.enabledTopics}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center">
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 rounded-lg px-3"
                disabled={enabledCount === 0 || runAllSubscriptions.isPending}
                onClick={() => runAllSubscriptions.mutate()}
              >
                {runAllSubscriptions.isPending ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCwIcon className="size-3.5" />
                )}
                {t.intelligence.runAll}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 rounded-lg px-3"
                onClick={() => setBuilderOpen(true)}
              >
                <PlusIcon className="size-3.5" />
                {t.intelligence.addSubscription}
              </Button>
            </div>
          </div>

          {subscriptions.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border-default bg-card/50 p-4">
              <div className="flex items-start gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <RadarIcon className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold">
                    {t.intelligencePanel.noSubscriptionsYet}
                  </div>
                  <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
                    {t.intelligence.noSubscriptionsHint(
                      t.intelligence.exampleKeyword,
                    )}
                  </p>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {t.intelligencePanel.examplePrompts
                  .slice(0, 3)
                  .map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => handleUseExample(example)}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border-default bg-background/70 px-2.5 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:border-primary/30 hover:bg-primary/5 hover:text-foreground"
                    >
                      <SparklesIcon className="size-3 shrink-0 text-primary/70" />
                      <span className="truncate">{example}</span>
                    </button>
                  ))}
              </div>
            </div>
          ) : (
            <div className="space-y-1.5">
              {subscriptions.map((item, index) => {
                const enabled = item.enabled !== false;
                const title = item.display_name || item.topic;
                const reportCount = reports.filter(
                  (report) =>
                    report.topic === item.topic || report.title === title,
                ).length;
                return (
                  <div
                    key={item.id}
                    className={cn(
                      "w-full rounded-lg border px-3 py-2.5 transition-colors",
                      selectedReportKey &&
                        (selectedReport?.topic === item.topic ||
                          selectedReport?.title === title)
                        ? "border-primary/30 bg-primary/5"
                        : "border-border-default bg-card/60 hover:border-border hover:bg-card",
                    )}
                  >
                    <div className="flex flex-col gap-2.5 md:flex-row md:items-start">
                      <button
                        type="button"
                        className="flex min-w-0 flex-1 items-start gap-2.5 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                        aria-label={t.intelligence.selectSubscription(title)}
                        onClick={() => {
                          const matched = reports.find(
                            (report) =>
                              report.topic === item.topic ||
                              report.title === title,
                          );
                          setSelectedReportId(matched?.id ?? null);
                        }}
                      >
                        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/80 text-xs font-semibold text-foreground">
                          {String(index + 1).padStart(2, "0")}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <div className="truncate text-sm font-medium">
                              {title}
                            </div>
                            <Badge
                              variant={enabled ? "secondary" : "outline"}
                              className="rounded-full px-2 py-0.5 text-xs"
                            >
                              {enabled
                                ? t.intelligence.enabled
                                : t.intelligence.disabled}
                            </Badge>
                          </div>
                          <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
                            <span>
                              {t.intelligence.keywordsPrefix}{" "}
                              {(item.keywords?.length
                                ? item.keywords
                                : [item.topic]
                              )
                                .filter(Boolean)
                                .join(", ")}
                            </span>
                            <span>{scheduleText(item, t)}</span>
                            <span>
                              {reportCount} {t.intelligence.reports}
                            </span>
                            <span>
                              {item.last_run
                                ? t.intelligence.lastRunPrefix(
                                    fmtDate(item.last_run, locale),
                                  )
                                : t.intelligence.neverRun}
                            </span>
                          </div>
                          {item.instructions && (
                            <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-muted-foreground/80">
                              {item.instructions}
                            </p>
                          )}
                        </div>
                      </button>
                      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 rounded-lg p-0 text-muted-foreground hover:bg-muted hover:text-foreground"
                          disabled={!enabled || runSubscription.isPending}
                          onClick={(event) => {
                            event.stopPropagation();
                            runSubscription.mutate(item.id);
                          }}
                          title={t.intelligence.runNow}
                          aria-label={t.intelligence.runSubscription(title)}
                        >
                          {runningSubscriptionId === item.id ? (
                            <Loader2Icon className="size-3.5 animate-spin" />
                          ) : (
                            <RefreshCwIcon className="size-3.5" />
                          )}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className={cn(
                            "h-7 rounded-lg px-2 text-xs",
                            enabled
                              ? "border-success/30 text-success"
                              : "text-muted-foreground",
                          )}
                          disabled={updateSubscription.isPending}
                          onClick={(event) => {
                            event.stopPropagation();
                            updateSubscription.mutate({
                              id: item.id,
                              enabled: !enabled,
                            });
                          }}
                          aria-label={
                            enabled
                              ? t.intelligence.disableSubscription(title)
                              : t.intelligence.enableSubscription(title)
                          }
                        >
                          {updatingSubscriptionId === item.id ? (
                            <Loader2Icon className="size-3.5 animate-spin" />
                          ) : enabled ? (
                            t.intelligence.enabled
                          ) : (
                            t.intelligence.disabled
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 rounded-lg p-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                          disabled={deleteSubscription.isPending}
                          onClick={(event) => {
                            event.stopPropagation();
                            setSubscriptionToDelete({ id: item.id, title });
                          }}
                          title={t.intelligence.deleteSubscription}
                          aria-label={t.intelligence.deleteSubscriptionNamed(
                            title,
                          )}
                        >
                          {deletingSubscriptionId === item.id ? (
                            <Loader2Icon className="size-3.5 animate-spin" />
                          ) : (
                            <Trash2Icon className="size-3.5" />
                          )}
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section className="min-w-0 overflow-hidden rounded-lg border border-border-default bg-card/60">
          <div className="flex items-center justify-between gap-3 border-b border-border-default px-3 py-2.5">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <NewspaperIcon className="size-4 text-primary" />
                {t.intelligencePanel.latestUpdates}
              </div>
              <div className="text-xs text-muted-foreground">
                {reports.length} {t.intelligence.reports} ·{" "}
                {t.intelligencePanel.sortedBySubscriptionPush}
              </div>
            </div>
            <Badge
              variant="secondary"
              className="rounded-md px-2 py-0.5 text-xs"
            >
              {t.intelligencePanel.newsFeed}
            </Badge>
          </div>

          {reports.length === 0 ? (
            <div className="m-3 rounded-lg border border-dashed border-border-default bg-background/45 p-4">
              <div className="flex items-start gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                  <NewspaperIcon className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold">
                    {t.intelligencePanel.noReportsYet}
                  </div>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    {t.intelligence.noReportsHint}
                  </p>
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="mt-3 h-7 gap-1.5 rounded-lg px-3"
                disabled={enabledCount === 0 || runAllSubscriptions.isPending}
                onClick={() => runAllSubscriptions.mutate()}
              >
                {runAllSubscriptions.isPending ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCwIcon className="size-3.5" />
                )}
                {t.intelligence.runAll}
              </Button>
            </div>
          ) : (
            <div className="space-y-2.5 px-3 py-3">
              <div className="rounded-lg border border-border-default bg-background/55 px-3 py-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">
                  {t.intelligencePanel.trackingNow}
                </span>
                <span className="mx-2 text-border">/</span>
                {t.intelligencePanel.reportTimelineHint}
              </div>
              {reports.slice(0, 8).map((report, index) => {
                const key = reportKey(report, index);
                return (
                  <ReportTimelineItem
                    key={key}
                    report={report}
                    index={index}
                    selected={key === selectedReportKey}
                    fallback={t.intelligence.topicReport}
                    onSelect={() => setSelectedReportId(key)}
                  />
                );
              })}
            </div>
          )}
        </section>
      </div>

      {selectedReport && (
        <section className="overflow-hidden rounded-4xl border border-border-default bg-card/70">
          <div className="border-b border-border-default bg-background/40 px-4 py-4">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-start">
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1 rounded-full border border-border-default bg-background/70 px-2.5 py-1 text-foreground">
                    <BellRingIcon className="size-3.5 text-primary" />
                    {t.intelligencePanel.subscriptionTopic}
                  </span>
                  <span>
                    {selectedReport.topic || t.intelligence.topicReport}
                  </span>
                  {selectedReport.created_at && (
                    <span>{fmtDate(selectedReport.created_at, locale)}</span>
                  )}
                  {typeof selectedReport.items_analyzed === "number" && (
                    <span>
                      {t.intelligence.itemsCount(selectedReport.items_analyzed)}
                    </span>
                  )}
                </div>
                <div className="text-2xl font-semibold leading-tight">
                  {selectedReport.title ||
                    selectedReport.topic ||
                    t.intelligence.topicReport}
                </div>
                {selectedReport.summary && (
                  <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
                    {stripMd(selectedReport.summary)}
                  </p>
                )}
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs uppercase tracking-wide text-muted-foreground">
                  <span>{t.intelligencePanel.todayPush}</span>
                  <span>
                    {reportDateParts(selectedReport.created_at, t).monthDay}
                  </span>
                </div>
                <ReportCover
                  label={reportTopic(
                    selectedReport,
                    t.intelligence.topicReport,
                  )}
                />
              </div>
            </div>
          </div>

          <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.42fr)]">
            <article className="min-w-0 border-b border-border-default bg-background/70 px-4 py-5 lg:border-b-0 lg:border-r lg:border-border-default">
              <div className="prose prose-sm max-w-none prose-headings:scroll-mt-24 prose-p:leading-7 prose-li:leading-7 prose-a:text-primary">
                {reportBody ? (
                  <MarkdownContent
                    content={reportBody}
                    isLoading={false}
                    rehypePlugins={[]}
                    chatFontSize="medium"
                    className="text-sm"
                  />
                ) : (
                  <div className="text-sm text-muted-foreground">
                    {t.intelligence.noReportsHint}
                  </div>
                )}
              </div>
            </article>

            <aside className="space-y-4 bg-muted/15 px-4 py-5">
              {Array.isArray(selectedReport.findings) &&
                selectedReport.findings.length > 0 && (
                  <div>
                    <div className="mb-2 text-sm font-medium">
                      {t.intelligence.repoSpotlights}
                    </div>
                    <div className="space-y-2">
                      {selectedReport.findings
                        .slice(0, 5)
                        .map((finding, findingIndex) => (
                          <div
                            key={`${selectedReportKey}-finding-${findingIndex}`}
                            className="rounded-lg border border-border-default bg-background/80 px-3 py-2 text-sm leading-6"
                          >
                            {stripMd(finding)}
                          </div>
                        ))}
                    </div>
                  </div>
                )}

              {Array.isArray(selectedReport.items) &&
                selectedReport.items.length > 0 && (
                  <div>
                    <div className="mb-2 text-sm font-medium">
                      {t.intelligence.source}
                    </div>
                    <div className="space-y-2">
                      {selectedReport.items
                        .slice(0, 6)
                        .map((source, sourceIndex) => (
                          <div
                            key={
                              source.id ??
                              `${selectedReportKey}-source-${sourceIndex}`
                            }
                            className="rounded-lg border border-border-default bg-background/80 p-3"
                          >
                            <div className="flex items-center gap-2">
                              <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                                {source.source ?? t.intelligence.web}
                              </span>
                              {source.url ? (
                                <RoutedWebLink
                                  href={source.url}
                                  openTargetSource="intelligence-source"
                                  className="truncate text-sm font-medium text-foreground hover:underline"
                                >
                                  {source.title || source.url}
                                </RoutedWebLink>
                              ) : (
                                <span className="truncate text-sm font-medium text-foreground">
                                  {source.title}
                                </span>
                              )}
                            </div>
                            {source.snippet && (
                              <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted-foreground">
                                {stripMd(source.snippet)}
                              </p>
                            )}
                          </div>
                        ))}
                    </div>
                  </div>
                )}
            </aside>
          </div>
        </section>
      )}

      <Dialog
        open={subscriptionToDelete !== null}
        onOpenChange={(open) => {
          if (!open && !deleteSubscription.isPending) {
            setSubscriptionToDelete(null);
          }
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="w-[min(360px,calc(100vw-2rem))] gap-3 rounded-lg p-4 shadow-xl sm:max-w-[360px]"
        >
          <DialogHeader className="gap-1 text-left">
            <DialogTitle className="text-base">
              {t.intelligence.deleteConfirmTitle}
            </DialogTitle>
            <DialogDescription className="text-caption leading-5">
              {subscriptionToDelete
                ? t.intelligence.deleteConfirmDescription(
                    subscriptionToDelete.title,
                  )
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-1 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={deleteSubscription.isPending}
              onClick={() => setSubscriptionToDelete(null)}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              disabled={deleteSubscription.isPending}
              onClick={() => {
                if (subscriptionToDelete) {
                  deleteSubscription.mutate(subscriptionToDelete.id);
                }
              }}
            >
              {deleteSubscription.isPending ? (
                <Loader2Icon className="mr-1.5 size-3.5 animate-spin" />
              ) : (
                <Trash2Icon className="mr-1.5 size-3.5" />
              )}
              {t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
