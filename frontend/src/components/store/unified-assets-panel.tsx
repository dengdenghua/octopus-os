/* Implementation note. */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BoxesIcon,
  BotIcon,
  PuzzleIcon,
  RefreshCwIcon,
  UsersIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  fetchUnifiedAssets,
  syncUnifiedAssets,
  type UnifiedAsset,
  type UnifiedAssetKind,
  type UnifiedAssetSource,
  type UnifiedAssetsSummary,
} from "@/core/agents/agent-world-api";
import { cn } from "@/lib/utils";

// 统一资产面板:插件 / 技能 / 角色 一次看全,不管来自 WorkBuddy / Codex / 本地 / 内置。
// 数据源:GET /api/assets(统一 index.json,runtime/platform/assets/asset_registry.py)。

const KIND_TABS: {
  key: UnifiedAssetKind | "team";
  label: string;
  icon: typeof PuzzleIcon;
}[] = [
  { key: "plugin", label: "插件", icon: PuzzleIcon },
  { key: "skill", label: "技能", icon: BoxesIcon },
  { key: "agent", label: "角色", icon: BotIcon },
  { key: "team", label: "专家团", icon: UsersIcon },
];

const SOURCE_FILTERS: { key: UnifiedAssetSource | "all"; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "codex", label: "Codex" },
  { key: "workbuddy", label: "WorkBuddy" },
  { key: "local", label: "本地" },
  { key: "builtin", label: "内置" },
  { key: "imported", label: "迁移" },
];

const SOURCE_BADGE: Record<UnifiedAssetSource, string> = {
  codex: "bg-sky-500/10 text-sky-600 dark:text-sky-300",
  workbuddy: "bg-rose-500/10 text-rose-600 dark:text-rose-300",
  local: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300",
  builtin: "bg-amber-500/10 text-amber-600 dark:text-amber-300",
  imported: "bg-violet-500/10 text-violet-600 dark:text-violet-300",
};

const SOURCE_LABEL: Record<UnifiedAssetSource, string> = {
  codex: "Codex",
  workbuddy: "WorkBuddy",
  local: "本地",
  builtin: "内置",
  imported: "迁移",
};

const PAGE_SIZE = 60;
type AssetKindFilter = UnifiedAssetKind | "team";

function SourceBadge({ source }: { source: UnifiedAssetSource }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "shrink-0 border-transparent font-medium",
        SOURCE_BADGE[source] ?? SOURCE_BADGE.local,
      )}
    >
      {SOURCE_LABEL[source] ?? source}
    </Badge>
  );
}

function AssetCard({
  asset,
  onOpen,
}: {
  asset: UnifiedAsset;
  onOpen: (asset: UnifiedAsset) => void;
}) {
  const title = asset.name_zh || asset.name || asset.id;
  const desc = asset.description || asset.type || "";
  return (
    <Card
      className="group cursor-pointer transition-colors hover:border-primary/40"
      onClick={() => onOpen(asset)}
    >
      <CardHeader className="flex flex-row items-start gap-3 p-4">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border-default bg-muted text-lg">
          {asset.kind === "plugin"
            ? "🧩"
            : asset.kind === "skill"
              ? "📦"
              : asset.kind === "team"
                ? "👥"
                : "🧑‍💼"}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <CardTitle className="truncate text-sm font-semibold">
              {title}
            </CardTitle>
            <SourceBadge source={asset.source} />
          </div>
          {asset.type ? (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {asset.type}
              {asset.version ? ` · v${asset.version}` : ""}
              {asset.auth_mode ? ` · ${asset.auth_mode} 授权` : ""}
            </p>
          ) : null}
          {asset.skills_count != null && asset.skills_count > 0 ? (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {asset.skills_count} 项内置技能
            </p>
          ) : null}
        </div>
      </CardHeader>
      {desc ? (
        <CardContent className="pb-3 pt-0">
          <CardDescription className="line-clamp-2 text-xs">
            {desc}
          </CardDescription>
        </CardContent>
      ) : null}
    </Card>
  );
}

