import { useCallback, useEffect, useMemo, useState } from "react";
import { Boxes, Check, Cloud, Loader2, Puzzle, Search } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  fetchCloudInstalled,
  fetchCloudPlugins,
  fetchCloudSkills,
  installCloudPlugin,
  installCloudSkill,
  type CloudPluginItem,
  type CloudSkillItem,
} from "@/core/agents/agent-world-api";
import { cn } from "@/lib/utils";

// 云商城 · 我们发布的资产(发布到 GitHub Pages 的 plugin-store.json /
// skill-registry.json)。本地插件/技能通过 build-*/publish-cloud 脚本上云后,
// 这里就能浏览并一键安装(后端下载内容包 → 解包落地到 ~/.echo)。
// 数据来自后端 /api/agent-market/cloud/plugins + /api/agent-market/cloud/skills。

const KIND_META: Record<string, { label: string; badge: string }> = {
  plugin: {
    label: "插件",
    badge: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
  },
  connector: { label: "连接器", badge: "bg-primary/10 text-primary" },
};

function InstallButton({
  installed,
  installing,
  onInstall,
}: {
  installed: boolean;
  installing: boolean;
  onInstall: () => void;
}) {
  if (installed) {
    return (
      <Button
        size="sm"
        variant="outline"
        className="pointer-events-none gap-1 text-muted-foreground"
        disabled
      >
        <Check className="size-3.5 text-chart-2" /> 已安装
      </Button>
    );
  }
  return (
    <Button
      size="sm"
      variant="ghost"
      className="gap-1"
      disabled={installing}
      onClick={onInstall}
    >
      {installing ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <Cloud className="size-3.5" />
      )}
      {installing ? "安装中…" : "安装"}
    </Button>
  );
}

