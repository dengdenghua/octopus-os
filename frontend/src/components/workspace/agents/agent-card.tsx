import {
  BotIcon,
  MessageSquareIcon,
  MoreHorizontalIcon,
  Trash2Icon,
  UserPlusIcon,
} from "lucide-react";

import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { AuthenticatedImage } from "@/components/ui/authenticated-image";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import { useDeleteAgent } from "@/core/agents";
import type { Agent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { taskWorkspaceRoute } from "@/core/router/task-workspace-route";
import {
  DEFAULT_PRIMARY_AGENT_ID,
  isPrimaryPersonaAgentId,
} from "@/core/agents/persona-policy";
import { useActiveAgentId } from "@/core/agents/active";
import {
  taskCollaboratorRouteForLeader,
  writeTaskCollaboratorPreset,
} from "@/core/collaboration/task-collaborator-preset";

interface AgentCardProps {
  agent: Agent;
  /** When true the card is a built-in default and cannot be deleted. */
  isDefault?: boolean;
  /** Fixed squad identities can own chats; all other roles join on demand. */
  isPrimaryIdentity?: boolean;
  onSelect?: (agent: Agent) => void;
}

const ZH_TALENT_CAPABILITY_LABELS: Record<string, string> = {
  web_read: "网页研究",
  browser_read: "浏览分析",
  browser_interact: "网页操作",
  fs_writer: "文档交付",
  git: "代码协作",
  shell: "自动化",
  computer: "桌面操作",
};

export function AgentCard({
  agent,
  isDefault,
  isPrimaryIdentity = isPrimaryPersonaAgentId(agent.name),
  onSelect,
}: AgentCardProps) {
  const { locale, t } = useI18n();
  const navigate = useNavigate();
  const activeAgentId = useActiveAgentId();
  const deleteAgent = useDeleteAgent();
  const { confirm, confirmDialog } = useConfirmDialog();

  function handleChat() {
    if (isPrimaryIdentity) {
      navigate(taskWorkspaceRoute({ agentId: agent.name }));
      return;
    }
    const leaderId = isPrimaryPersonaAgentId(activeAgentId)
      ? activeAgentId
      : DEFAULT_PRIMARY_AGENT_ID;
    writeTaskCollaboratorPreset({
      leaderId,
      collaboratorIds: [agent.name],
      mode: "cluster",
      label: agent.display_name || agent.name,
      openPicker: true,
    });
    navigate(taskCollaboratorRouteForLeader(leaderId));
  }

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast.success(t.agents.deleteSuccess);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  const displayName = agent.display_name ?? agent.name;
  const talentTags = Array.from(
    new Set(
      [...(agent.tool_groups ?? []), agent.model]
        .filter((tag): tag is string => Boolean(tag?.trim()))
        .map((tag) => tag.trim())
        .map((tag) =>
          locale === "zh-CN" ? (ZH_TALENT_CAPABILITY_LABELS[tag] ?? tag) : tag,
        ),
    ),
  ).slice(0, 3);

  return (
    <>
      <Card className="group flex min-h-36 flex-col overflow-hidden rounded-xl border-border-subtle bg-card py-0 shadow-none transition-colors hover:border-border-default hover:bg-muted/10">
        <button
          type="button"
          disabled={!onSelect}
          aria-label={t.agentCard.profileAriaLabel(displayName)}
          className="block w-full cursor-pointer rounded-t-xl text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-default"
          onClick={() => onSelect?.(agent)}
        >
          <CardHeader className="px-4 pb-2 pt-3.5">
            <div className="flex items-start gap-3">
              <div className="relative flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border-subtle bg-muted text-base leading-none">
                {agent.avatar_url ? (
                  <AuthenticatedImage
                    src={withAgentAvatarVersion(agent.avatar_url)}
                    alt={displayName}
                    className="h-full w-full bg-muted object-cover [image-rendering:pixelated]"
                    fallback={
                      agent.icon ? (
                        <span className="flex h-full w-full items-center justify-center rounded-lg bg-muted text-foreground/80">
                          {agent.icon}
                        </span>
                      ) : (
                        <span className="flex h-full w-full items-center justify-center rounded-lg bg-muted text-muted-foreground">
                          <BotIcon className="h-4 w-4" />
                        </span>
                      )
                    }
                  />
                ) : agent.icon ? (
                  <span className="flex h-full w-full items-center justify-center rounded-lg bg-muted text-foreground/80">
                    {agent.icon}
                  </span>
                ) : (
                  <span className="flex h-full w-full items-center justify-center rounded-lg bg-muted text-muted-foreground">
                    <BotIcon className="h-4 w-4" />
                  </span>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <CardTitle className="truncate text-sm font-semibold leading-5">
                  {displayName}
                </CardTitle>
                <CardDescription
                  className="mt-0.5 line-clamp-2 text-xs leading-5 text-muted-foreground"
                  title={agent.description}
                >
                  {agent.description}
                </CardDescription>
              </div>
            </div>

            {talentTags.length > 0 && (
              <div
                className="mt-2.5 flex min-h-5 flex-wrap gap-1.5"
                aria-label={talentTags.join(", ")}
              >
                {talentTags.map((tag) => (
                  <span
                    key={tag}
                    className="max-w-32 truncate rounded-md bg-muted/60 px-2 py-0.5 text-[11px] font-normal text-muted-foreground"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </CardHeader>
        </button>

        <CardFooter className="mt-auto flex items-center gap-1 px-4 pb-3 pt-0">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 flex-1 justify-start rounded-md px-0 text-xs font-medium text-muted-foreground shadow-none hover:bg-transparent hover:text-foreground"
            onClick={(event) => {
              event.stopPropagation();
              handleChat();
            }}
            aria-label={
              isPrimaryIdentity
                ? t.agentCard.chatAriaLabel(displayName)
                : t.agentCard.addOnDemandAriaLabel(displayName)
            }
            data-agent-entry={isPrimaryIdentity ? "identity" : "on-demand"}
          >
            {isPrimaryIdentity ? (
              <MessageSquareIcon className="mr-1.5 h-3.5 w-3.5" />
            ) : (
              <UserPlusIcon className="mr-1.5 h-3.5 w-3.5" />
            )}
            {isPrimaryIdentity ? t.agentCard.chat : t.agentCard.addOnDemand}
          </Button>
          {(onSelect || !isDefault) && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-7 shrink-0 rounded-md text-muted-foreground"
                  onClick={(event) => {
                    event.stopPropagation();
                  }}
                  title={t.common.more}
                  aria-label={`${t.common.more}：${displayName}`}
                >
                  <MoreHorizontalIcon className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-36">
                {onSelect ? (
                  <DropdownMenuItem
                    aria-label={t.agentCard.profileAriaLabel(displayName)}
                    onSelect={() => onSelect(agent)}
                  >
                    <BotIcon />
                    {t.agentCard.profile}
                  </DropdownMenuItem>
                ) : null}
                {!isDefault ? (
                  <DropdownMenuItem
                    variant="destructive"
                    aria-label={t.agentCard.deleteAriaLabel(displayName)}
                    onSelect={async () => {
                      if (
                        await confirm({
                          title: t.agentCard.deleteTitle(displayName),
                          description: t.agentCard.deleteConfirm(displayName),
                          confirmLabel: t.common.delete,
                        })
                      ) {
                        void handleDelete();
                      }
                    }}
                  >
                    <Trash2Icon />
                    {t.common.delete}
                  </DropdownMenuItem>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </CardFooter>
      </Card>

      {confirmDialog}
    </>
  );
}