function DetailDialog({
  asset,
  open,
  onOpenChange,
}: {
  asset: UnifiedAsset | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!asset) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center p-4 transition-opacity",
        open ? "opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      <div
        className="absolute inset-0 bg-black/40"
        onClick={() => onOpenChange(false)}
      />
      <div className="relative w-full max-w-md rounded-xl border border-border-default bg-background p-5 shadow-xl">
        <div className="flex items-center gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-border-default bg-muted text-xl">
            {asset.kind === "plugin"
              ? "🧩"
              : asset.kind === "skill"
                ? "📦"
                : asset.kind === "team"
                  ? "👥"
                  : "🧑‍💼"}
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base font-bold">
              {asset.name_zh || asset.name || asset.id}
            </h3>
            <div className="mt-1 flex items-center gap-1.5">
              <SourceBadge source={asset.source} />
              {asset.type ? (
                <Badge variant="outline">{asset.type}</Badge>
              ) : null}
            </div>
          </div>
        </div>
        {asset.description ? (
          <p className="mt-3 text-sm text-muted-foreground">
            {asset.description}
          </p>
        ) : null}
        <dl className="mt-4 flex flex-col gap-2 text-xs">
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">ID</dt>
            <dd className="font-mono">{asset.id}</dd>
          </div>
          {asset.version ? (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">版本</dt>
              <dd>{asset.version}</dd>
            </div>
          ) : null}
          {asset.author ? (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">作者</dt>
              <dd>{asset.author}</dd>
            </div>
          ) : null}
          {asset.category ? (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">分类</dt>
              <dd>{asset.category}</dd>
            </div>
          ) : null}
          {asset.mcp_servers && asset.mcp_servers.length ? (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">MCP 服务</dt>
              <dd>{asset.mcp_servers.join(", ")}</dd>
            </div>
          ) : null}
          {asset.skills && asset.skills.length ? (
            <div className="flex flex-col gap-1">
              <dt className="text-muted-foreground">内置技能</dt>
              <dd className="flex flex-wrap gap-1">
                {asset.skills.map((s) => (
                  <Badge key={s} variant="secondary" className="font-mono">
                    {s}
                  </Badge>
                ))}
              </dd>
            </div>
          ) : null}
          {asset.origin ? (
            <div className="flex flex-col gap-1">
              <dt className="text-muted-foreground">位置</dt>
              <dd className="break-all font-mono text-[11px]">
                {asset.origin}
              </dd>
            </div>
          ) : null}
        </dl>
        <div className="mt-5 flex justify-end">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </div>
      </div>
    </div>
  );
}