function PluginCard({
  item,
  installed,
  installing,
  onInstall,
}: {
  item: CloudPluginItem;
  installed: boolean;
  installing: boolean;
  onInstall: () => void;
}) {
  const kind = KIND_META[item.kind] ?? {
    label: item.kind ?? "插件",
    badge: "bg-muted text-muted-foreground",
  };
  return (
    <Card className="gap-1 py-3 transition-colors hover:border-primary/40">
      <CardHeader className="flex-row items-center gap-2 px-3 pt-0">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border-default bg-muted text-base">
          {item.kind === "connector" ? (
            <Puzzle className="size-4 text-primary" />
          ) : (
            <Boxes className="size-4 text-indigo-500" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <CardTitle className="truncate text-sm">
            {item.name_zh || item.name}
          </CardTitle>
          <CardDescription className="truncate text-xs">
            {item.id}
          </CardDescription>
        </div>
        <InstallButton
          installed={installed}
          installing={installing}
          onInstall={onInstall}
        />
      </CardHeader>
      <div className="flex flex-wrap items-center gap-1 px-3">
        <Badge className={cn("border-transparent text-[11px]", kind.badge)}>
          {kind.label}
        </Badge>
        <Badge
          variant="outline"
          className="text-[11px] font-normal text-muted-foreground"
        >
          {item.source === "codex"
            ? "EchoOS 插件"
            : item.source === "workbuddy"
              ? "WorkBuddy"
              : item.source}
        </Badge>
        {typeof item.skills_count === "number" && item.skills_count > 0 && (
          <Badge
            variant="outline"
            className="text-[11px] font-normal text-muted-foreground"
          >
            技能 ×{item.skills_count}
          </Badge>
        )}
      </div>
      <p className="line-clamp-2 px-3 text-xs leading-5 text-muted-foreground">
        {item.description}
      </p>
    </Card>
  );
}

function SkillCard({
  item,
  installed,
  installing,
  onInstall,
}: {
  item: CloudSkillItem;
  installed: boolean;
  installing: boolean;
  onInstall: () => void;
}) {
  const isEcho = item.source?.startsWith("echo");
  return (
    <Card className="gap-1 py-3 transition-colors hover:border-primary/40">
      <CardHeader className="flex-row items-center gap-2 px-3 pt-0">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border-default bg-muted text-base">
          <Cloud className="size-4 text-chart-2" />
        </div>
        <div className="min-w-0 flex-1">
          <CardTitle className="truncate text-sm">{item.name}</CardTitle>
          <CardDescription className="truncate text-xs">
            {item.author}
          </CardDescription>
        </div>
        <InstallButton
          installed={installed}
          installing={installing}
          onInstall={onInstall}
        />
      </CardHeader>
      <div className="flex flex-wrap items-center gap-1 px-3">
        <Badge
          className={cn(
            "border-transparent text-[11px]",
            isEcho
              ? "bg-chart-2/10 text-chart-2"
              : "bg-chart-3/10 text-chart-3",
          )}
        >
          {isEcho ? "EchoOS 自研" : "WorkBuddy"}
        </Badge>
        <Badge
          variant="outline"
          className="text-[11px] font-normal text-muted-foreground"
        >
          v{item.version || "0.1.0"}
        </Badge>
      </div>
      <p className="line-clamp-2 px-3 text-xs leading-5 text-muted-foreground">
        {item.description}
      </p>
    </Card>
  );
}

export function CloudCatalogPanel() {
  const [plugins, setPlugins] = useState<CloudPluginItem[]>([]);
  const [skills, setSkills] = useState<CloudSkillItem[]>([]);
  const [installedSkills, setInstalledSkills] = useState<Set<string>>(
    new Set(),
  );
  const [installedPlugins, setInstalledPlugins] = useState<Set<string>>(
    new Set(),
  );
  const [installing, setInstalling] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, s, inst] = await Promise.all([
        fetchCloudPlugins({ limit: 500 }),
        fetchCloudSkills({ limit: 500 }),
        fetchCloudInstalled(),
      ]);
      setPlugins(p.items);
      setSkills(s.items);
      setInstalledSkills(new Set(inst.skills));
      setInstalledPlugins(new Set(inst.plugins));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const installSkill = useCallback(async (name: string) => {
    setInstalling((m) => ({ ...m, [`skill:${name}`]: true }));
    try {
      const res = await installCloudSkill(name);
      if (res.already_exists) {
        toast.info(`技能「${name}」本地已存在`);
      } else {
        toast.success(`技能「${name}」已安装`);
      }
      setInstalledSkills((prev) => new Set(prev).add(name));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling((m) => {
        const next = { ...m };
        delete next[`skill:${name}`];
        return next;
      });
    }
  }, []);

  const installPlugin = useCallback(async (id: string, member: string) => {
    setInstalling((m) => ({ ...m, [`plugin:${id}`]: true }));
    try {
      const res = await installCloudPlugin(id);
      const copied = res.copied_skills?.length ?? 0;
      toast.success(
        copied > 0
          ? `插件「${id}」已安装,捆绑技能 ×${copied}`
          : `插件「${id}」已安装`,
      );
      setInstalledPlugins((prev) => new Set(prev).add(member));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling((m) => {
        const next = { ...m };
        delete next[`plugin:${id}`];
        return next;
      });
    }
  }, []);

  const query = q.trim().toLowerCase();
  const filteredPlugins = useMemo(
    () =>
      plugins.filter((p) => {
        if (!query) return true;
        return [p.name, p.name_zh, p.id, p.description]
          .join(" ")
          .toLowerCase()
          .includes(query);
      }),
    [plugins, query],
  );
  const filteredSkills = useMemo(
    () =>
      skills.filter((s) => {
        if (!query) return true;
        return [s.name, s.description].join(" ").toLowerCase().includes(query);
      }),
    [skills, query],
  );

  const installedPluginCount = plugins.filter((p) =>
    installedPlugins.has(p.plugin),
  ).length;
  const installedSkillCount = skills.filter((s) =>
    installedSkills.has(s.name),
  ).length;

  return (
    <div className="space-y-3 rounded-md border border-border-default p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Cloud className="size-3.5 text-primary" />
          <span className="text-sm font-medium">云商城 · 我们发布的资产</span>
          <Badge
            variant="outline"
            className="text-[11px] font-normal text-muted-foreground"
          >
            {plugins.length} 插件 / {skills.length} 技能 · 已装{" "}
            {installedPluginCount}/{installedSkillCount}
          </Badge>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索云商城"
              aria-label="搜索云商城"
              className="h-8 w-40 rounded-md border border-border-default bg-background pl-7 pr-2 text-sm outline-none focus:border-primary/50"
            />
          </div>
          <Button
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={() => void load()}
            title="刷新云商城"
          >
            <Loader2 className={cn("size-3.5", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      {error ? (
        <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Boxes className="size-3.5" /> 插件 / 连接器
            <span className="ml-1 text-muted-foreground/70">
              {filteredPlugins.length}/{plugins.length}
            </span>
          </div>
          <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
            {filteredPlugins.map((p) => (
              <PluginCard
                key={p.id}
                item={p}
                installed={installedPlugins.has(p.plugin)}
                installing={!!installing[`plugin:${p.id}`]}
                onInstall={() => void installPlugin(p.id, p.plugin)}
              />
            ))}
            {filteredPlugins.length === 0 && !loading && (
              <div className="py-6 text-center text-xs text-muted-foreground">
                没有匹配的云插件
              </div>
            )}
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Cloud className="size-3.5" /> 技能
            <span className="ml-1 text-muted-foreground/70">
              {filteredSkills.length}/{skills.length}
            </span>
          </div>
          <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
            {filteredSkills.map((s) => (
              <SkillCard
                key={s.name}
                item={s}
                installed={installedSkills.has(s.name)}
                installing={!!installing[`skill:${s.name}`]}
                onInstall={() => void installSkill(s.name)}
              />
            ))}
            {filteredSkills.length === 0 && !loading && (
              <div className="py-6 text-center text-xs text-muted-foreground">
                没有匹配的云技能
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
