import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import {
  useClearReachCache,
  useReachStatus,
} from "@/core/reach/use-reach-status";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

const COPY = {
  zh: {
    title: "多平台采集 (Native Reach)",
    desc: "统一搜索和读取 GitHub、YouTube、B站、Reddit、X、小红书、抖音、头条、豆包与 RSS。",
    healthy: "可用",
    collections: "份采集记录",
    clear: "清理搜索缓存",
    cleared: "搜索缓存已清理",
    failed: "暂时无法读取多平台状态",
  },
  en: {
    title: "Multi-platform collection (Native Reach)",
    desc: "Search and read GitHub, YouTube, Bilibili, Reddit, X, Xiaohongshu, Douyin, Toutiao, Doubao and RSS.",
    healthy: "available",
    collections: "collections",
    clear: "Clear search cache",
    cleared: "Search cache cleared",
    failed: "Multi-platform status is unavailable",
  },
};

export function ReachControl() {
  const { locale } = useI18n();
  const t = locale?.startsWith("zh") ? COPY.zh : COPY.en;
  const { status, isLoading, isError, refetch } = useReachStatus();
  const { clear, isPending } = useClearReachCache();
  const healthy = Boolean(status && status.healthy === status.total);

  const clearCache = async () => {
    try {
      await clear();
      toast.success(t.cleared);
    } catch {
      toast.error(t.failed);
    }
  };

  return (
    <div className="rounded-lg border border-border-default bg-card/50 p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className={cn(
                "size-2 shrink-0 rounded-full",
                healthy
                  ? "bg-success"
                  : isLoading
                    ? "animate-pulse bg-warning"
                    : "bg-destructive",
              )}
            />
            <h4 className="text-sm font-medium">{t.title}</h4>
          </div>
          {isError ? (
            <button
              className="mt-1 text-xs text-destructive"
              onClick={refetch}
              type="button"
            >
              {t.failed}
            </button>
          ) : (
            <>
              <p className="mt-1 text-xs leading-snug text-muted-foreground">
                {t.desc}
              </p>
              {status ? (
                <p className="mt-2 text-xs text-muted-foreground">
                  {status.healthy}/{status.total} {t.healthy} ·{" "}
                  {status.collection_count} {t.collections}
                </p>
              ) : null}
            </>
          )}
        </div>
        <Button
          disabled={isLoading || isPending}
          onClick={() => void clearCache()}
          size="sm"
          variant="outline"
        >
          {t.clear}
        </Button>
      </div>
      {status ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {status.channels.map((channel) => (
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px]",
                channel.available
                  ? "border-success/30 text-success"
                  : "border-destructive/30 text-destructive",
              )}
              key={channel.platform}
              title={channel.detail || channel.backend}
            >
              {channel.platform}
              {channel.requires_login ? " · login" : ""}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
