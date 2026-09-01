import type { Message } from "@/core/api/types";
import type { CoworkRoomMessage } from "@/core/cowork";
import {
  CheckCircle2Icon,
  DnaIcon,
  FileIcon,
  GitForkIcon,
  Loader2Icon,
  PencilIcon,
  RefreshCwIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  XCircleIcon,
} from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ImgHTMLAttributes,
  type ReactNode,
} from "react";
import { toast } from "sonner";

import { Loader } from "@/components/ai-elements/loader";
import {
  Message as AIElementMessage,
  MessageContent as AIElementMessageContent,
  MessageResponse as AIElementMessageResponse,
} from "@/components/ai-elements/message";
import { Task, TaskTrigger } from "@/components/ai-elements/task";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import {
  artifactRefFromMarkdownHref,
  dispatchOpenArtifact,
} from "@/core/artifacts/open-artifact";
import { resolveArtifactURL } from "@/core/artifacts/utils";
import { jsonAuthHeaders } from "@/core/auth/api";
import { canAccessOperatorControlPlane } from "@/core/auth/control-plane-access";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import {
  getDualHelixShadowStatus,
  queueDualHelixShadowRun,
  type DualHelixShadowStatus,
} from "@/core/evolution/api";
import { useForkThread } from "@/core/threads/hooks";
import type { ExecutionPlan } from "@/core/threads/types";
import {
  extractContentFromMessage,
  extractTextFromMessage,
  parseUploadedFiles,
  stripInternalToolProtocol,
  stripLeakedRendererMarkup,
  stripUploadedFilesTag,
  type FileInMessage,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { useHumanMessagePlugins } from "@/core/streamdown";
import { cn } from "@/lib/utils";
import { useOptionalAuth } from "@/providers/AuthProvider";

import { CopyButton } from "../copy-button";
import {
  CoworkRoomMessageActions,
  type CoworkRoomMessageActionsProps,
} from "../collab";
import { emitOpenAgentWorkbench } from "../agent-workbench-events";
import {
  ExecutionPlanReview,
  isExecutionPlanMessage,
  getExecutionPlanFromMessage,
} from "../execution-plan-review";
import {
  TaskProgressChecklist,
  isTaskChecklistMessage,
  getChecklistPlanFromMessage,
} from "../task-progress-checklist";
import { normalizeExecutionPlan } from "../execution-plan-utils";

import { MarkdownContent } from "./markdown-content";
import { useThreadValues } from "./context";
import {
  STREAMING_TYPE_PRESETS,
  useStreamingTextBuffer,
} from "@/hooks/use-streaming-text-buffer";
import {
  RETRY_PENDING_MESSAGE_EVENT,
  type OutboundDeliveryState,
} from "@/core/threads/optimistic-messages";
import { ClarificationChoiceCard } from "./clarification-choice-card";
import { GroundingChip } from "./grounding-chip";
import { extractClarificationQuestionnaire } from "../clarification-questionnaire";

export interface MessageListProjectActions extends Omit<
  CoworkRoomMessageActionsProps,
  "message"
> {
  /** Metadata from the hidden room mirror, keyed by source_message_id. */
  messageMetadataBySourceId?: Record<string, CoworkRoomMessage["metadata"]>;
}

export interface ShadowReviewContext {
  goal: string;
  primaryEngine: "echo" | "codex";
  primaryOutput: string;
  threadId: string;
  messageId: string;
  workspacePath?: string | null;
}

type ShadowRun = DualHelixShadowStatus["runs"][number];

function isShadowRunActive(run: ShadowRun | null): boolean {
  return Boolean(
    run && ["queued", "snapshotting", "running"].includes(run.status),
  );
}

export function ShadowReviewAction({
  context,
}: {
  context: ShadowReviewContext;
}) {
  const auth = useOptionalAuth();
  const canReview = canAccessOperatorControlPlane(
    auth?.authStatus ?? null,
    auth?.user ?? null,
  );
  const [run, setRun] = useState<ShadowRun | null>(null);
  const [queueing, setQueueing] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const terminalNotified = useRef(false);

  useEffect(() => {
    if (!canReview) return undefined;
    let active = true;
    void getDualHelixShadowStatus()
      .then((status) => {
        if (!active) return;
        const previous = status.runs.find(
          (item) =>
            item.source_thread_id === context.threadId &&
            item.source_message_id === context.messageId,
        );
        if (previous) {
          terminalNotified.current = ["completed", "failed"].includes(
            previous.status,
          );
          setRun(previous);
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [canReview, context.messageId, context.threadId]);

  useEffect(() => {
    if (!isShadowRunActive(run)) return;
    const timer = window.setInterval(() => {
      void getDualHelixShadowStatus()
        .then((status) => {
          const current = status.runs.find(
            (item) => item.run_id === run?.run_id,
          );
          if (!current) return;
          setRun(current);
          if (
            !terminalNotified.current &&
            ["completed", "failed"].includes(current.status)
          ) {
            terminalNotified.current = true;
            if (current.status === "completed") {
              toast.success("另一引擎已完成影子复核");
            } else {
              toast.error("影子复核未完成，可点击查看原因");
            }
          }
        })
        .catch(() => undefined);
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [run]);

  const queueReview = async () => {
    if (!canReview) return;
    if (run && ["completed", "failed"].includes(run.status)) {
      setDialogOpen(true);
      return;
    }
    if (queueing || isShadowRunActive(run)) return;
    setQueueing(true);
    try {
      const status = await getDualHelixShadowStatus();
      if (!status.enabled) {
        toast.error("请先到“自进化”页面开启影子模式");
        return;
      }
      terminalNotified.current = false;
      const queued = await queueDualHelixShadowRun({
        goal: context.goal.slice(0, 20_000),
        primary_engine: context.primaryEngine,
        primary_output: context.primaryOutput.slice(0, 50_000),
        workspace_path: context.workspacePath || undefined,
        source_thread_id: context.threadId,
        source_message_id: context.messageId,
      });
      setRun(queued);
      toast.success(
        `已交给${queued.shadow_engine === "codex" ? " Codex" : " Echo"} 影子复核`,
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "影子复核提交失败");
    } finally {
      setQueueing(false);
    }
  };

  const active = queueing || isShadowRunActive(run);
  const completed = run?.status === "completed";
  const failed = run?.status === "failed";
  const label = completed
    ? "影子复核已完成，点击查看"
    : failed
      ? "影子复核失败，点击查看"
      : active
        ? "另一引擎正在影子复核"
        : "让另一引擎复核本次任务";

  if (!canReview) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => void queueReview()}
        disabled={active}
        className="inline-flex size-7 items-center justify-center rounded-lg text-foreground/60 transition-all duration-base hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45 disabled:cursor-wait disabled:opacity-60"
        title={label}
        aria-label={label}
      >
        {active ? (
          <Loader2Icon className="size-4 animate-spin" />
        ) : completed ? (
          <CheckCircle2Icon className="size-4 text-success" />
        ) : failed ? (
          <XCircleIcon className="size-4 text-destructive" />
        ) : (
          <DnaIcon className="size-4" />
        )}
      </button>
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[80dvh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>双引擎影子复核</DialogTitle>
            <DialogDescription>
              {run?.shadow_engine === "codex" ? "Codex" : "Echo"}
              在隔离的只读副本中给出的复核结果。
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-xl border border-border bg-muted/25 p-4 text-sm leading-6 whitespace-pre-wrap">
            {run?.result || run?.error || "暂时没有可显示的结果。"}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Build the hidden Team Room copy that gives a canonical thread message a
 * stable Project OS action anchor without rendering the text twice. */
export function threadMessageToCoworkRoomMessage(
  message: Message,
  threadId: string | null,
  messageIndex: number | undefined,
  metadataBySourceId: MessageListProjectActions["messageMetadataBySourceId"],
): CoworkRoomMessage {
  const stableMessageId = message.id
    ? String(message.id)
    : `${threadId ?? "thread"}:${messageIndex ?? "message"}`;
  const sourceMessageId = `thread:${stableMessageId}`;
  return {
    seq: -1,
    participant_id: "human",
    display_name: "我",
    text: extractTextFromMessage(message),
    metadata: metadataBySourceId?.[sourceMessageId] ?? {
      source_message_id: sourceMessageId,
    },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function outboundDeliveryState(message: Message): OutboundDeliveryState | null {
  if (message.type !== "human") return null;
  const state = message.additional_kwargs?.delivery_state;
  return state === "queued" || state === "sending" || state === "failed"
    ? state
    : null;
}

export function HumanMessageDeliveryStatus({
  message,
  threadId,
}: {
  message: Message;
  threadId?: string | null;
}) {
  const { t } = useI18n();
  const state = outboundDeliveryState(message);
  if (!state) return null;
  const clientMessageId = message.id;
  const canRetry =
    state === "failed" &&
    typeof clientMessageId === "string" &&
    clientMessageId.length > 0;
  const label =
    state === "queued"
      ? t.conversation.messageQueued
      : state === "sending"
        ? t.conversation.messageSending
        : t.conversation.messageSendFailed;
  const error = message.additional_kwargs?.delivery_error;
  return (
    <div
      className={cn(
        "mt-1 flex items-center justify-end gap-1.5 text-[11px] leading-4",
        state === "failed" ? "text-destructive/80" : "text-muted-foreground/70",
      )}
      data-testid="human-message-delivery-status"
      data-delivery-state={state}
      role="status"
      title={typeof error === "string" ? error : undefined}
    >
      {state === "sending" ? (
        <Loader2Icon className="size-3 animate-spin" aria-hidden="true" />
      ) : (
        <span
          className={cn(
            "size-1.5 rounded-full",
            state === "failed" ? "bg-destructive/70" : "bg-current/45",
          )}
          aria-hidden="true"
        />
      )}
      <span>{label}</span>
      {canRetry ? (
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded px-1 py-0.5 font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45"
          data-testid="retry-pending-message"
          onClick={() => {
            window.dispatchEvent(
              new CustomEvent(RETRY_PENDING_MESSAGE_EVENT, {
                detail: { threadId, clientMessageId },
              }),
            );
          }}
        >
          <RefreshCwIcon className="size-3" aria-hidden="true" />
          {t.conversation.retry}
        </button>
      ) : null}
    </div>
  );
}

const INTERNAL_TRACE_DETAILS_RE =
  /^\s*<details\b[^>]*>\s*<summary\b[^>]*>\s*[^<]*(?:ReAct|\u8f68\u8ff9)[^<]*<\/summary>[\s\S]*?<\/details>\s*/i;

const INLINE_THINKING_DETAILS_CAPTURE_RE =
  /^\s*<details\b[^>]*>\s*<summary\b[^>]*>\s*[^<]*(?:\u601d\u8003\u8fc7\u7a0b|Thinking)[^<]*<\/summary>([\s\S]*?)<\/details>\s*/i;

const LEGACY_SUBAGENT_BUDGET_PLACEHOLDER_RE =
  /^\s*(?:\[[^\]\n]+\]\s*)?\(sub-agent exceeded token budget \d+\/\d+\)\s*$/i;

function stripInternalTraceDetails(content: string): string {
  let next = content;
  for (let i = 0; i < 4; i += 1) {
    const stripped = next.replace(INTERNAL_TRACE_DETAILS_RE, "");
    if (stripped === next) break;
    next = stripped.trimStart();
  }
  return next;
}

function stripLegacySubagentBudgetPlaceholder(content: string): string {
  return LEGACY_SUBAGENT_BUDGET_PLACEHOLDER_RE.test(content) ? "" : content;
}

function splitInlineThinkingDetails(content: string) {
  const match = content.match(INLINE_THINKING_DETAILS_CAPTURE_RE);
  if (!match) {
    return { content, hadInlineThinking: false, thinkingContent: null };
  }
  const fullMatch = match[0] ?? "";
  const thinkingContent = (match[1] ?? "").trim();
  return {
    content: content.slice(fullMatch.length).trimStart(),
    hadInlineThinking: true,
    thinkingContent: thinkingContent || null,
  };
}

/**
 * Cheap pre-filter for the streaming hot path.
 *
 * The full protocol-cleaning chain (stripInternalToolProtocol →
 * stripInternalTraceDetails → splitInlineThinkingDetails → …) is a dozen
 * whole-string regex passes — fine per settled message, wasteful when it
 * re-runs on every streamed token. Every pattern in that chain requires at
 * least one of the characters below (protocol XML/fence markers, ReAct
 * field headers, guard boilerplate, and the ASCII opening paren of the
 * legacy ``(sub-agent exceeded token budget N/N)`` placeholder — its
 * optional ``[...]`` prefix already matches via ``[``). A reply containing
 * none of them is guaranteed to pass through every stage unchanged, so we
 * can skip the entire chain. First-mark test is O(n) with zero allocation.
 */
const PROTOCOL_FIRST_MARK_RE = /[<`TAFONS质量（({[]/;

export function containsProtocolMarkers(content: string): boolean {
  return PROTOCOL_FIRST_MARK_RE.test(content);
}

function getReasoningSummary(message: Message): string | null {
  const additional = isRecord(message.additional_kwargs)
    ? message.additional_kwargs
    : null;
  const echo = isRecord(additional?.echo) ? additional.echo : null;
  for (const source of [additional, echo]) {
    if (!source) continue;
    const value = source.public_reasoning_summary;
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function buildReasoningSummary(message: Message): string | null {
  // Legacy snapshots used this explicit field for readable reasoning
  // summaries. It remains a reasoning disclosure and is never promoted to
  // normal commentary. Provider aliases are intentionally not guessed here.
  return getReasoningSummary(message);
}

function cleanClipboardText(value: string): string {
  return stripLeakedRendererMarkup(stripInternalToolProtocol(value), {
    trim: true,
  });
}

export function messageClipboardText(message: Message): string {
  const rawContent = extractContentFromMessage(message) ?? "";
  if (message.type === "human") {
    return stripUploadedFilesTag(rawContent).trim();
  }

  const displayContent = splitInlineThinkingDetails(
    stripLegacySubagentBudgetPlaceholder(stripInternalTraceDetails(rawContent)),
  ).content;
  const visibleContent =
    extractClarificationQuestionnaire(displayContent)?.visibleContent ??
    displayContent;
  const cleanedVisible = cleanClipboardText(visibleContent);
  if (cleanedVisible) return cleanedVisible;

  // Preserve the legacy explicit summary in clipboard output without ever
  // falling back to raw provider reasoning or guessed alias fields.
  return cleanClipboardText(getReasoningSummary(message) ?? "");
}

type MarkdownRenderProps = Pick<
  ComponentProps<typeof MarkdownContent>,
  "components" | "rehypePlugins" | "chatFontSize"
>;

function SegmentedReasoningPanel({
  publicThinkingSummary,
  isLoading,
  messageId,
}: MarkdownRenderProps & {
  publicThinkingSummary?: string | null;
  isLoading: boolean;
  messageId?: string;
}) {
  const replyThinking = publicThinkingSummary?.trim() || null;
  if (!replyThinking) return null;
  const summary = replyThinking.replace(/\s+/g, " ").trim();

  return (
    <button
      type="button"
      onClick={() =>
        emitOpenAgentWorkbench({
          tab: "agent",
          eventId: messageId,
          eventKind: "thinking",
          view: "summary",
          processEvent: {
            kind: "thinking",
            summary,
            detail: replyThinking,
            status: isLoading ? "running" : "done",
            count: 1,
          },
        })
      }
      className="group/thinking-row mb-1 flex w-full min-w-0 items-center gap-1.5 rounded-full border border-border/50 bg-muted/40 px-2.5 py-1 text-left text-xs leading-4 text-muted-foreground/70 transition-colors hover:text-muted-foreground hover:border-border"
      data-process-event-id={messageId}
      data-process-event-kind="thinking"
      data-testid="assistant-thinking-event"
      aria-label={summary}
    >
      <span
        className={cn(
          "inline-flex size-1.5 shrink-0 rounded-full bg-muted-foreground/35",
          isLoading && "animate-pulse bg-primary/55",
        )}
      />
      <span className="min-w-0 flex-1 truncate">{summary}</span>
    </button>
  );
}

// Wrapped in React.memo with chatFontSize as an explicit prop.
// Previously, MarkdownContent called useLocalSettings() internally to
// pick up the active chat font size. memo() here blocked prop-change-less
// re-renders, which meant settings changes (driven only by useState inside
// the hook) never reached the memoized subtree. Now we pass chatFontSize
// as a prop through to MarkdownContent, so memo's shallow comparison
// correctly detects font-size changes and re-renders only when needed.
export const MessageListItem = memo(function MessageListItem({
  className,
  message,
  isLoading,
  chatFontSize,
  suppressReasoningPanel = false,
  enableClarificationActions = false,
  isLastMessage = true,
  messageIndex,
  afterContent,
  projectMessageActions,
  shadowReview,
  allowThreadFork = true,
}: {
  className?: string;
  message: Message;
  isLoading?: boolean;
  chatFontSize?: "small" | "medium" | "large";
  suppressReasoningPanel?: boolean;
  enableClarificationActions?: boolean;
  isLastMessage?: boolean;
  messageIndex?: number;
  afterContent?: ReactNode;
  /** Project actions exposed on human bubbles in a bound project group. */
  projectMessageActions?: MessageListProjectActions;
  /** Explicit, opt-in review by the engine opposite to this answer's engine. */
  shadowReview?: ShadowReviewContext;
  /** Legacy on-demand-owned threads are readable but cannot clone that owner. */
  allowThreadFork?: boolean;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const forkThread = useForkThread();
  const isHuman = message.type === "human";
  const deliveryState = outboundDeliveryState(message);
  const messageMetadata =
    message.type === "ai"
      ? (message.additional_kwargs as Record<string, unknown> | undefined)
      : undefined;
  const assistantIsSettledAnswer =
    message.type === "ai" &&
    messageMetadata?.message_kind !== "commentary" &&
    messageMetadata?.public_progress !== true &&
    messageMetadata?.response_state !== "interrupted" &&
    messageMetadata?.response_state !== "failed" &&
    messageMetadata?.run_status !== "streaming";
  const clipboardText = useMemo(() => messageClipboardText(message), [message]);
  const showMessageActions =
    !isLoading &&
    deliveryState === null &&
    (isHuman ||
      (assistantIsSettledAnswer && clipboardText.length > 0 && isLastMessage));
  const params = useParams();
  const threadIdForFeedback = params.threadId ?? params.thread_id ?? null;
  const { messageMetadataBySourceId, ...coworkProjectMessageActions } =
    projectMessageActions ?? {};
  const roomMessageForProjectActions = useMemo<CoworkRoomMessage | null>(() => {
    if (!isHuman || !projectMessageActions) return null;
    return threadMessageToCoworkRoomMessage(
      message,
      threadIdForFeedback,
      messageIndex,
      messageMetadataBySourceId,
    );
  }, [
    isHuman,
    message,
    messageIndex,
    messageMetadataBySourceId,
    projectMessageActions,
    threadIdForFeedback,
  ]);
  const submitFeedback = useCallback(
    async (sentiment: "liked" | "disliked") => {
      const content =
        typeof message.content === "string" ? message.content : "";
      try {
        const res = await fetch(`${getBackendBaseURL()}/api/feedback`, {
          method: "POST",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({
            sentiment,
            message_id: message.id ?? null,
            thread_id: threadIdForFeedback,
            content_preview: content.slice(0, 400),
          }),
        });
        if (!res.ok) {
          throw new Error(`feedback http ${res.status}`);
        }

        toast.success(
          sentiment === "liked"
            ? t.conversation.feedbackThanks
            : t.conversation.feedbackRecorded,
        );
      } catch {
        toast.error(t.conversation.feedbackFailed);
      }
    },
    [message.content, message.id, threadIdForFeedback, t.conversation],
  );

  return (
    <AIElementMessage
      className={cn("group/conversation-message relative w-full", className)}
      from={isHuman ? "user" : "assistant"}
    >
      <MessageContent
        // Human bubbles used to pass `w-fit` here. Combined with the inner
        // AIElementMessageContent's own `w-fit max-w-full min-w-0`, the
        // outer `w-fit` could collapse the flex item's min-width to 0 and
        // push text onto one-character-per-line because the flex item
        // couldn't break in the middle of a 2-char string.
        // Using `max-w-[85%]` instead keeps the right-aligned cap but
        // gives the flex child room to stay on a single horizontal line.
        className={isHuman ? "max-w-[85%] items-end" : "w-full"}
        message={message}
        isLoading={isLoading}
        chatFontSize={chatFontSize}
        suppressReasoningPanel={suppressReasoningPanel}
        enableClarificationActions={enableClarificationActions}
      />
      {isHuman ? (
        <HumanMessageDeliveryStatus
          message={message}
          threadId={threadIdForFeedback}
        />
      ) : null}
      {afterContent}
      {showMessageActions && (
        <div
          className={cn(
            "flex items-center gap-1.5 text-foreground/60",
            isHuman
              ? "pointer-events-none absolute top-full right-0 z-20 mt-0.5 w-auto justify-end rounded-lg bg-background/90 px-1 py-0.5 opacity-0 shadow-[var(--shadow-xs)] transition-opacity group-hover/conversation-message:pointer-events-auto group-hover/conversation-message:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100"
              : "mt-2 w-full",
          )}
        >
          {message.type === "ai" && (
            <>
              <button
                onClick={() => {
                  void submitFeedback("liked");
                }}
                className="inline-flex size-7 items-center justify-center rounded-lg text-foreground/60 transition-all duration-base hover:bg-success/10 hover:text-success focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45 dark:hover:text-success"
                title={t.conversation.goodResponse}
                aria-label={t.conversation.goodResponse}
              >
                <ThumbsUpIcon className="size-4" />
              </button>
              <button
                onClick={() => {
                  void submitFeedback("disliked");
                }}
                className="inline-flex size-7 items-center justify-center rounded-lg text-foreground/60 transition-all duration-base hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45 dark:hover:text-destructive"
                title={t.conversation.badResponse}
                aria-label={t.conversation.badResponse}
              >
                <ThumbsDownIcon className="size-4" />
              </button>
            </>
          )}
          <CopyButton
            clipboardData={clipboardText}
            size="icon-sm"
            className="size-7 rounded-lg border-0 bg-transparent p-0 text-foreground/60 shadow-none transition-colors duration-base hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/45"
          />
          {roomMessageForProjectActions && projectMessageActions ? (
            <CoworkRoomMessageActions
              {...coworkProjectMessageActions}
              message={roomMessageForProjectActions}
              className={cn("min-h-0", projectMessageActions.className)}
            />
          ) : null}
          {allowThreadFork &&
          threadIdForFeedback != null &&
          messageIndex != null ? (
            <button
              onClick={() => {
                forkThread.mutate(
                  {
                    threadId: threadIdForFeedback,
                    atMessageIndex: messageIndex,
                  },
                  {
                    onSuccess: (result) => {
                      toast.success(t.conversation.forkedThread);
                      navigate(`/workspace/realtime/${result.thread_id}`);
                    },
                    onError: () => {
                      toast.error(t.conversation.forkFailed);
                    },
                  },
                );
              }}
              className="inline-flex size-7 items-center justify-center rounded-lg text-foreground/60 transition-all duration-base hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45"
              title={t.conversation.forkFromHere}
              aria-label={t.conversation.forkFromHere}
            >
              <GitForkIcon className="size-4" />
            </button>
          ) : null}
          {message.type === "ai" && shadowReview ? (
            <ShadowReviewAction context={shadowReview} />
          ) : null}
          {message.type === "ai" ? (
            <button
              onClick={() => {
                window.dispatchEvent(
                  new CustomEvent("echo:regenerate", {
                    detail: { threadId: threadIdForFeedback },
                  }),
                );
              }}
              className="inline-flex size-7 items-center justify-center rounded-lg text-foreground/60 transition-all duration-base hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45"
              title={t.conversation.regenerateResponse}
              aria-label={t.conversation.regenerateResponse}
            >
              <RefreshCwIcon className="size-4" />
            </button>
          ) : (
            <button
              onClick={() => {
                const text = extractTextFromMessage(message);
                window.dispatchEvent(
                  new CustomEvent("echo:edit-message", {
                    detail: { text, threadId: threadIdForFeedback },
                  }),
                );
              }}
              className="inline-flex size-6 items-center justify-center rounded-lg text-muted-foreground/70 transition-all duration-base hover:bg-muted/60 hover:text-foreground"
              title={t.conversation.editResend}
              aria-label={t.conversation.editResend}
            >
              <PencilIcon className="size-3.5" />
            </button>
          )}
        </div>
      )}
    </AIElementMessage>
  );
});

/**
 * Custom image component that handles artifact URLs
 */
function MessageImage({
  src,
  alt,
  threadId,
  maxWidth: _maxWidth = "90%",
  ...props
}: React.ImgHTMLAttributes<HTMLImageElement> & {
  threadId: string;
  maxWidth?: string;
}) {
  if (!src) return null;

  const imgClassName = "overflow-hidden rounded-lg max-w-[90%]";

  if (typeof src !== "string") {
    return <img className={imgClassName} src={src} alt={alt} {...props} />;
  }

  const url = src.startsWith("/mnt/") ? resolveArtifactURL(src, threadId) : src;

  return (
    <RoutedMessageLink href={url}>
      <img className={imgClassName} src={url} alt={alt} {...props} />
    </RoutedMessageLink>
  );
}

export function RoutedMessageLink({
  href,
  onClick,
  children,
  ...props
}: ComponentProps<"a">) {
  return (
    <RoutedWebLink
      {...props}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented || !href) return;
        const artifactRef = artifactRefFromMarkdownHref(href);
        if (artifactRef && dispatchOpenArtifact(artifactRef)) {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      openTargetSource="conversation"
    >
      {children}
    </RoutedWebLink>
  );
}

function useLiveExecutionPlan(planFromMessage: ExecutionPlan): ExecutionPlan {
  const { values } = useThreadValues();
  const livePlan = normalizeExecutionPlan(values?.execution_plan);
  return livePlan?.plan_id === planFromMessage.plan_id
    ? livePlan
    : planFromMessage;
}

/**
 * Keep the frequently-changing thread values subscription inside the two
 * message variants that actually need live plan updates. Regular transcript
 * rows must not re-render when an unrelated execution-plan value changes.
 */
function LiveTaskChecklistContent({
  className,
  planFromMessage,
}: {
  className?: string;
  planFromMessage: ExecutionPlan;
}) {
  const plan = useLiveExecutionPlan(planFromMessage);
  return (
    <AIElementMessageContent className={className}>
      <TaskProgressChecklist plan={plan} />
    </AIElementMessageContent>
  );
}

function LiveExecutionPlanContent({
  className,
  planFromMessage,
  threadId,
}: {
  className?: string;
  planFromMessage: ExecutionPlan;
  threadId: string;
}) {
  const plan = useLiveExecutionPlan(planFromMessage);
  return (
    <AIElementMessageContent className={className}>
      <ExecutionPlanReview plan={plan} threadId={threadId} />
    </AIElementMessageContent>
  );
}

function MessageContent_({
  className,
  message,
  isLoading = false,
  chatFontSize,
  suppressReasoningPanel = false,
  enableClarificationActions = false,
}: {
  className?: string;
  message: Message;
  isLoading?: boolean;
  chatFontSize?: "small" | "medium" | "large";
  suppressReasoningPanel?: boolean;
  enableClarificationActions?: boolean;
}) {
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const humanMessagePlugins = useHumanMessagePlugins();
  const { t } = useI18n();
  const isHuman = message.type === "human";
  const params = useParams();
  const thread_id = params.threadId ?? params.thread_id;

  // useRehypeSplitWordsIntoSpans now returns the full plugin stack including
  // rehypeRaw and rehypeKatex, so we don't need to add them again.
  const allRehypePlugins = rehypePlugins;

  // MessageList already narrows this prop to the active streaming row. Using
  // it directly avoids subscribing every historical message to delta updates.
  const isCurrentlyStreaming = isLoading;

  const components = useMemo(
    () => ({
      a: RoutedMessageLink,
      img: (props: ImgHTMLAttributes<HTMLImageElement>) => (
        <MessageImage {...props} threadId={thread_id ?? ""} maxWidth="90%" />
      ),
    }),
    [thread_id],
  );

  const rawContent = extractContentFromMessage(message);
  const files = useMemo(() => {
    const files = message.additional_kwargs?.files;
    if (!Array.isArray(files) || files.length === 0) {
      if (rawContent.includes("<uploaded_files>")) {
        // If the content contains the <uploaded_files> tag, we return the parsed files from the content for backward compatibility.
        return parseUploadedFiles(rawContent);
      }
      return null;
    }
    return files as FileInMessage[];
  }, [message.additional_kwargs?.files, rawContent]);

  // User messages can carry attachments in additional_kwargs.attachments
  // (research files used to go through .files, but images now ride this
  // separate channel so we can fold them into multimodal content arrays).
  const attachments = useMemo(() => {
    const raw = message.additional_kwargs?.attachments;
    if (!Array.isArray(raw) || raw.length === 0) return null;
    return raw as Array<{
      filename?: string;
      mediaType?: string;
      data_url?: string;
      url?: string;
      artifact_url?: string;
    }>;
  }, [message.additional_kwargs?.attachments]);

  const displayContentState = useMemo(() => {
    if (isHuman) {
      return {
        content: rawContent ? stripUploadedFilesTag(rawContent) : "",
        hadInlineThinking: false,
        thinkingContent: null,
      };
    }
    const source = rawContent ?? "";
    // Streaming fast path: skip the whole protocol-cleaning regex chain for
    // content that contains none of the protocol first-marks (see
    // containsProtocolMarkers). The chain is idempotent, so settled
    // messages and marked streaming content still get the full treatment.
    if (!containsProtocolMarkers(source)) {
      return {
        content: source,
        hadInlineThinking: false,
        thinkingContent: null,
      };
    }
    return splitInlineThinkingDetails(
      stripInternalToolProtocol(
        stripLegacySubagentBudgetPlaceholder(stripInternalTraceDetails(source)),
      ),
    );
  }, [rawContent, isHuman]);
  const contentToDisplay = displayContentState.content;
  const structuredClarification = useMemo(
    () =>
      isHuman ? null : extractClarificationQuestionnaire(contentToDisplay),
    [contentToDisplay, isHuman],
  );
  const visibleContentToDisplay =
    structuredClarification?.visibleContent ?? contentToDisplay;
  // Body typewriter (WorkBuddy-style buffer playback): while this message is
  // actively receiving streamed tokens, play the content back at a smooth
  // tick rate instead of re-rendering markdown on every delta. When the
  // stream ends (`enabled` flips off), the short remaining tail is drained
  // with a bounded delay before the complete source is shown. Guard against
  // the target text
  // shrinking mid-stream (e.g. a draft being replaced): if the buffer ever
  // exceeds the target, fall back to the source text directly.
  const bufferedBody = useStreamingTextBuffer({
    targetText: visibleContentToDisplay,
    enabled: isCurrentlyStreaming,
    resetKey: message.id,
    ...STREAMING_TYPE_PRESETS.finalAnswer,
  });
  const renderedBody =
    bufferedBody.length <= visibleContentToDisplay.length
      ? bufferedBody
      : visibleContentToDisplay;
  const messageHasToolCalls =
    !isHuman &&
    Array.isArray((message as { tool_calls?: unknown[] }).tool_calls) &&
    ((message as { tool_calls?: unknown[] }).tool_calls?.length ?? 0) > 0;
  const publicThinkingSummary = useMemo(() => {
    if (isHuman) return null;
    return buildReasoningSummary(message);
  }, [isHuman, message]);
  const hasVisibleBody = Boolean(
    visibleContentToDisplay.trim() ||
    structuredClarification ||
    publicThinkingSummary ||
    (files?.length ?? 0) > 0,
  );
  const responseState = (
    message.additional_kwargs as { response_state?: unknown } | undefined
  )?.response_state;
  const legacyRunStatus = (
    message.additional_kwargs as { run_status?: unknown } | undefined
  )?.run_status;
  const interruptReason = (
    message.additional_kwargs as { interrupt_reason?: unknown } | undefined
  )?.interrupt_reason;
  const showInterruptedReceipt =
    responseState === "interrupted" ||
    (legacyRunStatus === "streaming" && hasVisibleBody);
  const showPausedReceipt = responseState === "paused";
  const showCancelledReceipt = responseState === "cancelled";
  const filesList =
    files && files.length > 0 && thread_id ? (
      <RichFilesList files={files} threadId={thread_id} />
    ) : null;

  const attachmentsList = useMemo(() => {
    if (!attachments) return null;
    const images = attachments.filter((att) => {
      const mt = (att.mediaType ?? "").toLowerCase();
      const url = att.data_url ?? att.url ?? att.artifact_url ?? "";
      return mt.startsWith("image/") || url.startsWith("data:image/");
    });
    if (images.length === 0) return null;
    return (
      <div className="mb-2 flex flex-wrap justify-end gap-2">
        {images.map((att, idx) => {
          const src = att.data_url || att.url || att.artifact_url || "";
          if (!src) return null;
          return (
            <RoutedMessageLink
              key={`att-${idx}-${att.filename ?? idx}`}
              href={src}
              className="block overflow-hidden rounded-lg border border-border-subtle"
            >
              <img
                src={src}
                alt={att.filename ?? t.message.attachmentFallback}
                className="h-32 w-auto max-w-60 object-cover"
              />
            </RoutedMessageLink>
          );
        })}
      </div>
    );
  }, [attachments, t.message.attachmentFallback]);

  // Uploading state: mock AI message shown while files upload
  if (message.additional_kwargs?.element === "task") {
    return (
      <AIElementMessageContent className={className}>
        <Task defaultOpen={false}>
          <TaskTrigger title="">
            <div className="text-muted-foreground flex w-full cursor-default items-center gap-2 text-sm select-none">
              <Loader className="size-4" />
              <span>{visibleContentToDisplay}</span>
            </div>
          </TaskTrigger>
        </Task>
      </AIElementMessageContent>
    );
  }

  // Lightweight task checklist "" auto-mode plan (no approval needed)
  if (isTaskChecklistMessage(message)) {
    const planFromMessage = getChecklistPlanFromMessage(message);
    if (planFromMessage) {
      return (
        <LiveTaskChecklistContent
          className={className}
          planFromMessage={planFromMessage}
        />
      );
    }
  }

  // Execution plan review card "" shown inline when the middleware generates a plan
  if (isExecutionPlanMessage(message) && thread_id) {
    const planFromMessage = getExecutionPlanFromMessage(message);
    if (planFromMessage) {
      return (
        <LiveExecutionPlanContent
          className={className}
          planFromMessage={planFromMessage}
          threadId={thread_id}
        />
      );
    }
  }

  // Reasoning-only AI message (no main response content yet) "" just show
  // the collapsible thinking panel on its own.
  if (
    !suppressReasoningPanel &&
    !isHuman &&
    publicThinkingSummary &&
    !rawContent
  ) {
    return (
      <AIElementMessageContent className={className}>
        <SegmentedReasoningPanel
          publicThinkingSummary={publicThinkingSummary}
          isLoading={isLoading}
          messageId={message.id}
          rehypePlugins={allRehypePlugins}
          components={components}
          chatFontSize={chatFontSize}
        />
      </AIElementMessageContent>
    );
  }

  // AI message with BOTH reasoning and a final response "" render the
  // thinking panel above the response so users can still drill into the
  // model's chain of thought after the answer arrives. Defaults to
  // collapsed so the thinking trace doesn't dominate the visible
  // response; users click the trigger to expand and review.
  // Thinking: open while streaming, auto-collapse when done
  const segmentedReasoningPanel =
    !suppressReasoningPanel &&
    !isHuman &&
    !messageHasToolCalls &&
    publicThinkingSummary ? (
      <SegmentedReasoningPanel
        publicThinkingSummary={publicThinkingSummary}
        isLoading={isLoading}
        messageId={message.id}
        rehypePlugins={allRehypePlugins}
        components={components}
        chatFontSize={chatFontSize}
      />
    ) : null;
  if (isHuman) {
    const messageResponse = visibleContentToDisplay ? (
      <AIElementMessageResponse
        remarkPlugins={humanMessagePlugins.remarkPlugins}
        rehypePlugins={humanMessagePlugins.rehypePlugins}
        components={components}
      >
        {visibleContentToDisplay}
      </AIElementMessageResponse>
    ) : null;
    return (
      // items-end right-aligns the inner bubble; flex-col keeps files
      // stacked above the message body. Removing the explicit `w-fit` on
      // AIElementMessageContent lets it inherit its own default width
      // behaviour (`w-fit max-w-[85%]` via the .is-user group selector)
      // without compounding with an outer `w-fit` on the wrapper.
      <div className={cn("ml-auto flex flex-col items-end gap-2", className)}>
        {filesList}
        {attachmentsList}
        {messageResponse && (
          <AIElementMessageContent>{messageResponse}</AIElementMessageContent>
        )}
      </div>
    );
  }

  return (
    <AIElementMessageContent className={className}>
      {filesList}
      {attachmentsList}
      <GroundingChip message={message} />
      {segmentedReasoningPanel}
      {visibleContentToDisplay.trim() && (
        <div className="relative">
          <MarkdownContent
            content={renderedBody}
            isLoading={isLoading}
            rehypePlugins={allRehypePlugins}
            className={cn(
              "my-3",
              isCurrentlyStreaming && "kimi-streaming-tail",
            )}
            components={components}
            chatFontSize={chatFontSize}
          />
        </div>
      )}
      <ClarificationChoiceCard
        content={contentToDisplay}
        active={enableClarificationActions && !isCurrentlyStreaming}
        messageId={message.id}
      />
      {/* Terminal receipt for an interrupted answer. The incomplete draft is
          intentionally absent from the transcript; tools and checkpoints
          remain available through the process workbench. Legacy persisted
          `run_status=streaming` messages retain the same honest receipt. */}
      {!isCurrentlyStreaming && showInterruptedReceipt && (
        <div className="mt-2 text-xs leading-5 text-muted-foreground/70">
          {typeof interruptReason === "string" && interruptReason.trim()
            ? `${t.conversation.interruptedMessage}（原因：${interruptReason}）`
            : t.conversation.interruptedMessage}
        </div>
      )}
      {!isCurrentlyStreaming && showPausedReceipt && (
        <div className="mt-2 rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-xs leading-5 text-muted-foreground">
          {typeof interruptReason === "string" && interruptReason.trim()
            ? `${t.conversation.pausedMessage}（${interruptReason}）`
            : t.conversation.pausedMessage}
        </div>
      )}
      {!isCurrentlyStreaming && showCancelledReceipt && (
        <div className="mt-2 text-xs leading-5 text-muted-foreground/70">
          {t.conversation.cancelledMessage}
        </div>
      )}
    </AIElementMessageContent>
  );
}

/**
 * Get file extension and check helpers
 */
const getFileExt = (filename: string) =>
  filename.split(".").pop()?.toLowerCase() ?? "";

const FILE_TYPE_MAP: Record<string, string> = {
  json: "JSON",
  csv: "CSV",
  txt: "TXT",
  md: "Markdown",
  py: "Python",
  js: "JavaScript",
  ts: "TypeScript",
  tsx: "TSX",
  jsx: "JSX",
  html: "HTML",
  css: "CSS",
  xml: "XML",
  yaml: "YAML",
  yml: "YAML",
  pdf: "PDF",
  png: "PNG",
  jpg: "JPG",
  jpeg: "JPEG",
  gif: "GIF",
  svg: "SVG",
  zip: "ZIP",
  tar: "TAR",
  gz: "GZ",
};

const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"];

function getFileTypeLabel(filename: string, fileFallback: string): string {
  const ext = getFileExt(filename);
  return FILE_TYPE_MAP[ext] ?? (ext.toUpperCase() || fileFallback);
}

function isImageFile(filename: string): boolean {
  return IMAGE_EXTENSIONS.includes(getFileExt(filename));
}

/**
 * Format bytes to human-readable size string
 */
function formatBytes(
  bytes: number,
  units: { b: string; kb: string; mb: string },
): string {
  if (bytes === 0) return `0 ${units.b}`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} ${units.kb}`;
  return `${(kb / 1024).toFixed(1)} ${units.mb}`;
}

/**
 * List of files from additional_kwargs.files (with optional upload status)
 */
function RichFilesList({
  files,
  threadId,
}: {
  files: FileInMessage[];
  threadId: string;
}) {
  if (files.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap justify-end gap-2">
      {files.map((file, index) => (
        <RichFileCard
          key={`${file.filename}-${index}`}
          file={file}
          threadId={threadId}
        />
      ))}
    </div>
  );
}

/**
 * Single file card that handles FileInMessage (supports uploading state)
 */
function RichFileCard({
  file,
  threadId,
}: {
  file: FileInMessage;
  threadId: string;
}) {
  const { t } = useI18n();
  const isUploading = file.status === "uploading";
  const isImage = isImageFile(file.filename);

  if (isUploading) {
    return (
      <div className="bg-background border-border-subtle flex max-w-50 min-w-30 flex-col gap-1 rounded-lg border p-3 opacity-60 shadow-[var(--shadow-xs)]">
        <div className="flex items-start gap-2">
          <Loader2Icon className="text-muted-foreground mt-0.5 size-4 shrink-0 animate-spin" />
          <span
            className="text-foreground truncate text-sm font-medium"
            title={file.filename}
          >
            {file.filename}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <Badge
            variant="secondary"
            className="rounded px-1.5 py-0.5 text-xs font-normal"
          >
            {getFileTypeLabel(file.filename, t.messageGrouping.fileFallback)}
          </Badge>
          <span className="text-muted-foreground text-xs">
            {t.uploads.uploading}
          </span>
        </div>
      </div>
    );
  }

  if (!file.path) return null;

  const fileUrl = resolveArtifactURL(file.path, threadId);

  if (isImage) {
    return (
      <RoutedMessageLink
        href={fileUrl}
        className="border-border-subtle relative block overflow-hidden rounded-lg border"
      >
        <img
          src={fileUrl}
          alt={file.filename}
          className="h-32 w-auto max-w-60 object-cover"
        />
      </RoutedMessageLink>
    );
  }

  return (
    <div className="bg-background border-border-subtle flex max-w-50 min-w-30 flex-col gap-1 rounded-lg border p-3 shadow-[var(--shadow-xs)]">
      <div className="flex items-start gap-2">
        <FileIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <span
          className="text-foreground truncate text-sm font-medium"
          title={file.filename}
        >
          {file.filename}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <Badge
          variant="secondary"
          className="rounded px-1.5 py-0.5 text-xs font-normal"
        >
          {getFileTypeLabel(file.filename, t.messageGrouping.fileFallback)}
        </Badge>
        <span className="text-muted-foreground text-xs">
          {formatBytes(file.size, {
            b: t.common.fileSizeB,
            kb: t.common.fileSizeKB,
            mb: t.common.fileSizeMB,
          })}
        </span>
      </div>
    </div>
  );
}

const MessageContent = memo(MessageContent_);
