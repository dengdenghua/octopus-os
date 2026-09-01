import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Download, Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  installRegistrySkill,
  listRegistrySkills,
  registrySlug,
  type RegistrySkill,
} from "@/core/registry/api";
import { useSkills } from "@/core/skills/hooks";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import { getBackendBaseURL } from "@/core/config";

import { RegistryAssetCard } from "./registry-asset-card";

function registryAssetUrl(value?: string | null): string | null {
  if (!value || /^https?:\/\//i.test(value)) return null;
  return `${getBackendBaseURL()}${value.startsWith("/") ? value : `/${value}`}`;
}

// 技能商城:从公网 registry 浏览 / 安装 prompt-skill(母体接 registry)。卡片排版
// 对齐角色/插件商城面板(RegistryAssetCard),保持三个商城面板观感统一。
export function RegistrySkillsPanel() {
  const { t } = useI18n();
  const [skills, setSkills] = useState<RegistrySkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [installing, setInstalling] = useState<Record<string, boolean>>({});
  const [installed, setInstalled] = useState<Record<string, boolean>>({});
  // 本地已注册的技能名(all_skills/skills-public 里已有的)→ 去重:云端商城里同名的
  // 直接显示"已安装",不让用户误以为要重新装一份(名字即 slug,两边约定一致)。
  const { skills: localSkills } = useSkills();
  const localSkillSlugs = useMemo(
    () => new Set(localSkills.map((s) => s.name.toLowerCase())),
    [localSkills],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listRegistrySkills({ limit: 300 });
      setSkills(res.skills);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const q = query.trim().toLowerCase();
  const filtered = q
    ? skills.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.description.toLowerCase().includes(q) ||
          s.id.toLowerCase().includes(q),
      )
    : skills;

  const onInstall = async (skill: RegistrySkill) => {
    const slug = registrySlug(skill.id);
    setInstalling((m) => ({ ...m, [slug]: true }));
    setError(null);
    try {
      await installRegistrySkill(slug);
      setInstalled((m) => ({ ...m, [slug]: true }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling((m) => ({ ...m, [slug]: false }));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <span className="text-sm font-medium">{t.store.skillsPanelTitle}</span>
        <div className="flex shrink-0 items-center gap-1.5">
          <span className="text-xs text-muted-foreground">
            {filtered.length}/{skills.length}
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t.store.searchSkillsPlaceholder}
            aria-label={t.store.searchSkillsPlaceholder}
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
          {filtered.map((skill) => {
            const slug = registrySlug(skill.id);
            const alreadyLocal = localSkillSlugs.has(slug.toLowerCase());
            const done = installed[slug] || alreadyLocal;
            const busy = installing[slug];
            return (
              <RegistryAssetCard
                key={skill.id}
                name={skill.name}
                description={skill.description}
                category={null}
                categoryLabel={skill.category ?? undefined}
                typeLabel={t.store.typeLabelStore}
                iconUrl={registryAssetUrl(skill.logo_url || skill.icon_url)}
                iconText={skill.icon || "🧩"}
                actionSlot={
                  <Button
                    size="sm"
                    variant={done ? "outline" : "default"}
                    className="h-7 rounded-sm px-3 text-xs"
                    disabled={busy || done}
                    onClick={() => void onInstall(skill)}
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
          })}
        </div>
      )}
    </div>
  );
}
