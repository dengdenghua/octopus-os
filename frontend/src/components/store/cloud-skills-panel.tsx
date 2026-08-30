import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckIcon, CloudIcon, Loader2Icon, PackageIcon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchCloudInstalled,
  fetchCloudSkills,
  fetchUnifiedAssets,
  streamInstallCloudSkill,
  type CloudSkillInstallProgress,
  type CloudSkillItem,
  type UnifiedAsset,
} from "@/core/agents/agent-world-api";

function normalizeSkillName(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-");
}

function localSkillAliases(asset: UnifiedAsset) {
  return [asset.id, asset.name, asset.name_zh]
    .filter((value): value is string => Boolean(value?.trim()))
    .map(normalizeSkillName);
}

interface SkillEntry {
  key: string;
  name: string;
  description: string;
  version?: string;
  author?: string;
  source?: string;
  cloud: CloudSkillItem | null;
  local: UnifiedAsset | null;
}

export function CloudSkillsPanel({
  searchQuery = "",
}: {
  searchQuery?: string;
}) {
  const [cloudSkills, setCloudSkills] = useState<CloudSkillItem[]>([]);
  const [localSkills, setLocalSkills] = useState<UnifiedAsset[]>([]);
  const [installedNames, setInstalledNames] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<
    Record<string, CloudSkillInstallProgress>
  >({});
  const [loading, setLoading] = useState(true);
  const [cloudError, setCloudError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setCloudError(null);
    const [cloudResult, installedResult, localResult] =
      await Promise.allSettled([
        fetchCloudSkills({ limit: 500 }),
        fetchCloudInstalled(),
        fetchUnifiedAssets({ kind: "skill", limit: 500, offset: 0 }),
      ]);

    if (cloudResult.status === "fulfilled") {
      setCloudSkills(cloudResult.value.items);
    } else {
      setCloudSkills([]);
      setCloudError(
        cloudResult.reason instanceof Error
          ? cloudResult.reason.message
          : String(cloudResult.reason),
      );
    }
    setInstalledNames(
      new Set(
        installedResult.status === "fulfilled"
          ? installedResult.value.skills.map(normalizeSkillName)
          : [],
      ),
    );
    setLocalSkills(
      localResult.status === "fulfilled" ? localResult.value.items : [],
    );
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const entries = useMemo(() => {
    const localByAlias = new Map<string, UnifiedAsset>();
    for (const skill of localSkills) {
      for (const alias of localSkillAliases(skill))
        localByAlias.set(alias, skill);
    }

    const usedLocal = new Set<UnifiedAsset>();
    const merged: SkillEntry[] = cloudSkills.map((skill) => {
      const local = localByAlias.get(normalizeSkillName(skill.name)) ?? null;
      if (local) usedLocal.add(local);
      return {
        key: `cloud:${skill.name}`,
        name: skill.name,
        description: skill.description,
        version: skill.version,
        author: skill.author,
        source: skill.source,
        cloud: skill,
        local,
      };
    });
    for (const skill of localSkills) {
      if (usedLocal.has(skill)) continue;
      merged.push({
        key: `local:${skill.source}:${skill.id}`,
        name: skill.name_zh || skill.name || skill.id,
        description: skill.description || "本地技能",
        version: skill.version,
        author: skill.author,
        source: skill.source,
        cloud: null,
        local: skill,
      });
    }
    const query = searchQuery.trim().toLowerCase();
    return query
      ? merged.filter((entry) =>
          [entry.name, entry.description, entry.author, entry.source]
            .filter(Boolean)
            .join(" ")
            .toLowerCase()
            .includes(query),
        )
      : merged;
  }, [cloudSkills, localSkills, searchQuery]);

  const isInstalled = (entry: SkillEntry) =>
    Boolean(entry.local) || installedNames.has(normalizeSkillName(entry.name));

  const install = async (skill: CloudSkillItem) => {
    const key = normalizeSkillName(skill.name);
    setProgress((current) => ({
      ...current,
      [key]: { phase: "resolving", progress: 2, message: "正在启动安装" },
    }));
    try {
      await streamInstallCloudSkill(skill.name, (event) => {
        setProgress((current) => ({ ...current, [key]: event }));
      });
      setInstalledNames((current) => new Set(current).add(key));
      toast.success(`技能「${skill.name}」已安装`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setProgress((current) => ({
        ...current,
        [key]: { phase: "failed", progress: 100, message },
      }));
      toast.error(message);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <CloudIcon className="size-4 text-primary" />
        云端 Skills · 本地同名技能自动标记为已安装
        <Badge variant="outline" className="font-normal">
          {entries.filter(isInstalled).length}/{entries.length} 已安装
        </Badge>
      </div>

      {cloudError ? (
        <div className="rounded-lg border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-muted-foreground">
          云端目录同步失败，当前显示本地 Skills：{cloudError}
        </div>
      ) : null}

      {loading && entries.length === 0 ? (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-28 rounded-xl" />
          ))}
        </div>
      ) : entries.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-5 py-10 text-center text-sm text-muted-foreground">
          没有匹配的 Skill。
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {entries.map((entry) => {
            const installed = isInstalled(entry);
            const state = progress[normalizeSkillName(entry.name)];
            const installing =
              state && !["completed", "failed"].includes(state.phase);
            return (
              <Card key={entry.key} className="gap-2 py-3">
                <CardHeader className="flex-row items-center gap-2.5 px-3 pt-0">
                  <div className="grid size-9 shrink-0 place-items-center rounded-lg border border-border-default bg-muted">
                    <PackageIcon className="size-4 text-primary" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <CardTitle className="truncate text-sm">
                      {entry.name}
                    </CardTitle>
                    <CardDescription className="line-clamp-1 text-xs">
                      {entry.description}
                    </CardDescription>
                  </div>
                  {installed ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled
                      className="gap-1"
                    >
                      <CheckIcon className="size-3.5 text-success" /> 已安装
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={Boolean(installing)}
                      onClick={() => entry.cloud && void install(entry.cloud)}
                    >
                      {installing ? (
                        <Loader2Icon className="mr-1 size-3.5 animate-spin" />
                      ) : (
                        <CloudIcon className="mr-1 size-3.5" />
                      )}
                      {installing ? `${state.progress}%` : "安装"}
                    </Button>
                  )}
                </CardHeader>
                {state && !installed ? (
                  <div className="px-3">
                    <div className="h-1 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary transition-[width]"
                        style={{ width: `${state.progress}%` }}
                      />
                    </div>
                    <p className="mt-1 truncate text-[11px] text-muted-foreground">
                      {state.message}
                    </p>
                  </div>
                ) : null}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
