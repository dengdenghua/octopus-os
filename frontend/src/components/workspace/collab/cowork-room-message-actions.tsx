import { useMemo, useState } from "react";
import {
  AtSignIcon,
  CheckCircle2Icon,
  CheckIcon,
  EllipsisIcon,
  FileUpIcon,
  Link2Icon,
  ListPlusIcon,
  Loader2Icon,
  ReplyIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  extractCoworkAgentMentions,
  useApplyCollabRoomMessageProjectAction,
  type CoworkMessageProjectAction,
  type CoworkMessageProjectActionInput,
  type CoworkMessageProjectActionResponse,
  type CoworkRoomMessage,
} from "@/core/cowork";
import { cn } from "@/lib/utils";

export interface CoworkMilestoneOption {
  id: string;
  name: string;
  status?: string;
}

interface ActionContext {
  projectId?: string;
  milestoneId?: string;
}

function messageTitle(message: CoworkRoomMessage): string {
  const line = message.text
    .split(/\r?\n/)
    .map((item) => item.trim())
    .find(Boolean);
  return (line || "群聊事项").slice(0, 160);
}

/** Deterministic defaults keep menu actions useful without opening a form.
 * Callers can still intercept and enrich the input through `onActionRequest`. */
export function buildCoworkMessageProjectActionInput(
  action: CoworkMessageProjectAction,
  message: CoworkRoomMessage,
  context: ActionContext = {},
): CoworkMessageProjectActionInput {
  const base = {
    action,
    ...(context.projectId ? { project_id: context.projectId } : {}),
  };
  switch (action) {
    case "link_milestone":
      return { ...base, milestone_id: context.milestoneId };
    case "create_item": {
      const assignee = extractCoworkAgentMentions(message.text)[0];
      return {
        ...base,
        milestone_id: context.milestoneId,
        title: messageTitle(message),
        description: message.text.trim(),
        task_type: "analysis",
        priority: "P2",
        ...(assignee ? { assigned_agent: assignee } : {}),
      };
    }
    case "record_decision":
      return {
        ...base,
        decision: message.text.trim(),
      };
    case "publish_artifact":
      return {
        ...base,
        artifact: {
          title: messageTitle(message),
          summary: message.text.trim(),
          source: "cowork_room_message",
          source_message_seq: message.seq,
        },
      };
  }
}

export interface CoworkRoomMessageActionsProps {
  threadId?: string | null;
  message: CoworkRoomMessage;
  projectId?: string;
  milestones?: CoworkMilestoneOption[];
  defaultMilestoneId?: string;
  disabled?: boolean;
  onReply?: (message: CoworkRoomMessage) => void;
  onMentionAuthor?: (message: CoworkRoomMessage) => void;
  /** Optional integration seam for a confirmation dialog or custom mutation. */
  onActionRequest?: (
    input: CoworkMessageProjectActionInput,
    message: CoworkRoomMessage,
  ) => void | Promise<void>;
  onActionApplied?: (
    response: CoworkMessageProjectActionResponse | undefined,
    input: CoworkMessageProjectActionInput,
  ) => void;
  onActionError?: (error: Error) => void;
  className?: string;
}

export function CoworkRoomMessageActions({
  threadId,
  message,
  projectId,
  milestones = [],
  defaultMilestoneId,
  disabled = false,
  onReply,
  onMentionAuthor,
  onActionRequest,
  onActionApplied,
  onActionError,
  className,
}: CoworkRoomMessageActionsProps) {
  const mutation = useApplyCollabRoomMessageProjectAction();
  const [localPending, setLocalPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = mutation.isPending || localPending;
  const applied = useMemo(
    () =>
      new Set(
        (message.metadata?.project_actions ?? []).map((item) => item.action),
      ),
    [message.metadata?.project_actions],
  );
  const milestoneOptions = useMemo(() => {
    if (milestones.length > 0) return milestones;
    return defaultMilestoneId
      ? [{ id: defaultMilestoneId, name: "当前里程碑" }]
      : [];
  }, [defaultMilestoneId, milestones]);
  const projectActionDisabled =
    disabled || pending || (!threadId && !onActionRequest);

  const requestAction = async (
    action: CoworkMessageProjectAction,
    milestoneId?: string,
  ) => {
    const input = buildCoworkMessageProjectActionInput(action, message, {
      projectId,
      milestoneId,
    });
    setError(null);
    try {
      let response: CoworkMessageProjectActionResponse | undefined;
      if (onActionRequest) {
        setLocalPending(true);
        await onActionRequest(input, message);
      } else {
        response = await mutation.mutateAsync({
          threadId: threadId!,
          messageSeq: message.seq,
          input,
        });
      }
      onActionApplied?.(response, input);
    } catch (cause) {
      const nextError =
        cause instanceof Error ? cause : new Error("项目动作执行失败");
      setError(nextError.message);
      onActionError?.(nextError);
    } finally {
      setLocalPending(false);
    }
  };

  const milestoneAction = (
    action: "link_milestone" | "create_item",
    label: string,
    Icon: typeof Link2Icon,
  ) => {
    if (milestoneOptions.length === 0) {
      return (
        <DropdownMenuItem disabled>
          <Icon />
          {label}
          <span className="ml-auto text-[10px]">需里程碑</span>
        </DropdownMenuItem>
      );
    }
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger disabled={projectActionDisabled}>
          <Icon />
          {label}
          {applied.has(action) ? <CheckIcon className="ml-auto" /> : null}
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className="min-w-44">
          {milestoneOptions.map((milestone) => (
            <DropdownMenuItem
              key={milestone.id}
              onSelect={() => void requestAction(action, milestone.id)}
            >
              <span className="min-w-0 flex-1 truncate">{milestone.name}</span>
              {milestone.status ? (
                <span className="text-[10px] text-muted-foreground">
                  {milestone.status}
                </span>
              ) : null}
            </DropdownMenuItem>
          ))}
        </DropdownMenuSubContent>
      </DropdownMenuSub>
    );
  };

  return (
    <div
      className={cn("flex min-h-7 items-center gap-0.5", className)}
      data-testid="cowork-message-actions"
    >
      {onReply ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="size-7"
          aria-label="回复消息"
          disabled={disabled}
          onClick={() => onReply(message)}
        >
          <ReplyIcon className="size-3.5" />
        </Button>
      ) : null}
      {onMentionAuthor && message.participant_id ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="size-7"
          aria-label={`提及 ${message.display_name || message.participant_id}`}
          disabled={disabled}
          onClick={() => onMentionAuthor(message)}
        >
          <AtSignIcon className="size-3.5" />
        </Button>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="size-7"
            aria-label="消息项目操作"
            disabled={projectActionDisabled}
          >
            {pending ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <EllipsisIcon className="size-3.5" />
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-48">
          {milestoneAction("link_milestone", "关联里程碑", Link2Icon)}
          {milestoneAction("create_item", "创建项目事项", ListPlusIcon)}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={projectActionDisabled}
            onSelect={() => void requestAction("record_decision")}
          >
            <CheckCircle2Icon />
            记录为项目决策
            {applied.has("record_decision") ? (
              <CheckIcon className="ml-auto" />
            ) : null}
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={projectActionDisabled}
            onSelect={() => void requestAction("publish_artifact")}
          >
            <FileUpIcon />
            发布为项目资料
            {applied.has("publish_artifact") ? (
              <CheckIcon className="ml-auto" />
            ) : null}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {error ? (
        <span
          role="alert"
          title={error}
          className="max-w-40 truncate text-[10px] text-destructive"
        >
          {error}
        </span>
      ) : null}
    </div>
  );
}
