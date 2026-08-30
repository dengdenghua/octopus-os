import {
  CloudIcon,
  InfoIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  PlayCircleIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";

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

const subscriptionsKey = ["intelligence", "subscriptions"] as const;
const reportsKey = ["intelligence", "reports"] as const;
const EMPTY_SUBSCRIPTIONS: IntelligenceSubscription[] = [];
const TIP_DISMISSED_KEY = "echo:automation-tip-dismissed";
const KEEP_AWAKE_KEY = "echo:keep-awake";

function localTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
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

export function AutomationConfiguredTab() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [tipVisible, setTipVisible] = useState(true);
  const [keepAwake, setKeepAwake] = useState(true);
  const [subscriptionToDelete, setSubscriptionToDelete] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const wakeLockRef = useRef<WakeLockSentinel | null>(null);

  useEffect(() => {
    try {
      setTipVisible(localStorage.getItem(TIP_DISMISSED_KEY) !== "true");
      setKeepAwake(localStorage.getItem(KEEP_AWAKE_KEY) !== "false");
    } catch {}
  }, []);

  useEffect(() => {
    let cancelled = false;
    const requestWakeLock = async () => {
      const wakeLock = (
        navigator as Navigator & {
          wakeLock?: {
            request(type: "screen"): Promise<WakeLockSentinel>;
          };
        }
      ).wakeLock;
      if (!keepAwake || !wakeLock) return;
      try {
        const lock = await wakeLock.request("screen");
        if (cancelled) {
          lock.release?.();
          return;
        }
        wakeLockRef.current = lock;
        lock.addEventListener("release", () => {
          if (wakeLockRef.current === lock) wakeLockRef.current = null;
        });
      } catch {}
    };
    const releaseWakeLock = () => {
      if (wakeLockRef.current) {
        wakeLockRef.current.release?.().catch(() => {});
        wakeLockRef.current = null;
      }
    };
    if (keepAwake) {
      requestWakeLock();
      const onVisibility = () => {
        if (
          document.visibilityState === "visible" &&
          keepAwake &&
          !wakeLockRef.current
        ) {
          requestWakeLock();
        }
      };
      document.addEventListener("visibilitychange", onVisibility);
      return () => {
        cancelled = true;
        document.removeEventListener("visibilitychange", onVisibility);
        releaseWakeLock();
      };
    } else {
      releaseWakeLock();
      return () => {
        cancelled = true;
      };
    }
  }, [keepAwake]);

  const dismissTip = () => {
    setTipVisible(false);
    try {
      localStorage.setItem(TIP_DISMISSED_KEY, "true");
    } catch {}
  };

  const toggleKeepAwake = (checked: boolean) => {
    setKeepAwake(checked);
    try {
      localStorage.setItem(KEEP_AWAKE_KEY, checked ? "true" : "false");
    } catch {}
  };

  const subscriptionsQuery = useQuery({
    queryKey: subscriptionsKey,
    queryFn: async () => {
      const data = await apiFetch<{
        subscriptions?: IntelligenceSubscription[];
      }>("/api/intelligence/subscriptions");
      return data.subscriptions ?? [];
    },
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
      apiFetch<{ ok: boolean; report?: { items_analyzed?: number } }>(
        `/api/intelligence/subscriptions/${encodeURIComponent(id)}/run`,
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      ),
    onSuccess: () => {
      toast.success(t.intelligence.runNow);
      void queryClient.invalidateQueries({ queryKey: subscriptionsKey });
      void queryClient.invalidateQueries({ queryKey: reportsKey });
    },
    onError: () => toast.error(t.intelligence.runSubscriptionFailed),
  });

  const subscriptions = subscriptionsQuery.data ?? EMPTY_SUBSCRIPTIONS;
  const loading = subscriptionsQuery.isLoading;
  const runningSubscriptionId = runSubscription.isPending
    ? runSubscription.variables
    : null;
  const updatingSubscriptionId = updateSubscription.isPending
    ? updateSubscription.variables?.id
    : null;

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3 rounded-lg border border-border-default/50 bg-card/30 px-3 py-2">
          <Skeleton className="codex-skeleton size-4 shrink-0 rounded-full" />
          <Skeleton className="codex-skeleton h-3.5 flex-1 rounded" />
          <Skeleton className="codex-skeleton h-5 w-9 shrink-0 rounded-full" />
        </div>
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-3 rounded-lg border border-border-default/50 bg-card/30 px-3 py-2.5"
          >
            <Skeleton className="codex-skeleton size-4 shrink-0 rounded-full" />
            <Skeleton className="codex-skeleton h-4 flex-1 rounded" />
            <Skeleton className="codex-skeleton h-4 w-10 shrink-0 rounded" />
            <Skeleton className="codex-skeleton h-3.5 w-20 shrink-0 rounded" />
            <Skeleton className="codex-skeleton h-5 w-9 shrink-0 rounded-full" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {tipVisible && (
        <div className="flex items-center gap-3 rounded-lg border border-primary/15 bg-primary/5 px-3 py-2">
          <InfoIcon className="size-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1 text-xs text-foreground">
            {t.intelligence.configuredTip}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-muted-foreground">
              {t.intelligence.configuredTipToggle}
            </span>
            <Switch
              checked={keepAwake}
              onCheckedChange={toggleKeepAwake}
              aria-label={t.intelligence.configuredTipToggle}
            />
          </div>
          <button
            type="button"
            onClick={dismissTip}
            className="shrink-0 rounded-md p-0.5 text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground"
            aria-label="关闭提示"
          >
            <XIcon className="size-3.5" />
          </button>
        </div>
      )}

      {subscriptions.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border-default bg-card/50 py-16 text-center">
          <CloudIcon className="size-10 text-muted-foreground/60" />
          <div className="mt-4 text-sm font-medium text-foreground">
            {t.intelligence.configuredEmptyTitle}
          </div>
          <p className="mt-1.5 max-w-md text-xs leading-5 text-muted-foreground">
            {t.intelligence.configuredEmptyDescription}
          </p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {subscriptions.map((item) => {
            const enabled = item.enabled !== false;
            const title = item.display_name || item.topic;
            const isRunning = runningSubscriptionId === item.id;
            const isUpdating = updatingSubscriptionId === item.id;

            return (
              <div
                key={item.id}
                className="group flex items-center gap-3 rounded-lg border border-border-default bg-card/60 px-3 py-2.5 transition-colors hover:bg-card"
              >
                <CloudIcon className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1 truncate text-sm font-medium">
                  {title}
                </div>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {scheduleText(item, t)}
                </span>
                <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 rounded-md"
                    disabled={!enabled || isRunning}
                    onClick={() => runSubscription.mutate(item.id)}
                    title={t.intelligence.runNow}
                  >
                    {isRunning ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <PlayCircleIcon className="size-3.5" />
                    )}
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 rounded-md"
                        aria-label={t.common.more}
                        title={t.common.more}
                      >
                        <MoreHorizontalIcon className="size-3.5" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() =>
                          setSubscriptionToDelete({ id: item.id, title })
                        }
                      >
                        <Trash2Icon />
                        {t.intelligence.deleteSubscription}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
                <Switch
                  checked={enabled}
                  disabled={isUpdating}
                  onCheckedChange={(checked) =>
                    updateSubscription.mutate({ id: item.id, enabled: checked })
                  }
                />
              </div>
            );
          })}
        </div>
      )}

      <Dialog
        open={subscriptionToDelete !== null}
        onOpenChange={(open) => {
          if (!open) setSubscriptionToDelete(null);
        }}
      >
        <DialogContent className="gap-3 rounded-lg p-4 sm:max-w-[420px]">
          <DialogHeader className="gap-1 text-left">
            <DialogTitle className="text-base">
              {t.intelligence.deleteConfirmTitle}
            </DialogTitle>
            <DialogDescription className="text-xs leading-5">
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
              className="rounded-md"
              onClick={() => setSubscriptionToDelete(null)}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              className="rounded-md"
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