export function UnifiedAssetsPanel({
  searchQuery = "",
  className,
  allowedKinds = ["plugin", "skill", "agent", "team"],
  initialKind = "plugin",
  showSyncAction = true,
}: {
  searchQuery?: string;
  className?: string;
  allowedKinds?: readonly AssetKindFilter[];
  initialKind?: AssetKindFilter;
  showSyncAction?: boolean;
}) {
  const allowedKindsKey = allowedKinds.join("|");
  const visibleKindTabs = useMemo(() => {
    const allowed = new Set<AssetKindFilter>(
      allowedKindsKey.split("|").filter(Boolean) as AssetKindFilter[],
    );
    const tabs = KIND_TABS.filter((tab) => allowed.has(tab.key));
    return tabs.length > 0 ? tabs : KIND_TABS;
  }, [allowedKindsKey]);
  const fallbackKind = visibleKindTabs[0]?.key ?? "plugin";
  const [kind, setKind] = useState<AssetKindFilter>(() =>
    visibleKindTabs.some((tab) => tab.key === initialKind)
      ? initialKind
      : fallbackKind,
  );
  const [source, setSource] = useState<UnifiedAssetSource | "all">("all");
  const [summary, setSummary] = useState<UnifiedAssetsSummary | null>(null);
  const [items, setItems] = useState<UnifiedAsset[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<UnifiedAsset | null>(null);

  useEffect(() => {
    if (!visibleKindTabs.some((tab) => tab.key === kind)) {
      setKind(fallbackKind);
    }
  }, [fallbackKind, kind, visibleKindTabs]);

  const load = useCallback(
    async (
      k: AssetKindFilter,
      s: UnifiedAssetSource | "all",
      q: string,
      offset = 0,
    ) => {
      const append = offset > 0;
      if (append) setLoadingMore(true);
      else setLoading(true);
      setError(null);
      try {
        const res = await fetchUnifiedAssets({
          kind: k === "team" ? "team" : k,
          source: s === "all" ? undefined : s,
          search: q.trim() || undefined,
          limit: PAGE_SIZE,
          offset,
        });
        setItems((current) =>
          append ? [...current, ...res.items] : res.items,
        );
        setTotal(res.total);
        setSummary(res.summary);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (append) setLoadingMore(false);
        else setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(kind, source, searchQuery);
  }, [load, kind, source, searchQuery]);

  const counts = summary?.counts ?? {};
  const totalForKind =
    kind === "team" ? counts.team : (counts[kind] as number | undefined);

  const onSync = async () => {
    setSyncing(true);
    try {
      const res = await syncUnifiedAssets();
      toast.success(
        `统一资产已重建:插件 ${res.counts.plugin ?? 0} / 技能 ${res.counts.skill ?? 0} / 角色 ${res.counts.agent ?? 0}`,
      );
      await load(kind, source, searchQuery);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重建失败");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {visibleKindTabs.length > 1 || showSyncAction ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          {visibleKindTabs.length > 1 ? (
            <Tabs
              value={kind}
              onValueChange={(v) => setKind(v as AssetKindFilter)}
            >
              <TabsList variant="line" className="mb-0">
                {visibleKindTabs.map((tab) => (
                  <TabsTrigger
                    key={tab.key}
                    value={tab.key}
                    className="h-8 gap-1.5 px-3 text-xs"
                  >
                    <tab.icon className="h-3.5 w-3.5" />
                    {tab.label}
                    {totalForKind != null && tab.key === kind ? (
                      <span className="ml-0.5 rounded bg-primary/10 px-1.5 text-[11px] text-primary">
                        {totalForKind}
                      </span>
                    ) : null}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          ) : (
            <span className="text-xs text-muted-foreground">
              {totalForKind != null ? `${totalForKind} 项` : null}
            </span>
          )}
          {showSyncAction ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onSync()}
              disabled={syncing}
              className="shrink-0"
            >
              <RefreshCwIcon
                className={cn("size-3.5", syncing && "animate-spin")}
              />
              重建索引
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-1.5">
        {SOURCE_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setSource(f.key)}
            className={cn(
              "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
              source === f.key
                ? "border-primary/40 bg-primary/10 text-primary"
                : "border-transparent bg-muted/35 text-muted-foreground hover:bg-muted/65 hover:text-foreground",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-dashed border-border-default p-8 text-center text-sm text-muted-foreground">
          没有匹配的资产
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((asset) => (
              <AssetCard
                key={`${asset.kind}:${asset.source}:${asset.id}`}
                asset={asset}
                onOpen={setDetail}
              />
            ))}
          </div>
          {items.length < total ? (
            <Button
              variant="ghost"
              size="sm"
              className="mx-auto"
              disabled={loadingMore}
              onClick={() => void load(kind, source, searchQuery, items.length)}
            >
              {loadingMore ? "加载中…" : `加载更多(${total - items.length})`}
            </Button>
          ) : null}
        </>
      )}

      <DetailDialog
        asset={detail}
        open={detail !== null}
        onOpenChange={(open) => {
          if (!open) setDetail(null);
        }}
      />
    </div>
  );
}
