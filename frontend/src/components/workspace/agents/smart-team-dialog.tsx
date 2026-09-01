import { useEffect, useMemo, useState } from "react";
import { Loader2Icon, PuzzleIcon, SparklesIcon, UsersIcon } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  installAgent,
  installCloudExpert,
  listCloudStoreExperts,
  type CloudExpertAgent,
} from "@/core/agents/agent-world-api";
import type { AgentWorldAgent } from "@/core/agents/types";
import { DEFAULT_PRIMARY_AGENT_ID, isPrimaryPersonaAgentId } from "@/core/agents/persona-policy";
import {
  taskCollaboratorRouteForLeader,
  writeTaskCollaboratorPreset,
} from "@/core/collaboration/task-collaborator-preset";

import { buildSmartTeamPlan } from "./smart-team-matcher";

export function SmartTeamDialog({
  open,
  onOpenChange,
  agents,
  onInstallChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agents: AgentWorldAgent[];
  onInstallChange: () => void;
}) {
  const navigate = useNavigate();
  const [task, setTask] = useState("");
  const [launching, setLaunching] = useState(false);
  const [cloudExperts, setCloudExperts] = useState<CloudExpertAgent[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  useEffect(() => {
    if (!open || cloudExperts.length > 0 || catalogLoading) return;
    setCatalogLoading(true);
    void listCloudStoreExperts({ limit: 500 })
      .then((result) => setCloudExperts(result.agents))
      .catch(() => undefined)
      .finally(() => setCatalogLoading(false));
  }, [catalogLoading, cloudExperts.length, open]);
  const candidates = useMemo<AgentWorldAgent[]>(() => {
    const localKeys = new Set(agents.flatMap((agent) => [agent.id, agent.name]));
    const remote = cloudExperts
      .filter((expert) => !localKeys.has(expert.id) && !localKeys.has(expert.name))
      .map<AgentWorldAgent>((expert) => ({
        id: expert.id,
        name: expert.name || expert.id,
        display_name: expert.display_name || expert.name || expert.id,
        description: [expert.profession, expert.description].filter(Boolean).join(" · "),
        author: expert.author,
        category: "specialist",
        tags: [...(expert.tags ?? []), ...(expert.is_team ? ["专家团", "team"] : ["专家"])],
        icon: expert.icon || (expert.is_team ? "👥" : "🧑‍💼"),
        avatar_url: expert.avatar_url,
        version: "cloud",
        downloads: 0,
        rating: 0,
        rating_count: 0,
        is_featured: false,
        is_official: false,
        is_installed: Boolean(expert.is_installed),
        source_kind: "cloud",
        created_at: expert.created_at || "",
      }));
    return [...agents, ...remote];
  }, [agents, cloudExperts]);
  const plan = useMemo(() => buildSmartTeamPlan(task, candidates), [candidates, task]);
  const missing = plan.members.filter((agent) => !agent.is_installed);

  const launch = async () => {
    if (!task.trim() || plan.members.length === 0 || launching) return;
    setLaunching(true);
    try {
      const installedIds = new Map<string, string>();
      for (const agent of missing) {
        if (agent.source_kind === "cloud") {
          const result = await installCloudExpert(agent.id);
          installedIds.set(agent.id, result.agent_id || agent.name);
        } else {
          const result = await installAgent(agent.id);
          installedIds.set(agent.id, result.agent_id || agent.name);
        }
      }
      if (missing.length > 0) onInstallChange();
      const visibleLead = plan.members[0]!;
      const visibleLeadId = installedIds.get(visibleLead.id) || visibleLead.name;
      const runtimeLead = isPrimaryPersonaAgentId(visibleLeadId)
        ? visibleLeadId
        : DEFAULT_PRIMARY_AGENT_ID;
      const collaborators = plan.members
        .map((agent) => installedIds.get(agent.id) || agent.name)
        .filter((id) => id !== runtimeLead);
      writeTaskCollaboratorPreset({
        leaderId: runtimeLead,
        collaboratorIds: collaborators,
        mode: plan.mode,
        label: task.trim().slice(0, 48),
        openPicker: true,
      });
      onOpenChange(false);
      navigate(taskCollaboratorRouteForLeader(runtimeLead));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "智能组队失败");
    } finally {
      setLaunching(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <SparklesIcon className="size-5 text-violet-500" />
            智能组队
          </DialogTitle>
          <DialogDescription>
            从全部角色、云端专家和专家团中匹配成员，并推荐任务所需插件。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="smart-team-task">要完成什么任务？</Label>
            <Textarea
              id="smart-team-task"
              value={task}
              onChange={(event) => setTask(event.target.value)}
              placeholder="例如：调研东南亚宠物用品市场，完成 TikTok Shop 选品、利润模型和测品计划"
              className="min-h-24 resize-none"
            />
          </div>
          {task.trim() ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <section className="rounded-xl border border-border bg-muted/15 p-3">
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  <UsersIcon className="size-4" /> 推荐成员
                </h3>
                <div className="mt-3 space-y-2">
                  {plan.members.map((agent, index) => (
                    <div key={agent.id} className="flex items-center gap-2 rounded-lg bg-background px-2.5 py-2">
                      <span className="flex size-8 items-center justify-center overflow-hidden rounded-full bg-muted text-sm">
                        {agent.avatar_url ? <img src={agent.avatar_url} alt="" className="size-full object-cover" /> : agent.icon || "·"}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{agent.display_name}</span>
                        <span className="block truncate text-[11px] text-muted-foreground">
                          {index === 0 ? "任务协调候选" : agent.tags.includes("专家团") ? "专家团" : agent.category}
                        </span>
                      </span>
                      {!agent.is_installed ? <span className="text-[10px] text-amber-600">需安装</span> : null}
                    </div>
                  ))}
                  {catalogLoading ? <div className="flex items-center gap-2 px-2 py-1 text-[11px] text-muted-foreground"><Loader2Icon className="size-3 animate-spin" />正在扫描云端专家目录…</div> : null}
                </div>
              </section>
              <section className="rounded-xl border border-border bg-muted/15 p-3">
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  <PuzzleIcon className="size-4" /> 推荐插件
                </h3>
                <div className="mt-3 space-y-2">
                  {plan.plugins.length > 0 ? plan.plugins.map((plugin) => (
                    <div key={plugin.id} className="rounded-lg bg-background px-2.5 py-2">
                      <div className="text-sm font-medium">{plugin.label}</div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">{plugin.reason}</div>
                    </div>
                  )) : <p className="text-xs text-muted-foreground">描述再具体一些后，会给出插件建议。</p>}
                </div>
              </section>
            </div>
          ) : null}
          <p className="text-[11px] leading-5 text-muted-foreground">
            专家优先按能力匹配，不强制主角入队。必要时系统协调层仅承载任务线程，不占专家席位。插件只推荐，不会静默安装。
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button disabled={!task.trim() || plan.members.length === 0 || launching} onClick={() => void launch()}>
            {launching ? <Loader2Icon className="mr-2 size-4 animate-spin" /> : <SparklesIcon className="mr-2 size-4" />}
            {missing.length > 0 ? `安装 ${missing.length} 位专家并组队` : "创建专家团队"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
