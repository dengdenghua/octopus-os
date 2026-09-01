import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, CloudDownload, Loader2, RefreshCw, Users } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  installCloudExpert,
  listCloudStoreCategories,
  listCloudStoreExperts,
  type CloudExpertAgent,
  type CloudStoreCategory,
} from "@/core/agents/agent-world-api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

// 商城(替换第三方 octoapk 角色商城) → WorkBuddy 专家商城 421 位云端源。
// 数据来自后端 /api/agent-market/cloud/store(见
// runtime/platform/plugins/cloud_expert_store.py + 发布脚本 publish-cloud.py)。

function avatarUrl(value?: string): string | null {
  if (!value) return null;
  if (/^https?:\/\//i.test(value)) return value;
  return `${getBackendBaseURL()}${value.startsWith("/") ? value : `/${value}`}`;
}

const TYPE_STYLE = {
  agent: { badge: "bg-primary/10 text-primary", label: "expert" },
  team: {
    badge: "bg-chart-3/10 text-chart-3 dark:text-chart-3",
    label: "team",
  },
} as const;

/** 首屏渲染上限 + 「加载更多」步长(避免 421 张带图卡片一次性全量渲染)。 */
const PAGE_SIZE = 60;
const EMBEDDED_PAGE_SIZE = 24;

export type WorkBuddyCloudStoreKind = "agent" | "team";

export interface WorkBuddyCloudStorePanelProps {
  /** 外层人才市场的全局搜索词。 */
  searchQuery?: string;
  /** 固定只看专家或专家团；不传时保留原来的面板内切换。 */
  kind?: WorkBuddyCloudStoreKind;
  /** 嵌入人才市场时隐藏重复标题/搜索，并采用更舒展的卡片密度。 */
  embedded?: boolean;
  /** 聚合目录中可隐藏角色/角色团切换，让两者混排并只保留领域筛选。 */
  showTypeFilter?: boolean;
  /** 聚合目录可只提供一个专家团开关，避免恢复完整的类型筛选层。 */
  showTeamFilter?: boolean;
  /** 安装成功后通知外层刷新“角色库”。 */
  onInstalled?: (expert: CloudExpertAgent) => void;
}

/** 安装分步:后端接口为单次 POST,无分步回调,前端按阶段展示文案。 */
type InstallPhase = "confirm" | "download" | "unpack" | "import" | "done";

function ExpertDetailDialog({
  expert,
  open,
  onOpenChange,
  onInstall,
  installing,
}: {
  expert: CloudExpertAgent;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInstall: (expert: CloudExpertAgent) => void;
  installing: boolean;
}) {
  const { t } = useI18n();
  const isTeam = !!expert.is_team;
  const typeStyle = isTeam ? TYPE_STYLE.team : TYPE_STYLE.agent;
  const av = avatarUrl(expert.avatar_url);
  const prompts = expert.quick_prompts?.filter(Boolean) ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <div className="flex items-start gap-3 pr-6">
            {av ? (
              <img
                src={av}
                alt=""
                className="size-12 shrink-0 rounded-lg border border-border-default object-cover"
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = "none";
                }}
              />
            ) : (
              <div className="flex size-12 shrink-0 items-center justify-center rounded-lg border border-border-default bg-muted text-xl">
                {isTeam ? "👥" : "🧑‍💼"}
              </div>
            )}
            <div className="min-w-0">
              <DialogTitle className="text-base">
                {t.store.detailTitle(expert.display_name)}
              </DialogTitle>
              <DialogDescription className="mt-0.5 line-clamp-2 text-xs">
                {expert.profession || expert.description}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <ScrollArea className="max-h-[50vh] pr-3">
          <div className="flex flex-col gap-3">
            {expert.profession ? (
              <div>
                <p className="text-xs font-medium text-muted-foreground">
                  {t.store.detailProfession}
                </p>
                <p className="text-sm">{expert.profession}</p>
              </div>
            ) : null}

            {expert.description ? (
              <div>
                <p className="text-xs font-medium text-muted-foreground">
                  {t.store.detailDescription}
                </p>
                <p className="whitespace-pre-wrap text-sm leading-relaxed">
                  {expert.description}
                </p>
              </div>
            ) : null}

            {expert.tags.length > 0 ? (
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  {t.store.detailTags}
                </p>
                <div className="flex flex-wrap gap-1">
                  {expert.tags.map((tag) => (
                    <Badge
                      key={tag}
                      variant="outline"
                      className="text-[11px] font-normal text-muted-foreground"
                    >
                      {tag}
                    </Badge>
                  ))}
                </div>
              </div>
            ) : null}

            {prompts.length > 0 ? (
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  {t.store.detailQuickPrompts}
                </p>
                <div className="flex flex-col gap-1.5">
                  {prompts.map((p, i) => (
                    <div
                      key={i}
                      className="rounded-md border border-border-default bg-muted/40 px-2.5 py-1.5 text-xs text-foreground/90"
                    >
                      {p}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </ScrollArea>

        <DialogFooter className="flex items-center justify-between gap-2">
          <Badge
            className={cn("border-transparent text-[11px]", typeStyle.badge)}
          >
            {isTeam && <Users className="mr-1 inline size-3 align-[-2px]" />}
            {isTeam ? t.store.expertTypeTeam : t.store.expertTypeAgent}
          </Badge>
          <Button
            size="sm"
            variant={expert.is_installed ? "outline" : "default"}
            className="h-8 rounded-sm px-3 text-xs"
            disabled={installing || expert.is_installed}
            onClick={() => onInstall(expert)}
          >
            {installing ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : expert.is_installed ? (
              <Check className="mr-1 h-3 w-3" />
            ) : (
              <CloudDownload className="mr-1 h-3 w-3" />
            )}
            {installing
              ? t.store.installing
              : expert.is_installed
                ? t.store.detailInstalled
                : t.store.detailInstall}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function InstallProgressDialog({
  expert,
  phase,
  onOpenChange,
}: {
  expert: CloudExpertAgent;
  phase: InstallPhase;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const steps: { key: InstallPhase; label: string }[] = [
    { key: "download", label: t.store.phaseDownload },
    { key: "unpack", label: t.store.phaseUnpack },
    { key: "import", label: t.store.phaseImport },
  ];
  const activeIndex = Math.max(
    0,
    steps.findIndex((s) => s.key === phase),
  );

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle className="text-sm">
            {t.store.installExpertTitle}
          </DialogTitle>
          <DialogDescription className="line-clamp-1 text-xs">
            {expert.display_name}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          {steps.map((s, i) => {
            const state =
              i < activeIndex ? "done" : i === activeIndex ? "active" : "todo";
            return (
              <div key={s.key} className="flex items-center gap-2 text-sm">
                {state === "done" ? (
                  <Check className="size-4 shrink-0 text-primary" />
                ) : state === "active" ? (
                  <Loader2 className="size-4 shrink-0 animate-spin text-primary" />
                ) : (
                  <span className="size-4 shrink-0 rounded-full border border-border-default" />
                )}
                <span
                  className={cn(state === "todo" && "text-muted-foreground")}
                >
                  {s.label}
                </span>
              </div>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ExpertCardSkeleton() {
  return (
    <Card className="gap-2.5 py-3">
      <CardHeader className="flex-row items-center gap-2.5 px-3 pt-0">
        <Skeleton className="size-10 shrink-0 rounded-lg" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <Skeleton className="h-3.5 w-2/3" />
          <Skeleton className="h-3 w-full" />
        </div>
      </CardHeader>
      <div className="flex gap-1 px-3">
        <Skeleton className="h-4 w-10 rounded-full" />
        <Skeleton className="h-4 w-14 rounded-full" />
      </div>
      <CardFooter className="px-3 pb-0">
        <Skeleton className="h-7 w-20 rounded-sm" />
      </CardFooter>
    </Card>
  );
}

export function WorkBuddyCloudStorePanel({
  searchQuery = "",
  kind,
  embedded = false,
  showTypeFilter = true,
  showTeamFilter = false,
  onInstalled,
}: WorkBuddyCloudStorePanelProps = {}) {
  const { t } = useI18n();
  const [experts, setExperts] = useState<CloudExpertAgent[]>([]);
  const [categories, setCategories] = useState<CloudStoreCategory[]>([]);
  const [metaCount, setMetaCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState("all");
  const [typeFilter, setTypeFilter] = useState<"all" | "agent" | "team">("all");
  const [installing, setInstalling] = useState<Record<string, boolean>>({});
  const [installed, setInstalled] = useState<Record<string, boolean>>({});
  // 详情弹窗 + 安装分步弹窗
  const [detailTarget, setDetailTarget] = useState<CloudExpertAgent | null>(
    null,
  );
  const [installTarget, setInstallTarget] = useState<CloudExpertAgent | null>(
    null,
  );
  const [installPhase, setInstallPhase] = useState<InstallPhase>("download");
  // 增量渲染:首屏 PAGE_SIZE,「加载更多」逐步追加
  const pageSize = embedded ? EMBEDDED_PAGE_SIZE : PAGE_SIZE;
  const [visibleCount, setVisibleCount] = useState(pageSize);
  const timersRef = useRef<number[]>([]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const id of timers) window.clearTimeout(id);
    };
  }, []);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        const [storeRes, catRes] = await Promise.all([
          listCloudStoreExperts({ limit: 500, refresh }),
          listCloudStoreCategories(),
        ]);
        setExperts(storeRes.agents);
        setCategories(catRes.categories);
        setMetaCount(
          (catRes.meta?.count as number | undefined) ?? storeRes.total,
        );
        // 标注已安装
        const done: Record<string, boolean> = {};
        for (const e of storeRes.agents) if (e.is_installed) done[e.id] = true;
        setInstalled((prev) => ({ ...prev, ...done }));
        setVisibleCount(pageSize);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [pageSize],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const effectiveTypeFilter =
    kind ?? (showTypeFilter || showTeamFilter ? typeFilter : "all");
  const categoryCounts = useMemo(() => {
    const typeScopedExperts =
      effectiveTypeFilter === "all"
        ? experts
        : experts.filter(
            (expert) =>
              (expert.is_team ? "team" : "agent") === effectiveTypeFilter,
          );
    const counts = new Map<string, number>([["all", typeScopedExperts.length]]);
    for (const e of typeScopedExperts) {
      const key = e.category_id || "all";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [effectiveTypeFilter, experts]);

  const zhName = (n?: { en?: string; zh?: string }): string =>
    n?.zh || n?.en || "";

  const externalQuery = searchQuery.trim().toLowerCase();
  const localQuery = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    return experts.filter((e) => {
      if (activeCategory !== "all" && (e.category_id || "") !== activeCategory)
        return false;
      if (
        effectiveTypeFilter !== "all" &&
        (e.is_team ? "team" : "agent") !== effectiveTypeFilter
      )
        return false;
      const hay = [
        e.display_name,
        e.profession || "",
        e.description,
        e.id,
        ...e.tags,
      ]
        .join(" ")
        .toLowerCase();
      if (externalQuery && !hay.includes(externalQuery)) return false;
      if (localQuery && !hay.includes(localQuery)) return false;
      return true;
    });
  }, [activeCategory, effectiveTypeFilter, experts, externalQuery, localQuery]);

  useEffect(() => {
    setVisibleCount(pageSize);
  }, [
    activeCategory,
    effectiveTypeFilter,
    externalQuery,
    localQuery,
    pageSize,
  ]);

  const visible = filtered.slice(0, visibleCount);
  const hasMore = visibleCount < filtered.length;

  const runInstallFlow = async (expert: CloudExpertAgent) => {
    setInstalling((m) => ({ ...m, [expert.id]: true }));
    setInstallTarget(expert);
    setInstallPhase("download");
    // 后端单次 POST 无分步回调:按时间推进阶段文案,给用户可感知的进度。
    const timers: number[] = [];
    timers.push(
      window.setTimeout(() => setInstallPhase("unpack"), 600),
      window.setTimeout(() => setInstallPhase("import"), 1400),
    );
    timersRef.current.push(...timers);
    setError(null);
    try {
      await installCloudExpert(expert.id);
      setInstalled((m) => ({ ...m, [expert.id]: true }));
      onInstalled?.(expert);
      setInstallPhase("done");
      toast.success(t.store.installSuccess(expert.display_name));
      window.setTimeout(() => setInstallTarget(null), 500);
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      setError(reason);
      toast.error(t.store.installFailed(expert.display_name, reason));
      setInstallTarget(null);
    } finally {
      setInstalling((m) => ({ ...m, [expert.id]: false }));
    }
  };

  /** 卡片/详情里的安装入口:已安装直接忽略,否则进入确认流。 */
  const onInstall = (expert: CloudExpertAgent) => {
    if (installed[expert.id] || expert.is_installed) return;
    // 详情弹窗关闭,打开安装确认
    setDetailTarget(null);
    void runInstallFlow(expert);
  };

  const typeLabel = (e: CloudExpertAgent): string =>
    e.is_team ? t.store.expertTypeTeam : t.store.expertTypeAgent;

  return (
    <div className="space-y-3">
      {/* 面板标题 */}
      {!embedded ? (
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">
            {t.store.expertsPanelTitle}
          </span>
        </div>
      ) : null}

      {/* 分类 + 类型 + 搜索 */}
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div
          data-testid="workbuddy-category-scroll"
          className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 pr-1 [scrollbar-width:none] [-webkit-overflow-scrolling:touch] [&::-webkit-scrollbar]:hidden"
        >
          <Button
            type="button"
            size="sm"
            variant={
              embedded
                ? "ghost"
                : activeCategory === "all"
                  ? "secondary"
                  : "outline"
            }
            onClick={() => {
              setActiveCategory("all");
              if (showTeamFilter) setTypeFilter("all");
            }}
            className={cn(
              "h-8 shrink-0 px-2.5 text-xs",
              embedded &&
                "rounded-md font-normal text-muted-foreground shadow-none",
              embedded &&
                activeCategory === "all" &&
                (!showTeamFilter || typeFilter !== "team") &&
                "bg-muted font-medium text-foreground",
            )}
          >
            {t.store.typeAll}
            {!embedded || showTypeFilter ? (
              <span className="ml-1 text-xs text-muted-foreground">
                {categoryCounts.get("all") ?? 0}
              </span>
            ) : null}
          </Button>
          {!kind && !showTypeFilter && showTeamFilter ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-pressed={typeFilter === "team"}
              onClick={() => {
                setActiveCategory("all");
                setTypeFilter((current) =>
                  current === "team" ? "all" : "team",
                );
              }}
              className={cn(
                "h-8 shrink-0 rounded-md px-2.5 text-xs font-normal text-muted-foreground shadow-none",
                typeFilter === "team" && "bg-muted font-medium text-foreground",
              )}
            >
              {t.store.expertTypeTeam}
            </Button>
          ) : null}
          {categories
            .filter((c) => !kind || (categoryCounts.get(c.id) ?? 0) > 0)
            .map((c) => {
              const count = categoryCounts.get(c.id) ?? 0;
              return (
                <Button
                  key={c.id}
                  type="button"
                  size="sm"
                  variant={
                    embedded
                      ? "ghost"
                      : activeCategory === c.id
                        ? "secondary"
                        : "outline"
                  }
                  onClick={() => {
                    setActiveCategory(c.id);
                    if (showTeamFilter) setTypeFilter("all");
                  }}
                  className={cn(
                    "h-8 shrink-0 px-2.5 text-xs",
                    embedded &&
                      "rounded-md font-normal text-muted-foreground shadow-none",
                    !embedded &&
                      activeCategory === c.id &&
                      "border-primary/35 bg-primary/10 text-foreground",
                    embedded &&
                      activeCategory === c.id &&
                      "bg-muted font-medium text-foreground",
                  )}
                >
                  {zhName(c.name)}
                  {!embedded || showTypeFilter ? (
                    <span className="ml-1 text-xs text-muted-foreground">
                      {count}
                    </span>
                  ) : null}
                </Button>
              );
            })}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {!kind && showTypeFilter ? (
            <div className="flex items-center gap-1">
              {(["all", "agent", "team"] as const).map((tp) => (
                <Button
                  key={tp}
                  type="button"
                  size="sm"
                  variant={typeFilter === tp ? "secondary" : "ghost"}
                  onClick={() => setTypeFilter(tp)}
                  className="h-8 px-2.5 text-xs"
                >
                  {tp === "all"
                    ? t.store.typeAll
                    : tp === "team"
                      ? t.store.expertTypeTeam
                      : t.store.expertTypeAgent}
                </Button>
              ))}
            </div>
          ) : null}
          {!embedded ? (
            <span className="text-xs text-muted-foreground">
              {filtered.length}/{experts.length}
            </span>
          ) : null}
          {!embedded ? (
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t.store.searchExpertsPlaceholder}
              aria-label={t.store.searchExpertsPlaceholder}
              className="h-8 w-40 rounded-md border border-border-default bg-background px-2 text-sm outline-none focus:border-primary/50"
            />
          ) : null}
          {!embedded ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={loading}
              onClick={() => void load(true)}
              title={t.store.refreshTooltip}
            >
              <RefreshCw
                className={cn("size-3.5", loading && "animate-spin")}
              />
            </Button>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="flex items-center justify-between gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <span className="line-clamp-2">{error}</span>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 shrink-0 px-2 text-xs"
            disabled={loading}
            onClick={() => void load()}
          >
            <RefreshCw className="mr-1 size-3" />
            {t.store.retry}
          </Button>
        </div>
      ) : null}

      {loading ? (
        <div
          className={cn(
            "grid grid-cols-1 gap-3 sm:grid-cols-2",
            embedded
              ? "xl:grid-cols-3 min-[1800px]:grid-cols-4"
              : "lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5",
          )}
          aria-label={t.store.expertLoadingAria}
        >
          {Array.from({ length: embedded ? 6 : 10 }).map((_, i) => (
            <ExpertCardSkeleton key={i} />
          ))}
        </div>
      ) : (
        <>
          <div
            className={cn(
              "grid grid-cols-1 gap-3 sm:grid-cols-2",
              embedded
                ? "xl:grid-cols-3 min-[1800px]:grid-cols-4"
                : "lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5",
            )}
          >
            {visible.map((expert) => {
              const isTeam = !!expert.is_team;
              const typeStyle = isTeam ? TYPE_STYLE.team : TYPE_STYLE.agent;
              const done = installed[expert.id] || expert.is_installed;
              const busy = installing[expert.id];
              const av = avatarUrl(expert.avatar_url);
              return (
                <Card
                  key={expert.id}
                  className="gap-2.5 cursor-pointer py-3 transition-colors hover:border-primary/40"
                  onClick={() => setDetailTarget(expert)}
                >
                  <CardHeader className="flex-row items-center gap-2.5 px-3 pt-0">
                    {av ? (
                      <img
                        src={av}
                        alt=""
                        loading="lazy"
                        className="size-10 shrink-0 rounded-lg border border-border-default object-cover"
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display =
                            "none";
                        }}
                      />
                    ) : (
                      <div className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-border-default bg-muted text-base">
                        {isTeam ? "👥" : "🧑‍💼"}
                      </div>
                    )}
                    <div className="min-w-0">
                      <CardTitle className="truncate text-sm">
                        {expert.display_name}
                      </CardTitle>
                      <CardDescription className="truncate text-xs">
                        {expert.profession || expert.description}
                      </CardDescription>
                    </div>
                  </CardHeader>
                  <div className="flex flex-wrap gap-1 px-3">
                    <Badge
                      className={cn(
                        "border-transparent text-[11px]",
                        typeStyle.badge,
                      )}
                    >
                      {isTeam && (
                        <Users className="mr-1 inline size-3 align-[-2px]" />
                      )}
                      {typeLabel(expert)}
                    </Badge>
                    {expert.tags.slice(0, 2).map((tag) => (
                      <Badge
                        key={tag}
                        variant="outline"
                        className="text-[11px] font-normal text-muted-foreground"
                      >
                        {tag}
                      </Badge>
                    ))}
                  </div>
                  <CardFooter className="px-3 pb-0">
                    <Button
                      size="sm"
                      variant={done ? "outline" : "default"}
                      className="h-7 rounded-sm px-3 text-xs"
                      disabled={busy || done}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        onInstall(expert);
                      }}
                    >
                      {busy ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      ) : done ? (
                        <Check className="mr-1 h-3 w-3" />
                      ) : (
                        <CloudDownload className="mr-1 h-3 w-3" />
                      )}
                      {busy
                        ? t.store.installing
                        : done
                          ? t.store.installed
                          : t.store.install}
                    </Button>
                  </CardFooter>
                </Card>
              );
            })}
          </div>

          {visible.length > 0 && (
            <div className="flex justify-center">
              {hasMore ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 px-4 text-xs"
                  onClick={() =>
                    setVisibleCount((c) =>
                      Math.min(c + pageSize, filtered.length),
                    )
                  }
                >
                  {t.store.loadMore}
                  <span className="ml-1 text-muted-foreground">
                    ({visibleCount}/{filtered.length})
                  </span>
                </Button>
              ) : (
                <span className="text-xs text-muted-foreground">
                  {t.store.noMoreItems}
                </span>
              )}
            </div>
          )}
        </>
      )}

      {!loading && !error && filtered.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          {t.store.noMatchExperts(metaCount ?? 0)}
        </div>
      ) : null}

      {/* 详情弹窗 */}
      {detailTarget && (
        <ExpertDetailDialog
          expert={detailTarget}
          open
          onOpenChange={(open) => {
            if (!open) setDetailTarget(null);
          }}
          onInstall={onInstall}
          installing={!!(detailTarget && installing[detailTarget.id])}
        />
      )}

      {/* 安装进度弹窗(确认后直接进入分步流程) */}
      {installTarget && installPhase !== "done" && (
        <InstallProgressDialog
          expert={installTarget}
          phase={installPhase}
          onOpenChange={() => {
            // 安装进行中不允许关闭,避免用户误以为已取消
          }}
        />
      )}
    </div>
  );
}
