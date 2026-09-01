import { useEffect, useMemo, useState } from "react";
import { CloudDownloadIcon, CloudIcon, Loader2Icon } from "lucide-react";
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
  installRegistryRole,
  listRegistryRoles,
  type RegistryRole,
} from "@/core/registry/api";
import type { AgentWorldAgent } from "@/core/agents/types";
import { AgentCard } from "./agent-card";
import { worldAgentToAgent } from "./agent-world-data";

interface CloudRoleMarketPanelProps {
  searchQuery?: string;
  installedAgents?: AgentWorldAgent[];
  onInstallChange?: () => void;
}

function roleToAgent(role: RegistryRole): AgentWorldAgent {
  return {
    id: role.id,
    name: role.id,
    display_name: role.name,
    description: role.description,
    author: "Registry",
    category: (role.category as AgentWorldAgent["category"]) || "assistant",
    tags: role.tags ?? [],
    icon: role.icon ?? "☁️",
    avatar_url: role.icon_url ?? role.logo_url ?? undefined,
    version: role.version || "1.0.0",
    downloads: 0,
    rating: 0,
    rating_count: 0,
    is_featured: false,
    is_official: false,
    is_installed: false,
    created_at: "0",
  };
}

function roleMatchesAgent(role: RegistryRole, agent: AgentWorldAgent): boolean {
  const roleId = role.id.split("/").pop() ?? role.id;
  const registryId = `registry_${roleId
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")}`;
  return (
    agent.id === role.id ||
    agent.id === roleId ||
    agent.name === roleId ||
    agent.id === registryId ||
    agent.name === registryId
  );
}

export function CloudRoleMarketPanel({
  searchQuery = "",
  installedAgents = [],
  onInstallChange,
}: CloudRoleMarketPanelProps) {
  const [roles, setRoles] = useState<RegistryRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listRegistryRoles({
      search: searchQuery.trim() || undefined,
      limit: 300,
    })
      .then((result) => {
        if (!cancelled) setRoles(result.roles);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setRoles([]);
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [searchQuery]);

  const installedByRole = useMemo(() => {
    return roles.reduce((result, role) => {
      const agent = installedAgents.find((candidate) =>
        roleMatchesAgent(role, candidate),
      );
      if (agent) result.set(role.id, agent);
      return result;
    }, new Map<string, AgentWorldAgent>());
  }, [installedAgents, roles]);

  const directoryEntries = useMemo(() => {
    const entries: Array<{
      key: string;
      role: RegistryRole | null;
      agent: AgentWorldAgent;
      installed: boolean;
    }> = roles.map((role) => {
      const installedAgent = installedByRole.get(role.id);
      return {
        key: role.id,
        role,
        agent: installedAgent ?? roleToAgent(role),
        installed: Boolean(installedAgent),
      };
    });

    const normalizedQuery = searchQuery.trim().toLowerCase();
    for (const agent of installedAgents) {
      if (roles.some((role) => roleMatchesAgent(role, agent))) continue;
      if (
        normalizedQuery &&
        ![
          agent.display_name,
          agent.name,
          agent.description,
          agent.author,
          ...agent.tags,
        ]
          .join(" ")
          .toLowerCase()
          .includes(normalizedQuery)
      ) {
        continue;
      }
      entries.push({
        key: `installed:${agent.id}`,
        role: null,
        agent,
        installed: true,
      });
    }
    return entries;
  }, [installedAgents, installedByRole, roles, searchQuery]);

  async function handleInstall(role: RegistryRole) {
    setInstalling(role.id);
    try {
      await installRegistryRole(role.id);
      toast.success(`分身「${role.name}」已添加到本地`);
      onInstallChange?.();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setInstalling(null);
    }
  }

  if (loading && directoryEntries.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" />
        正在加载云端角色…
      </div>
    );
  }

  if (error && directoryEntries.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border px-5 py-10 text-center text-sm text-muted-foreground">
        云端角色暂时不可用，请稍后重试。
      </div>
    );
  }

  if (directoryEntries.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border px-5 py-10 text-center text-sm text-muted-foreground">
        没有匹配的云端角色。
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <CloudIcon className="size-4 text-primary" />
        云端角色 · 添加后只能按需召唤进当前对话
        {loading ? (
          <span className="inline-flex items-center gap-1">
            <Loader2Icon className="size-3 animate-spin" />
            正在同步云端目录…
          </span>
        ) : null}
        {error ? <span>云端目录同步失败，当前显示本地已添加角色</span> : null}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {directoryEntries.map(({ key, role, agent, installed }) => {
          if (installed) {
            return (
              <AgentCard
                key={key}
                agent={worldAgentToAgent(agent)}
                isDefault
                isPrimaryIdentity={false}
              />
            );
          }

          if (!role) return null;
          const isInstalling = installing === role.id;
          return (
            <Card
              key={key}
              className="flex min-h-36 flex-col border-border-default"
            >
              <CardHeader className="gap-2 px-4 pb-3 pt-4">
                <div className="flex items-start gap-3">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-border-default bg-primary/10 text-lg">
                    {agent.icon}
                  </div>
                  <div className="min-w-0 flex-1">
                    <CardTitle className="truncate text-sm">
                      {role.name}
                    </CardTitle>
                    <CardDescription className="mt-1 line-clamp-2 text-xs leading-5">
                      {role.description}
                    </CardDescription>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="secondary" className="gap-1 text-[11px]">
                    <CloudIcon className="size-3" />
                    云端角色
                  </Badge>
                  {(role.tags ?? []).slice(0, 2).map((tag) => (
                    <Badge key={tag} variant="outline" className="text-[11px]">
                      {tag}
                    </Badge>
                  ))}
                </div>
              </CardHeader>
              <CardFooter className="mt-auto gap-2 border-t border-border-subtle bg-muted/10 px-3 py-2.5">
                <Button
                  size="sm"
                  className="flex-1 text-xs"
                  disabled={isInstalling}
                  onClick={() => void handleInstall(role)}
                >
                  {isInstalling ? (
                    <Loader2Icon className="mr-1 size-3.5 animate-spin" />
                  ) : (
                    <CloudDownloadIcon className="mr-1 size-3.5" />
                  )}
                  {isInstalling ? "添加中…" : "按需添加"}
                </Button>
              </CardFooter>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
