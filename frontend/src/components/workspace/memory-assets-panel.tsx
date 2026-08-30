import { useDeferredValue, useMemo, useState } from "react";
import {
  BrainIcon,
  ChevronRightIcon,
  CircleGaugeIcon,
  DatabaseIcon,
  FileClockIcon,
  FingerprintIcon,
  Loader2Icon,
  LockIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldCheckIcon,
  UsersIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  useMemoryAssets,
  useMemoryAssetTrace,
  useUpdateMemoryFact,
} from "@/core/memory/hooks";
import type {
  MemoryAsset,
  MemoryAssetType,
  MemoryLayer,
  MemoryVisibility,
} from "@/core/memory/types";
import { cn } from "@/lib/utils";

const LAYERS: Array<{ value: "" | MemoryLayer; label: string; hint: string }> =
  [
    { value: "", label: "全部层级", hint: "" },
    { value: "L0", label: "L0 原文", hint: "对话与原始证据" },
    { value: "L1", label: "L1 事实", hint: "偏好、约束与事件" },
    { value: "L2", label: "L2 场景", hint: "项目与任务经验" },
    { value: "L3", label: "L3 核心", hint: "稳定认知与画像" },
  ];

const TYPE_LABELS: Record<MemoryAssetType, string> = {
  conversation: "对话",
  atom: "事实",
  scenario: "场景",
  persona: "画像",
  skill: "技能",
  wiki: "Wiki",
  code_graph: "代码图谱",
  media: "媒体",
};

const VISIBILITY_LABELS: Record<MemoryVisibility, string> = {
  private: "仅自己",
  team: "团队",
  restricted: "指定成员",
  agent: "指定 Agent",
};

function LayerMark({ layer }: { layer: MemoryLayer }) {
  return (
    <div
      className={cn(
        "grid size-9 shrink-0 place-items-center rounded-lg border text-mini font-semibold",
        layer === "L0" && "border-slate-200 bg-slate-50 text-slate-600",
        layer === "L1" && "border-info bg-info text-info",
        layer === "L2" && "border-success/30 bg-success/5 text-success",
        layer === "L3" && "border-chart-1/30 bg-chart-1/10 text-chart-1",
      )}
    >
      {layer}
    </div>
  );
}

function VisibilityIcon({ visibility }: { visibility: MemoryVisibility }) {
  if (visibility === "private") return <LockIcon className="size-3" />;
  if (visibility === "team") return <UsersIcon className="size-3" />;
  return <ShieldCheckIcon className="size-3" />;
}

function DetailLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-3 py-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words text-foreground">
        {value || "—"}
      </span>
    </div>
  );
}

