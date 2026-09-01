import { useCallback, useEffect, useState } from "react";
import { Check, Download, Info, Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  installRegistryPlugin,
  listRegistryPlugins,
  registrySlug,
  type RegistryPlugin,
} from "@/core/registry/api";
import { useSkills } from "@/core/skills/hooks";
import { usePlugins } from "@/core/plugins/hooks";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import { getBackendBaseURL } from "@/core/config";

import { RegistryAssetCard } from "./registry-asset-card";

function registryAssetUrl(value?: string | null): string | null {
  if (!value) return null;
  // Registry metadata is untrusted.  Only consume asset paths served by our
  // own backend; remote URLs must not be fetched from a marketplace response.
  if (/^https?:\/\//i.test(value)) return null;
  return `${getBackendBaseURL()}${value.startsWith("/") ? value : `/${value}`}`;
}

// 插件商城:从公网 registry 浏览并安装 prompt-only 能力。插件 body 会作为
// 本地提示技能保存；不会下载、导入或执行远程代码。真正的代码插件仍走本地
// 插件目录和显式权限审核。卡片排版与角色/技能商城保持统一。
export function RegistryPluginsPanel() {
  const { t } = useI18n();
  const [plugins, setPlugins] = useState<RegistryPlugin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [installing, setInstalling] = useState<Record<string, boolean>>({});
  const [installed, setInstalled] = useState<Record<string, boolean>>({});
  const { skills: localSkills } = useSkills();
  const { plugins: localPlugins } = usePlugins();
  const localPluginNames = new Set(
    localSkills
      .filter((skill) => skill.name.startsWith("plugin-"))
      .map((skill) => skill.name.toLowerCase()),
  );
  const localCodePluginIds = new Set(
    localPlugins.map((plugin) => plugin.id.toLowerCase()),
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listRegistryPlugins({ limit: 300 });
      setPlugins(res.plugins);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const onInstall = async (plugin: RegistryPlugin) => {
    const slug = registrySlug(plugin.id);
    setInstalling((m) => ({ ...m, [slug]: true }));
    setError(null);
    try {
      await installRegistryPlugin(slug);
      setInstalled((m) => ({ ...m, [slug]: true }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling((m) => ({ ...m, [slug]: false }));
    }
  };

  useEffect(() => {
    void load();
  }, [load]);

  const q = query.trim().toLowerCase();
  const filtered = q
    ? plugins.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.description.toLowerCase().includes(q) ||
          p.id.toLowerCase().includes(q),
      )
    : plugins;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <span className="text-sm font-medium">{t.store.pluginsPanelTitle}</span>
        <div className="flex shrink-0 items-center gap-1.5">
          <span className="text-xs text-muted-foreground">
            {filtered.length}/{plugins.length}
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t.store.searchPluginsPlaceholder}
            aria-label={t.store.searchPluginsPlaceholder}
            className="h-8 w-44 rounded-md border border-border-default bg-background px-2 text-sm outline-none focus:border-primary/50"
          />
          <Button
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={() => void load()}
          >
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      <div className="flex items-start gap-2 rounded-md border border-warning/25 bg-warning/8 px-3 py-2 text-xs text-warning">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        <span>{t.store.pluginsSafetyNotice}</span>
      </div>

      {error ? (
        <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="flex min-h-[200px] items-center justify-center text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {filtered.map((plugin) =>
            (() => {
              const slug = registrySlug(plugin.id);
              const name = `plugin-${slug}`;
              const isBundle = Boolean(plugin.bundle?.ref);
              const done = isBundle
                ? installed[slug] || localCodePluginIds.has(slug.toLowerCase())
                : installed[slug] ||
                  localPluginNames.has(name.toLowerCase()) ||
                  localCodePluginIds.has(slug.toLowerCase());
              const busy = installing[slug];
              return (
                <RegistryAssetCard
                  key={plugin.id}
                  name={plugin.name}
                  description={plugin.description}
                  category={null}
                  categoryLabel={plugin.category ?? undefined}
                  typeLabel={
                    isBundle
                      ? t.store.typeLabelPluginBundle
                      : t.store.typeLabelPromptCapability
                  }
                  iconUrl={registryAssetUrl(plugin.logo_url || plugin.icon_url)}
                  iconText={plugin.icon || "🔌"}
                  actionSlot={
                    <Button
                      size="sm"
                      variant={done ? "outline" : "default"}
                      className="h-7 rounded-sm px-3 text-xs"
                      disabled={busy || done}
                      onClick={() => void onInstall(plugin)}
                    >
                      {busy ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      ) : done ? (
                        <Check className="mr-1 h-3 w-3" />
                      ) : (
                        <Download className="mr-1 h-3 w-3" />
                      )}
                      {busy
                        ? t.store.installing
                        : done
                          ? t.store.installed
                          : t.store.install}
                    </Button>
                  }
                />
              );
            })(),
          )}
        </div>
      )}
    </div>
  );
}
