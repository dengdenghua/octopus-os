/**
 * 企业版角色资产 tab(消费侧前端)。
 *
 * 只读列举企业版(echo-enterprise)托管的角色资产——消费而非 fork。配了
 * ECHO_ENTERPRISE_URL(后端)才有数据,否则显示"未接通"提示。「导入到本地」
 * 是下一步(需后端 scaffold + load),这里先把企业版的角色摆出来可浏览/搜索。
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  installEnterpriseAsset,
  listEnterpriseAssets,
  type EnterpriseAsset,
} from "@/core/agents/agent-world-api";
import { useI18n } from "@/core/i18n/hooks";

export function EnterpriseAssetsTab({ query }: { query?: string }) {
  const { t } = useI18n();
  const [items, setItems] = useState<EnterpriseAsset[]>([]);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState<string | null>(null);

  async function handleInstall(a: EnterpriseAsset) {
    if (installing) return;
    setInstalling(a.id);
    try {
      const r = await installEnterpriseAsset(a.id);
      toast.success(t.enterpriseAssetsTab.importSuccess(r.name || a.name));
    } catch (e) {
      toast.error(
        t.enterpriseAssetsTab.importFailed(
          e instanceof Error ? e.message : String(e),
        ),
      );
    } finally {
      setInstalling(null);
    }
  }

  useEffect(() => {
    let alive = true;
    setLoading(true);
    listEnterpriseAssets({ search: query || undefined })
      .then((r) => {
        if (!alive) return;
        setAvailable(r.available);
        setItems(r.available ? r.items : []);
      })
      .catch(() => {
        if (alive) {
          setAvailable(false);
          setItems([]);
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [query]);

  if (loading) {
    return (
      <div className="py-10 text-center text-xs text-muted-foreground">
        {t.enterpriseAssetsTab.loading}
      </div>
    );
  }
  if (available === false) {
    return (
      <div className="py-10 text-center text-xs leading-relaxed text-muted-foreground">
        {t.enterpriseAssetsTab.notAvailableTitle}
        <br />
        {t.enterpriseAssetsTab.notAvailableHintPrefix}{" "}
        <code className="rounded bg-muted/50 px-1">ECHO_ENTERPRISE_URL</code>{" "}
        {t.enterpriseAssetsTab.notAvailableHintSuffix}
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="py-10 text-center text-xs text-muted-foreground">
        {t.enterpriseAssetsTab.empty}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2 text-xs text-muted-foreground">
        {t.enterpriseAssetsTab.header(items.length)}
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {items.map((a) => (
          <div
            key={a.id}
            className="flex gap-3 rounded-lg border border-border-default bg-muted/20 p-3"
          >
            <div className="shrink-0 text-2xl leading-none">
              {a.icon || "🤖"}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium">{a.name}</span>
                {a.category && (
                  <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
                    {a.category}
                  </span>
                )}
              </div>
              <div className="mt-0.5 line-clamp-2 text-xs leading-snug text-muted-foreground">
                {a.description}
              </div>
              {a.tags && a.tags.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {a.tags.slice(0, 4).map((tag) => (
                    <span
                      key={tag}
                      className="rounded bg-muted/50 px-1 py-0.5 text-xs text-muted-foreground"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => handleInstall(a)}
              disabled={installing !== null}
              className="shrink-0 self-center rounded-md border border-primary/40 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition hover:bg-primary/20 disabled:opacity-50"
            >
              {installing === a.id
                ? t.enterpriseAssetsTab.installing
                : t.enterpriseAssetsTab.install}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