function AssetDetail({
  asset,
  open,
  onOpenChange,
  onAssetChange,
}: {
  asset: MemoryAsset | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAssetChange: (asset: MemoryAsset) => void;
}) {
  const trace = useMemoryAssetTrace(asset?.id ?? null);
  const updateAsset = useUpdateMemoryFact();

  if (!asset) return null;

  const updateVisibility = async (visibility: MemoryVisibility) => {
    try {
      await updateAsset.mutateAsync({
        factId: asset.id,
        input: { visibility },
      });
      onAssetChange({ ...asset, visibility, version: asset.version + 1 });
      toast.success("共享范围已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新失败");
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="gap-0 sm:max-w-md">
        <SheetHeader className="border-b border-border pr-12">
          <div className="flex items-center gap-2">
            <LayerMark layer={asset.layer} />
            <div className="min-w-0">
              <SheetTitle className="truncate text-sm">
                {asset.title}
              </SheetTitle>
              <SheetDescription className="mt-0.5 text-xs">
                {TYPE_LABELS[asset.asset_type]} · v{asset.version}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <section className="rounded-xl border border-border bg-muted/20 p-3">
            <div className="mb-1 text-mini font-medium text-muted-foreground">
              记忆内容
            </div>
            <p className="whitespace-pre-wrap text-sm leading-6">
              {asset.content}
            </p>
          </section>

          <section className="mt-4">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold">
              <ShieldCheckIcon className="size-3.5" />
              访问与装备
            </div>
            <div className="rounded-xl border border-border px-3">
              <div className="flex items-center justify-between gap-3 py-2">
                <span className="text-xs text-muted-foreground">共享范围</span>
                <Select
                  value={asset.visibility}
                  onValueChange={(value) =>
                    void updateVisibility(value as MemoryVisibility)
                  }
                  disabled={updateAsset.isPending}
                >
                  <SelectTrigger size="sm" className="h-7 min-w-32 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(VISIBILITY_LABELS).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <DetailLine label="所有者" value={asset.owner} />
              <DetailLine label="团队" value={asset.team_id} />
              <DetailLine
                label="Agent"
                value={[asset.agent_id, ...asset.allowed_agents]
                  .filter(Boolean)
                  .join("、")}
              />
              <DetailLine
                label="授权成员"
                value={[...asset.allowed_users, ...asset.allowed_roles].join(
                  "、",
                )}
              />
            </div>
          </section>

          <section className="mt-4">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold">
              <FingerprintIcon className="size-3.5" />
              来源追溯
            </div>
            <div className="rounded-xl border border-border px-3">
              {trace.isLoading ? (
                <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
                  <Loader2Icon className="size-3.5 animate-spin" /> 正在读取来源
                </div>
              ) : (
                <>
                  <DetailLine
                    label="来源类型"
                    value={
                      trace.data?.source.source_type ??
                      asset.provenance.source_type
                    }
                  />
                  <DetailLine
                    label="来源记录"
                    value={
                      trace.data?.source.source_id ?? asset.provenance.source_id
                    }
                  />
                  <DetailLine
                    label="父级记忆"
                    value={(
                      trace.data?.parent_ids ?? asset.provenance.parent_ids
                    ).join("、")}
                  />
                  <DetailLine
                    label="原始证据"
                    value={
                      trace.data?.source.evidence ?? asset.provenance.evidence
                    }
                  />
                </>
              )}
            </div>
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}

export function MemoryAssetsPanel() {
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim());
  const [layer, setLayer] = useState<"" | MemoryLayer>("");
  const [visibility, setVisibility] = useState<"" | MemoryVisibility>("");
  const [selected, setSelected] = useState<MemoryAsset | null>(null);
  const assets = useMemoryAssets({
    q: deferredQuery,
    layer,
    visibility,
    status: "active",
    limit: 200,
  });

  const items = useMemo(() => assets.data?.items ?? [], [assets.data?.items]);
  const summary = useMemo(
    () => ({
      total: items.length,
      traceable: items.filter(
        (item) =>
          item.provenance.source_id ||
          item.provenance.source_uri ||
          item.provenance.evidence,
      ).length,
      shared: items.filter((item) => item.visibility !== "private").length,
    }),
    [items],
  );

  return (
    <div className="flex min-h-full flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        {[
          { icon: DatabaseIcon, label: "当前资产", value: summary.total },
          { icon: FingerprintIcon, label: "可追溯", value: summary.traceable },
          { icon: UsersIcon, label: "已共享", value: summary.shared },
        ].map(({ icon: Icon, label, value }) => (
          <div
            key={label}
            className="flex min-h-16 items-center gap-3 rounded-xl border border-border bg-card px-3"
          >
            <div className="grid size-8 place-items-center rounded-lg bg-muted text-muted-foreground">
              <Icon className="size-4" />
            </div>
            <div>
              <div className="text-lg font-semibold tabular-nums">{value}</div>
              <div className="text-mini text-muted-foreground">{label}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-2">
        <div className="relative min-w-56 flex-1">
          <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索记忆资产"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索事实、场景、技能或来源"
            className="h-8 rounded-lg border-0 bg-muted/60 pl-8 text-xs shadow-none"
          />
        </div>
        <Select
          value={layer || "all"}
          onValueChange={(value) =>
            setLayer(value === "all" ? "" : (value as MemoryLayer))
          }
        >
          <SelectTrigger size="sm" className="h-8 min-w-28 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {LAYERS.map((item) => (
              <SelectItem key={item.value || "all"} value={item.value || "all"}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={visibility || "all"}
          onValueChange={(value) =>
            setVisibility(value === "all" ? "" : (value as MemoryVisibility))
          }
        >
          <SelectTrigger size="sm" className="h-8 min-w-28 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部权限</SelectItem>
            {Object.entries(VISIBILITY_LABELS).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          aria-label="刷新记忆资产"
          onClick={() => void assets.refetch()}
        >
          <RefreshCwIcon
            className={cn("size-3.5", assets.isFetching && "animate-spin")}
          />
        </Button>
      </div>

      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className="grid grid-cols-[minmax(0,1fr)_110px_110px_36px] border-b border-border bg-muted/40 px-3 py-2 text-mini text-muted-foreground">
          <span>记忆资产</span>
          <span>来源</span>
          <span>访问范围</span>
          <span />
        </div>

        {assets.isLoading ? (
          <div className="flex min-h-48 items-center justify-center text-muted-foreground">
            <Loader2Icon className="size-5 animate-spin" />
          </div>
        ) : assets.isError ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 text-center">
            <CircleGaugeIcon className="size-5 text-muted-foreground" />
            <div className="text-sm font-medium">暂时无法读取记忆资产</div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void assets.refetch()}
            >
              重新加载
            </Button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 text-center">
            <BrainIcon className="size-5 text-muted-foreground" />
            <div className="text-sm font-medium">没有符合条件的记忆</div>
            <div className="text-xs text-muted-foreground">
              新对话中沉淀的事实会自动出现在这里
            </div>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {items.map((asset) => (
              <button
                key={asset.id}
                type="button"
                onClick={() => setSelected(asset)}
                className="grid w-full grid-cols-[minmax(0,1fr)_110px_110px_36px] items-center px-3 py-2.5 text-left transition-colors hover:bg-muted/35 focus-visible:bg-muted/35 focus-visible:outline-none"
              >
                <div className="flex min-w-0 items-center gap-3 pr-4">
                  <LayerMark layer={asset.layer} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">
                        {asset.title}
                      </span>
                      <Badge
                        variant="outline"
                        className="h-5 px-1.5 text-micro"
                      >
                        {TYPE_LABELS[asset.asset_type]}
                      </Badge>
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      {asset.content}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
                  <FileClockIcon className="size-3.5 shrink-0" />
                  <span className="truncate">
                    {asset.provenance.source_type || "手动"}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <VisibilityIcon visibility={asset.visibility} />
                  {VISIBILITY_LABELS[asset.visibility]}
                </div>
                <ChevronRightIcon className="size-4 text-muted-foreground" />
              </button>
            ))}
          </div>
        )}
      </div>

      <AssetDetail
        asset={selected}
        open={Boolean(selected)}
        onOpenChange={(open) => !open && setSelected(null)}
        onAssetChange={setSelected}
      />
    </div>
  );
}
