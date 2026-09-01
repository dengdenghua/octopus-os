import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BotIcon, CheckIcon, CrownIcon, UsersRoundIcon } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { AgentAvatar } from "@/components/workspace/sidebar-footer";
import { useAgents } from "@/core/agents";
import { useActiveAgentId } from "@/core/agents/active";
import {
  isPrimaryPersonaAgentId,
  primaryPersonaAgentIdOrDefault,
} from "@/core/agents/persona-policy";
import {
  DEFAULT_PROJECT_AGENT_ID,
  type ProjectInitialAgent,
  useCreateProject,
} from "@/core/projects/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { isIMEComposing } from "@/lib/ime";
import { cn } from "@/lib/utils";

interface CreateProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateProjectDialog({
  open,
  onOpenChange,
}: CreateProjectDialogProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const CATEGORY_PRESETS = useMemo(
    () => [
      {
        label: t.createProjectDialog.categoryInvest,
        icon: "📈",
        category: "investment",
      },
      {
        label: t.createProjectDialog.categoryHomework,
        icon: "📝",
        category: "homework",
      },
      {
        label: t.createProjectDialog.categoryWriting,
        icon: "✍️",
        category: "writing",
      },
      {
        label: t.createProjectDialog.categoryTravel,
        icon: "✈️",
        category: "travel",
      },
    ],
    [t],
  );
  const [name, setName] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedIcon, setSelectedIcon] = useState<string>("📁");
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[] | null>(
    null,
  );
  const [invitePeopleAfterCreate, setInvitePeopleAfterCreate] = useState(false);
  const { agents, isLoading: agentsLoading } = useAgents();
  const activeAgentId = useActiveAgentId();
  const { mutate: createProject, isPending } = useCreateProject();
  const requestedLeaderId = primaryPersonaAgentIdOrDefault(activeAgentId);
  const defaultAgentId =
    agents.find((agent) => agent.name === requestedLeaderId)?.name ??
    agents.find((agent) => agent.name === DEFAULT_PROJECT_AGENT_ID)?.name ??
    agents.find((agent) => isPrimaryPersonaAgentId(agent.name))?.name ??
    DEFAULT_PROJECT_AGENT_ID;
  const effectiveSelectedAgentIds = useMemo(
    () => [
      defaultAgentId,
      ...(selectedAgentIds ?? []).filter((id) => id !== defaultAgentId),
    ],
    [defaultAgentId, selectedAgentIds],
  );
  const selectedAgentSet = useMemo(
    () => new Set(effectiveSelectedAgentIds),
    [effectiveSelectedAgentIds],
  );

  const resetForm = () => {
    setName("");
    setSelectedCategory(null);
    setSelectedIcon("📁");
    setSelectedAgentIds(null);
    setInvitePeopleAfterCreate(false);
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) resetForm();
    onOpenChange(nextOpen);
  };

  const handleCategorySelect = (preset: (typeof CATEGORY_PRESETS)[number]) => {
    if (selectedCategory === preset.category) {
      setSelectedCategory(null);
      setSelectedIcon("📁");
    } else {
      setSelectedCategory(preset.category);
      setSelectedIcon(preset.icon);
      if (!name) {
        setName(preset.label);
      }
    }
  };

  const handleSubmit = () => {
    if (!name.trim() || isPending) return;
    const initialAgents: ProjectInitialAgent[] = effectiveSelectedAgentIds.map(
      (id) => {
        const agent = agents.find((candidate) => candidate.name === id);
        return {
          id,
          displayName: agent?.display_name ?? agent?.name,
          description: agent?.description,
          avatarUrl: agent?.avatar_url,
          icon: agent?.icon,
        };
      },
    );
    createProject(
      {
        name: name.trim(),
        icon: selectedIcon,
        category: selectedCategory ?? undefined,
        initialAgents,
      },
      {
        onSuccess: ({ threadId }) => {
          resetForm();
          onOpenChange(false);
          navigate(`/workspace/realtime/${encodeURIComponent(threadId)}`, {
            state: {
              openProjectWorkbench: true,
              ...(invitePeopleAfterCreate
                ? { openHumanInviteAfterCreate: true }
                : {}),
            },
          });
        },
        onError: () => toast.error("项目工作群创建失败，请重试"),
      },
    );
  };

  const toggleAgent = (agentId: string) => {
    if (isPending || agentId === defaultAgentId) return;
    if (selectedAgentSet.has(agentId)) {
      setSelectedAgentIds(
        effectiveSelectedAgentIds.filter((id) => id !== agentId),
      );
      return;
    }
    setSelectedAgentIds([...effectiveSelectedAgentIds, agentId]);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] gap-0 overflow-hidden p-0 sm:max-w-[var(--dialog-lg)]">
        <DialogHeader className="px-6 pt-6 pb-4">
          <DialogTitle>{t.createProjectDialog.title}</DialogTitle>
          <DialogDescription>{t.createProjectDialog.hint}</DialogDescription>
        </DialogHeader>
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto border-y px-6 py-4">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t.createProjectDialog.placeholder}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !isIMEComposing(e)) {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />
          <div className="flex flex-col gap-2">
            <span className="text-muted-foreground text-sm">
              {t.createProjectDialog.quickCategory}
            </span>
            <div className="flex flex-wrap gap-2">
              {CATEGORY_PRESETS.map((preset) => (
                <Button
                  key={preset.category}
                  variant={
                    selectedCategory === preset.category ? "default" : "outline"
                  }
                  size="sm"
                  onClick={() => handleCategorySelect(preset)}
                >
                  <span className="mr-1">{preset.icon}</span>
                  {preset.label}
                </Button>
              ))}
            </div>
          </div>

          <section
            aria-labelledby="create-project-ai-members"
            className="overflow-hidden rounded-xl border border-border-default bg-muted/15"
          >
            <div className="flex items-start justify-between gap-3 border-b border-border-subtle px-3 py-2.5">
              <div className="flex min-w-0 items-start gap-2.5">
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                  <BotIcon className="size-4" />
                </span>
                <div className="min-w-0">
                  <h3
                    id="create-project-ai-members"
                    className="text-sm font-medium"
                  >
                    {t.createProjectDialog.aiMembersLabel}
                  </h3>
                  <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                    {t.createProjectDialog.aiMembersDescription}
                  </p>
                </div>
              </div>
              <span className="shrink-0 rounded-md bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
                {t.createProjectDialog.aiMembersSelected(
                  effectiveSelectedAgentIds.length,
                )}
              </span>
            </div>

            {agentsLoading ? (
              <div className="px-3 py-3 text-xs text-muted-foreground">
                {t.createProjectDialog.agentsLoading}
              </div>
            ) : agents.length > 0 ? (
              <div className="grid max-h-44 grid-cols-1 gap-1 overflow-y-auto p-2 sm:grid-cols-2">
                {agents.map((agent) => {
                  const selected = selectedAgentSet.has(agent.name);
                  const isLeader = agent.name === defaultAgentId;
                  const label = agent.display_name ?? agent.name;
                  return (
                    <button
                      key={agent.name}
                      type="button"
                      aria-label={label}
                      aria-pressed={selected}
                      disabled={isPending || isLeader}
                      onClick={() => toggleAgent(agent.name)}
                      className={cn(
                        "flex min-w-0 items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
                        selected
                          ? "border-primary/30 bg-primary/8"
                          : "border-transparent hover:border-border-default hover:bg-background/70",
                        isLeader && "cursor-default opacity-100",
                      )}
                    >
                      <AgentAvatar
                        agent={agent}
                        className="size-7 rounded-md"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium">
                          {label}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {agent.description || agent.name}
                        </span>
                      </span>
                      <span
                        className={cn(
                          "grid size-5 shrink-0 place-items-center rounded-full border",
                          selected
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-border-default bg-background",
                        )}
                      >
                        {selected && <CheckIcon className="size-3" />}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="px-3 py-3 text-xs text-muted-foreground">
                {t.createProjectDialog.agentsUnavailable}
              </div>
            )}
          </section>

          <div className="grid gap-2 sm:grid-cols-2">
            <div className="flex items-start gap-2.5 rounded-xl border border-border-default px-3 py-2.5">
              <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                <UsersRoundIcon className="size-4" />
              </span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                  <span>{t.createProjectDialog.humanMembersLabel}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-normal text-muted-foreground">
                    {t.createProjectDialog.humanMembersAfterCreate}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {t.createProjectDialog.humanMembersDescription}
                </p>
                <label className="mt-2 flex cursor-pointer items-center justify-between gap-2 rounded-lg bg-muted/45 px-2 py-1.5 text-xs font-medium">
                  <span>{t.createProjectDialog.invitePeopleOnArrival}</span>
                  <Switch
                    checked={invitePeopleAfterCreate}
                    onCheckedChange={setInvitePeopleAfterCreate}
                    disabled={isPending}
                    aria-label={t.createProjectDialog.invitePeopleOnArrival}
                  />
                </label>
              </div>
            </div>

            <div className="flex items-start gap-2.5 rounded-xl border border-border-default px-3 py-2.5">
              <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
                <CrownIcon className="size-4" />
              </span>
              <div className="min-w-0">
                <div className="text-xs text-muted-foreground">
                  {t.createProjectDialog.creatorRoleLabel}
                </div>
                <div className="mt-0.5 text-sm font-medium">
                  {t.createProjectDialog.creatorRole}
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {t.createProjectDialog.creatorRoleDescription}
                </p>
              </div>
            </div>
          </div>
        </div>
        <DialogFooter className="px-6 py-4">
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            {t.createProjectDialog.cancel}
          </Button>
          <Button onClick={handleSubmit} disabled={!name.trim() || isPending}>
            {t.createProjectDialog.create}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
