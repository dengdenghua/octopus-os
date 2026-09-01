import { swallow } from "@/core/utils/log";
import type { AIMessage, Message } from "@/core/api/types";
import {
  BrainIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  SparklesIcon,
  WrenchIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type MutableRefObject,
  type RefObject,
} from "react";

import { ChainOfThought } from "@/components/ai-elements/chain-of-thought";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { isApprovalRequest } from "@/components/workspace/tool-approval-card";
import {
  STREAMING_TYPE_PRESETS,
  useStreamingTextBuffer,
} from "@/hooks/use-streaming-text-buffer";
import { useConversationDetailLevel } from "./use-conversation-detail-level";
import {
  type AgentRunState,
  agentRunStatusLightClass,
  agentRunStatusLightPulseClass,
} from "../agent-run-status";
import { useI18n } from "@/core/i18n/hooks";
import { useToolEffects } from "@/core/observability/tool-effects-context";
import {
  extractContentFromMessage,
  findToolCallResult,
  hasToolCalls,
  isLikelyFinalAnswerContent,
  isProcessPrelude,
  stripInternalToolProtocol,
  stripLeakedRendererMarkup,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import {
  activateTimelineItem,
  getTimelineLinkageState,
  subscribeTimelineLinkage,
} from "@/core/threads/timeline-linkage";
import { cn } from "@/lib/utils";

import { emitOpenAgentWorkbench } from "../agent-workbench-events";
import { FlipDisplay } from "../flip-display";
import { isAutoVerificationToolName } from "../process-trace-events";
import {
  isFileMutationToolName,
  isReadToolName,
  isShellToolName,
  shellCommandFromInput,
} from "../tool-name-groups";

import { AgentAvatar } from "./agent-message-header";
import { ClarificationChoiceCard } from "./clarification-choice-card";
import { friendlyRoleName } from "../agent-workbench-pages";
import {
  extractFactSummary,
  isToolResultError,
  type FactSummary,
} from "./fact-summary";
import { GroundingChip } from "./grounding-chip";
import { MarkdownContent } from "./markdown-content";
import { stripTraceLabelPrefixes } from "./trace-labels";
import {
  assignTimelineRoles,
  isAnswerContent,
  type TimelineRole,
} from "./timeline-role";
import {
  getActionDisplay,
  getActionIcon,
  aggregateIconName,
  type ActionDisplay,
  type ActionAggregateKind,
} from "./action-display";
import {
  buildCompactTimelineItems,
  isExecutionTimelineItem,
} from "./timeline-pipeline";
import { projectToolNarrative } from "./narrative-block";

const HIDDEN_TIMELINE_TOOL_NAMES = new Set([
  "task",
  "todo_write",
  "write_todos",
  "bb_keys",
  "bb_write",
  "query_skill",
  "skill_search",
  "apply_skill",
  "deep-research",
  "deep_research",
  "deep-research-swarm",
  "recall",
]);
const AUTO_COLLAPSE_PROCESS_STEP_COUNT = 8;
const INTERNAL_PROCESS_BLOCK_RE =
  /`?<(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)\b[^<>`]*>[\s\S]*?<\/(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)>`?/g;
const PROCESS_TEXT_SECRET_RE =
  /\b(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[baprs])[-_][A-Za-z0-9]{8,}\b|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}|\b(?:Bearer|Authorization:?)\s+[A-Za-z0-9._-]{10,}|(["']?(?:api[_-]?key|secret|password|passwd|token)["']?\s*[:=]\s*)["']?[^\s"',}]{4,}/gi;
const PROCESS_TEXT_RAW_TOOL_RE =
  /\b(?:read_file|glob_files|find_files|exec_shell|shell_command|run_command|todo_write|apply_patch|write_file|edit_file|str_replace|web_search|fetch_url|web_fetch)\b/gi;
const PROCESS_TEXT_PROTOCOL_PREFIX_RE =
  /\b(?:Thought|Action|Observation|Final Answer|Tool|Tool Result)\s*:\s*/gi;
const RAW_PUBLIC_TOOL_CALLBACK_RE =
  /^(web_search|fetch_url|web_fetch|read_file|glob_files|find_files|exec_shell|shell_command|run_command|todo_write|apply_patch|write_file|edit_file|str_replace)(?:\s*\([^)]*\))?$/i;

function isHiddenTimelineToolName(name: string): boolean {
  const normalized = name.toLowerCase();
  return (
    HIDDEN_TIMELINE_TOOL_NAMES.has(normalized) ||
    isAutoVerificationToolName(normalized)
  );
}

function isTeamCallToolName(name: string): boolean {
  const normalized = name.toLowerCase();
  return (
    normalized === "call_agent_parallel" ||
    normalized === "call_agent" ||
    normalized === "delegate_agent" ||
    normalized === "spawn_agent" ||
    // Sub-agent lifecycle markers arrive as ``runtime.__subagent_spawned__``
    // / ``__subagent_finished__``. Strict equality above never matched them,
    // so ``isSubagentRow`` stayed false and the avatar branch was
    // unreachable — the row showed a generic Users glyph and no identity.
    normalized.includes("__subagent_spawned__") ||
    normalized.includes("__subagent_finished__")
  );
}

function roleIconFromId(roleId: string): string | null {
  const map: Record<string, string> = {
    architect: "🏗️",
    critic: "⚖️",
    debugger: "🐛",
    designer: "🎨",
    implementer: "🛠️",
    planner: "🗺️",
    researcher: "🔍",
    reviewer: "👁️",
    security: "🛡️",
    "security-review": "🛡️",
    synthesizer: "🧩",
    writer: "✍️",
  };
  return map[roleId.toLowerCase()] ?? null;
}

function subagentIdentityFromArgs(args: Record<string, unknown>): {
  name: string;
  icon?: string | null;
  avatarUrl?: string;
} {
  const explicitIcon =
    (typeof args.icon === "string" && args.icon) ||
    // Lifecycle markers ship a bare emoji under ``avatar``.
    (typeof args.avatar === "string" && args.avatar) ||
    null;
  const avatarUrl =
    typeof args.avatar_url === "string" ? args.avatar_url : undefined;

  // Human-facing identity keys that skip the friendlyRoleName() mapping:
  // ``role_display_name`` / ``codename`` come from the sub-agent lifecycle
  // markers and are already display-ready.
  const pickDisplay = (source: Record<string, unknown>): string => {
    return (
      (typeof source.agent_name === "string" && source.agent_name.trim()) ||
      (typeof source.display_name === "string" && source.display_name.trim()) ||
      (typeof source.role_display_name === "string" &&
        source.role_display_name.trim()) ||
      (typeof source.codename === "string" && source.codename.trim()) ||
      ""
    );
  };

  // Helper to pick the raw identity key and map it to a human-readable role.
  const pickRaw = (source: Record<string, unknown>): string => {
    return (
      pickDisplay(source) ||
      (typeof source.subagent_type === "string" &&
        source.subagent_type.trim()) ||
      (typeof source.agent_id === "string" && source.agent_id.trim()) ||
      (typeof source.name === "string" && source.name.trim()) ||
      (typeof source.role === "string" && source.role.trim()) ||
      ""
    );
  };

  // Top-level single-agent delegation (call_agent / delegate_agent) and
  // sub-agent lifecycle markers. An already human-facing display name is used
  // verbatim; only raw ids go through friendlyRoleName().
  const displayTop = pickDisplay(args);
  const rawTop = pickRaw(args);
  if (rawTop) {
    const roleForIcon =
      typeof args.role === "string" && args.role.trim()
        ? args.role.trim()
        : rawTop;
    return {
      name: displayTop || friendlyRoleName(rawTop),
      icon: explicitIcon ?? roleIconFromId(roleForIcon),
      avatarUrl,
    };
  }

  // Parallel delegation: specs=[{agent_id, prompt}, ...]
  const specs = args.specs;
  if (Array.isArray(specs) && specs.length > 0) {
    const first = specs[0];
    if (first && typeof first === "object") {
      const record = first as Record<string, unknown>;
      const rawFirst = pickRaw(record);
      if (rawFirst) {
        const displayFirst = pickDisplay(record);
        const name = displayFirst || friendlyRoleName(rawFirst);
        return {
          name: specs.length > 1 ? `${name} 等` : name,
          icon: explicitIcon ?? roleIconFromId(rawFirst),
          avatarUrl,
        };
      }
    }
  }

  return { name: "", icon: explicitIcon, avatarUrl };
}

function publicActionTextFromTraceTool(
  name: string,
  target: string | undefined,
  t: ReturnType<typeof useI18n>["t"] | undefined,
): string | null {
  if (!t || isHiddenTimelineToolName(name)) return null;
  const normalized = name.toLowerCase();
  const withTarget = (label: string) =>
    target ? `${label}: ${target}` : label;
  if (isTeamCallToolName(normalized)) {
    return withTarget(t.messageGrouping.callTeammate);
  }
  if (normalized.includes("search") || normalized.includes("glob")) {
    return withTarget(t.messageGrouping.searchSources);
  }
  if (normalized.includes("fetch") || normalized.includes("web_fetch")) {
    return withTarget(t.messageGrouping.readWebpage);
  }
  if (
    normalized.includes("read") ||
    normalized === "ls" ||
    normalized === "list_cwd"
  ) {
    return withTarget(t.messageGrouping.readFile);
  }
  if (
    normalized.includes("write") ||
    normalized.includes("edit") ||
    normalized.includes("replace")
  ) {
    return withTarget(t.messageGrouping.updateFile);
  }
  if (isShellToolName(normalized)) {
    return t.toolCalls.executeCommand;
  }
  // Unknown trace actions are implementation details, not a meaningful
  // public update. Do not invent a generic operation timeline item.
  return null;
}

function normalizePublicTimelineChunk(chunk: string): string | null {
  // Provider planning headings such as
  // ``**Planning...****Inspecting...**`` are private implementation labels,
  // not public progress. Public commentary is carried by message.content.
  const compact = chunk.replace(/\s+/g, "").trim();
  if (/^(?:\*\*[^*\n]{2,180}\*\*){1,6}$/.test(compact)) return null;
  const stripped = stripTraceLabelPrefixes(
    stripLeakedRendererMarkup(
      stripInternalToolProtocol(chunk.replace(INTERNAL_PROCESS_BLOCK_RE, "")),
    )
      .replace(/<\/?(?:tool|tool_call|function|thought|thinking)[^>]*>/gi, " ")
      .replace(/\s+/g, " ")
      .trim(),
  );
  const cleaned = redactPublicProcessText(stripped);
  return cleaned || null;
}

function redactPublicProcessText(value: string): string {
  return value
    .replace(PROCESS_TEXT_SECRET_RE, (_match, prefix?: string) =>
      prefix ? `${prefix}«redacted»` : "«redacted»",
    )
    .replace(PROCESS_TEXT_RAW_TOOL_RE, "operation")
    .replace(PROCESS_TEXT_PROTOCOL_PREFIX_RE, "")
    .trim();
}

/** 公开进度文案：剥离内部协议标记 + 敏感信息，折叠行内空白。
 *
 * 只折叠行内空白（``[^\S\n]+``）并**保留换行**。压平 ``\n`` 会让 markdown
 * 围栏塌到同一行 —— ``` 不再是块级围栏、只能解析成行内 code，整段过程文案
 * 糊成一个段落（语言标记 ``json`` 也混进正文）。保留换行后围栏正常渲染成
 * ``<pre><code>``。
 *
 * IMPORTANT: normalizePublicTimelineChunk MUST run BEFORE the redact path,
 * otherwise commit 142c0501's fence fix is unreachable (publicProcessText
 * short-circuits). */
function publicProcessText(value: string): string {
  const normalized = normalizePublicTimelineChunk(value);
  if (normalized) return normalized;

  return redactPublicProcessText(
    stripTraceLabelPrefixes(
      stripLeakedRendererMarkup(
        stripInternalToolProtocol(value.replace(INTERNAL_PROCESS_BLOCK_RE, "")),
      ),
    ).replace(/[^\S\n]+/g, " "),
  );
}

function dedupeTimelineChunks(chunks: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const chunk of chunks) {
    const normalized = stripTraceLabelPrefixes(chunk)
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(chunk);
  }
  return result;
}

function timelineNarrativeFingerprint(value: string): string {
  return stripTraceLabelPrefixes(value)
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function formatDuration(ms: number): string {
  const roundedSeconds = Math.round(ms / 1000);
  if (roundedSeconds < 60) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(roundedSeconds / 60);
  const remainingSeconds = roundedSeconds % 60;
  return `${minutes}m${remainingSeconds}s`;
}

/**
 * Smoothly glide a live stream window to the newest content instead of
 * snapping. Each typewriter tick calls this with the latest display text;
 * the scroll animates in a few rAF steps so long streams read like a
 * capped sliding window (newest line entering at the bottom) rather than a
 * jump. User scroll-away still pauses the stick.
 */
function useSmoothStickToBottom(
  ref: RefObject<HTMLDivElement | null>,
  stickToBottomRef: MutableRefObject<boolean>,
  trigger: string,
): MutableRefObject<boolean> {
  const targetRef = useRef(0);
  const animationRef = useRef<number | null>(null);
  const autoScrollingRef = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || !stickToBottomRef.current) return;
    targetRef.current = Math.max(0, el.scrollHeight - el.clientHeight);
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReducedMotion) {
      autoScrollingRef.current = true;
      el.scrollTop = targetRef.current;
      autoScrollingRef.current = false;
      return;
    }
    if (typeof requestAnimationFrame !== "function") {
      autoScrollingRef.current = true;
      el.scrollTop = targetRef.current;
      autoScrollingRef.current = false;
      return;
    }
    // A token update only moves the destination. Keep one animation alive so
    // 40ms text ticks cannot repeatedly cancel an ~80ms scroll transition.
    if (animationRef.current !== null) return;
    const follow = () => {
      const current = ref.current;
      if (!current || !stickToBottomRef.current) {
        autoScrollingRef.current = false;
        animationRef.current = null;
        return;
      }
      // Recompute both directions. A live block usually grows, but it can
      // shrink when a provisional trace is replaced or normalised. Keeping
      // the old larger target makes browsers clamp scrollTop while this loop
      // continues chasing an unreachable position forever.
      targetRef.current = Math.max(
        0,
        current.scrollHeight - current.clientHeight,
      );
      const distance = targetRef.current - current.scrollTop;
      autoScrollingRef.current = true;
      if (Math.abs(distance) <= 1) {
        current.scrollTop = targetRef.current;
        autoScrollingRef.current = false;
        animationRef.current = null;
        return;
      }
      current.scrollTop += distance * 0.45;
      animationRef.current = requestAnimationFrame(follow);
    };
    animationRef.current = requestAnimationFrame(follow);
  }, [ref, stickToBottomRef, trigger]);

  useEffect(
    () => () => {
      if (animationRef.current !== null) {
        cancelAnimationFrame(animationRef.current);
      }
    },
    [],
  );

  return autoScrollingRef;
}

const LIVE_STREAM_WINDOW_MAX_HEIGHT_PX = 128;

export function getLiveStreamWindowHeight(naturalHeight: number): number {
  return Math.min(
    LIVE_STREAM_WINDOW_MAX_HEIGHT_PX,
    Math.max(0, Math.ceil(naturalHeight)),
  );
}

/**
 * Let short live output occupy only its real content height, then cap long
 * output at the waterfall-window height. While the window is outside the
 * viewport (the reader is browsing history), freeze the last measured outer
 * height so new tokens cannot move historical content under the reader.
 */
function useViewportAwareLiveWindowHeight(
  ref: RefObject<HTMLDivElement | null>,
  contentRef: RefObject<HTMLDivElement | null>,
  trigger: string,
): number | undefined {
  const isVisibleRef = useRef(true);
  const [height, setHeight] = useState<number>();

  const measure = useCallback(() => {
    const content = contentRef.current;
    if (!content || !isVisibleRef.current) return;
    // Measure an unconstrained inner node, not the scroll viewport itself.
    // Once the viewport has reached its cap, its own scrollHeight can never
    // fall below clientHeight. Reading it made a later shorter update inherit
    // the old 128px height and leave a visible empty block below the text.
    const naturalHeight = Math.ceil(content.scrollHeight);
    if (naturalHeight < 1) return;
    const nextHeight = getLiveStreamWindowHeight(naturalHeight);
    setHeight((current) => (current === nextHeight ? current : nextHeight));
  }, [contentRef]);

  useLayoutEffect(() => {
    measure();
  }, [measure, trigger]);

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => {
      isVisibleRef.current = entry?.isIntersecting ?? true;
      if (isVisibleRef.current) measure();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [measure, ref]);

  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => observer.disconnect();
  }, [contentRef, measure]);

  return height;
}

/**
 * Live typewriter window for the in-flight extended thinking.
 *
 * Reasoning can run for thousands of characters. Letting it grow in document
 * flow pushed the answer far down the transcript, so the window is capped at
 * a fixed height and scrolls internally: newest text enters at the bottom
 * (waterfall), history stays reachable by scrolling up. Scrolling away pauses
 * follow-mode until the reader returns to the bottom.
 */
function LiveThinkingWindow({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  // Typewriter buffer: reveal the stream at a fixed tick rate instead of
  // flashing every delta. Full text appears instantly for
  // prefers-reduced-motion users. See STREAMING_TYPE_PRESETS.liveThinking
  // for the cadence rationale.
  const displayText = useStreamingTextBuffer({
    targetText: text,
    ...STREAMING_TYPE_PRESETS.liveThinking,
  });

  const autoScrollingRef = useSmoothStickToBottom(
    ref,
    stickToBottomRef,
    displayText,
  );
  const windowHeight = useViewportAwareLiveWindowHeight(
    ref,
    contentRef,
    displayText,
  );

  const handleScroll = () => {
    const el = ref.current;
    if (!el || autoScrollingRef.current) return;
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  return (
    <div
      ref={ref}
      onScroll={handleScroll}
      className="live-thinking-window mt-1 ml-4 max-h-32 min-w-0 overflow-y-auto whitespace-pre-wrap border-l-2 border-foreground/15 bg-transparent px-1 pl-3 text-[13px] leading-6 text-muted-foreground"
      data-height-policy="inner-content-capped-history-frozen"
      data-testid="live-thinking-stream"
      style={{ height: windowHeight }}
    >
      <div
        ref={contentRef}
        className="py-1.5"
        data-testid="live-thinking-content"
      >
        {displayText}
      </div>
    </div>
  );
}

/**
 * Live typewriter window for the in-flight execution output (shell stdout).
 * Same buffer + stick-to-bottom behaviour as LiveThinkingWindow, but monospace
 * since it renders command output. Appears only while the latest step is
 * still running; once it settles, the window folds away back to the summary
 * row ("fold only after the stream finishes").
 */
function LiveExecWindow({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const displayText = useStreamingTextBuffer({
    targetText: text,
    ...STREAMING_TYPE_PRESETS.finalAnswer,
  });

  const autoScrollingRef = useSmoothStickToBottom(
    ref,
    stickToBottomRef,
    displayText,
  );
  const windowHeight = useViewportAwareLiveWindowHeight(
    ref,
    contentRef,
    displayText,
  );

  const handleScroll = () => {
    const el = ref.current;
    if (!el || autoScrollingRef.current) return;
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  return (
    <div
      ref={ref}
      onScroll={handleScroll}
      className="mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap border-l-2 border-foreground/15 bg-transparent px-1 pl-3 font-mono text-xs leading-6 text-muted-foreground/85"
      data-height-policy="inner-content-capped-history-frozen"
      data-testid="live-exec-stream"
      style={{ height: windowHeight }}
    >
      <div ref={contentRef} className="py-1.5" data-testid="live-exec-content">
        {displayText}
      </div>
    </div>
  );
}

export function MessageGroup({
  className,
  enableClarificationActions = false,
  messages,
  isLoading = false,
  keepOpen = false,
  codeMode = false,
  suppressSubagentRows = false,
}: {
  className?: string;
  enableClarificationActions?: boolean;
  messages: Message[];
  isLoading?: boolean;
  keepOpen?: boolean;
  // Code mode auto-expands only while the turn is live. Once a turn is saved,
  // historical work logs fold behind a compact replay disclosure.
  codeMode?: boolean;
  /** A turn-level Agent cluster card already owns delegation lifecycle UI. */
  suppressSubagentRows?: boolean;
}) {
  const { t } = useI18n();
  const { receiptsByCallId } = useToolEffects();
  // Conversation detail level (Settings → Appearance → 对话细节级别):
  // low = hide thinking/tool/intermediate rows (final answer only),
  // medium (default) = collapse rows, high = expand them by default.
  const detailConfig = useConversationDetailLevel();
  // Keep the live turn focused on the current frame. Older steps move behind
  // a replay disclosure so streaming never becomes a long historical pile.
  const isLiveTimeline = isLoading || keepOpen;
  const steps = useMemo(() => convertToSteps(messages), [messages]);
  const shouldAutoCollapseProcess =
    detailConfig.autoCollapseToolCalls &&
    steps.length > AUTO_COLLAPSE_PROCESS_STEP_COUNT;
  const [processReplayOpen, setProcessReplayOpen] = useState(
    isLiveTimeline || !shouldAutoCollapseProcess,
  );
  const processWasLiveRef = useRef(isLiveTimeline);
  useEffect(() => {
    if (isLiveTimeline) {
      setProcessReplayOpen(true);
    } else if (processWasLiveRef.current) {
      setProcessReplayOpen(!shouldAutoCollapseProcess);
    }
    processWasLiveRef.current = isLiveTimeline;
  }, [isLiveTimeline, shouldAutoCollapseProcess]);
  const [expandedAggregatedGroups, setExpandedAggregatedGroups] = useState<
    Record<string, boolean>
  >({});
  // While streaming, phases default to collapsed (only the active phase expands);
  // `expandedHistoryPhases[phaseId]=true` overrides to expand a collapsed phase.
  // After streaming ends, phases default to expanded per spec §当前帧聚焦;
  // `collapsedHistoryPhases[phaseId]=true` overrides to collapse an expanded phase.
  const [expandedHistoryPhases, setExpandedHistoryPhases] = useState<
    Record<string, boolean>
  >({});
  const [collapsedHistoryPhases, setCollapsedHistoryPhases] = useState<
    Record<string, boolean>
  >({});
  // Inline expansion for thinking rows: default collapsed (summary only).
  const [expandedThinkingRows, setExpandedThinkingRows] = useState<
    Record<string, boolean>
  >({});
  const thinkingStartTimeRef = useRef<number | null>(null);
  const [thinkingElapsedMs, setThinkingElapsedMs] = useState(0);
  // Map parentItemId -> subagent identity so that child tool rows (searches,
  // reads, edits) spawned by a teammate can show the teammate's avatar.
  const subagentByParentItemId = useMemo(() => {
    const map = new Map<
      string,
      { name: string; icon?: string | null; avatarUrl?: string }
    >();
    for (const step of steps) {
      if (step.type !== "toolCall") continue;
      if (!isTeamCallToolName(step.name)) continue;
      const identity = subagentIdentityFromArgs(step.args);
      if (identity.name && step.parentItemId) {
        map.set(step.parentItemId, identity);
      }
    }
    return map;
  }, [steps]);
  const showInterruptedReceipt =
    !isLoading &&
    messages.some(
      (message) =>
        message.type === "ai" &&
        message.additional_kwargs?.response_state === "interrupted",
    );
  const clarificationContent = useMemo(
    () =>
      steps
        .map((step) =>
          step.type === "reasoning"
            ? step.reasoning
            : step.type === "actionCallback"
              ? step.actionText
              : null,
        )
        .filter((value): value is string => Boolean(value?.trim()))
        .join("\n\n"),
    [steps],
  );
  const timelineItems = useMemo(
    () => groupConsecutiveReasoningSteps(steps),
    [steps],
  );
  // A tool-carrying AI message can also contain the answer currently being
  // streamed. Keep that text in the conversation lane instead of burying it
  // inside the process replay.
  const streamingAnswer = useMemo(() => {
    if (!isLoading) return null;
    for (const msg of messages) {
      if (msg.type !== "ai" || !hasToolCalls(msg)) continue;
      if (msg.additional_kwargs?.public_progress === true) continue;
      if (isLikelyFinalAnswerContent(msg)) continue;
      const text = extractContentFromMessage(msg);
      if (text && text.trim()) return { text, messageId: msg.id };
    }
    return null;
  }, [messages, isLoading]);
  const streamingAnswerText = streamingAnswer?.text ?? null;
  // A reasoning disclosure may be opened while the model is still working.
  // Once the answer lane starts (or the turn settles), return it to the
  // compact completed state. This runs only on the live -> completed
  // transition, so a reader can still reopen completed thinking manually.
  const thinkingWasLiveRef = useRef(isLiveTimeline && !streamingAnswerText);
  useEffect(() => {
    const thinkingIsLive = isLiveTimeline && !streamingAnswerText;
    if (thinkingWasLiveRef.current && !thinkingIsLive) {
      setExpandedThinkingRows({});
    }
    thinkingWasLiveRef.current = thinkingIsLive;
  }, [isLiveTimeline, streamingAnswerText]);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  // The transcript has one disclosure model: compact events stay inline and
  // their detail opens in the right workbench. Expanding the same replay a
  // second time inside the conversation duplicated checkpoints and tool rows,
  // making one model turn read like several competing logs.
  // Keep process events on the same chronological lane as the answer while
  // letting the answer retain visual priority. The main transcript shows only
  // compact public summaries; complete event payloads live in the workbench.
  const compactTimelineItems = useMemo(
    () => buildCompactTimelineItems(timelineItems, receiptsByCallId),
    [timelineItems, receiptsByCallId],
  );
  const compactExecutionCoverage = useMemo(
    () => executionCoverageByVisibleItem(timelineItems, compactTimelineItems),
    [timelineItems, compactTimelineItems],
  );
  // 侧边栏 → 对话区联动：命中被折叠进聚合组的子项时，对话区只有聚合行带
  // data-timeline-item-id，子项不在 DOM 中无法定位高亮。这里订阅共享
  // linkage store：高亮 id 命中聚合组本身或其任一子项时自动展开该组，
  // 子项渲染后带自己的定位属性，message-list 的滚动/高亮逻辑随后接管。
  // 聚合组若被收进历史阶段折叠条，同时展开该阶段保证子项真实渲染。
  const timelineLinkage = useSyncExternalStore(
    subscribeTimelineLinkage,
    getTimelineLinkageState,
    getTimelineLinkageState,
  );
  useEffect(() => {
    const highlightedId = timelineLinkage.highlightedTimelineItemId;
    if (!highlightedId || timelineLinkage.activeSource !== "sidebar") return;
    const highlightedItem = compactTimelineItems.find(
      (item) =>
        timelineItemLinkageId(item) === highlightedId ||
        (item.type === "aggregatedToolGroup" &&
          item.items.some(
            (child) => timelineItemLinkageId(child) === highlightedId,
          )),
    );
    if (highlightedItem) setProcessReplayOpen(true);
    for (const item of compactTimelineItems) {
      if (item.type !== "aggregatedToolGroup") continue;
      const hitsGroup = timelineItemLinkageId(item) === highlightedId;
      const hitsChild = item.items.some(
        (child) => timelineItemLinkageId(child) === highlightedId,
      );
      if (!hitsGroup && !hitsChild) continue;
      setExpandedAggregatedGroups((current) =>
        current[item.id] ? current : { ...current, [item.id]: true },
      );
      const phaseId = lastTimelineStep(item).phaseId;
      if (phaseId) {
        setExpandedHistoryPhases((current) =>
          current[phaseId] ? current : { ...current, [phaseId]: true },
        );
      }
      break;
    }
  }, [
    timelineLinkage.highlightedTimelineItemId,
    timelineLinkage.nonce,
    timelineLinkage.activeSource,
    compactTimelineItems,
  ]);
  // The answer body belongs at the point in the timeline where it was
  // produced, so a turn reads chronologically (thought → action → answer →
  // further action) instead of pinning every process row above a trailing
  // answer lane. Position comes from the answer message's own order in the
  // turn, matched against each timeline item's source message.
  const answerSplitIndex = useMemo(() => {
    if (!streamingAnswer) return -1;
    const messageOrder = new Map<string, number>();
    messages.forEach((message, index) => {
      if (message.id) messageOrder.set(message.id, index);
    });
    const answerOrder = streamingAnswer.messageId
      ? messageOrder.get(streamingAnswer.messageId)
      : undefined;
    if (answerOrder === undefined) return -1;
    const splitAt = compactTimelineItems.findIndex((item) => {
      const order = messageOrder.get(timelineItemMessageId(item) ?? "");
      if (order === undefined) return false;
      if (order > answerOrder) return true;
      // A tool call carried by the answer message itself starts after its
      // text streamed, so it belongs below the answer.
      return order === answerOrder && isExecutionTimelineItem(item);
    });
    return splitAt;
  }, [streamingAnswer, messages, compactTimelineItems]);
  const compactItemsBeforeAnswer =
    answerSplitIndex >= 0
      ? compactTimelineItems.slice(0, answerSplitIndex)
      : compactTimelineItems;
  const compactItemsAfterAnswer =
    answerSplitIndex >= 0 ? compactTimelineItems.slice(answerSplitIndex) : [];
  const hasVisibleProcessReplay =
    detailConfig.level !== "low" && compactTimelineItems.length > 0;
  const processReplayExpanded = isLiveTimeline || processReplayOpen;
  const showProcessReplayDisclosure =
    !isLiveTimeline && shouldAutoCollapseProcess && hasVisibleProcessReplay;
  // 最终回答视觉分层：流式结束后，在过程段落与最终回答正文之间加分界。
  // 判定口径与 groupMessages 一致（tool_calls + isLikelyFinalAnswerContent 的
  // 消息会以独立 assistant 组在下方渲染正文），且必须是同组最后一条可见正文，
  // 避免给中途的过程组或 checkpoint 收尾的组误加分界。
  const showFinalAnswerBoundary =
    !isLiveTimeline &&
    compactTimelineItems.length > 0 &&
    messages.some(
      (message) =>
        isAnswerContent(message, messages) &&
        hasToolCalls(message) &&
        isLikelyFinalAnswerContent(message),
    );

  const lastCompactItem = compactTimelineItems[compactTimelineItems.length - 1];
  const isCurrentlyThinking = useMemo(() => {
    if (!isLoading || !isLiveTimeline) return false;
    if (!lastCompactItem) return false;
    return (
      lastCompactItem.type === "reasoningGroup" ||
      lastCompactItem.type === "commentary"
    );
  }, [isLoading, isLiveTimeline, lastCompactItem]);

  // Extract the backend-provided reasoning start timestamp so the live
  // timer measures from the true first-token arrival time, not the React
  // render frame that first noticed thinking was in progress.
  const reasoningStartedAt = useMemo(() => {
    if (!isCurrentlyThinking) return null;
    for (const message of messages) {
      if (message.type !== "ai") continue;
      const ts = message.additional_kwargs?.reasoning_started_at;
      if (typeof ts === "string" && ts) {
        const parsed = Date.parse(ts);
        if (!Number.isNaN(parsed)) return parsed;
      }
    }
    return null;
  }, [isCurrentlyThinking, messages]);

  useEffect(() => {
    if (isCurrentlyThinking) {
      if (thinkingStartTimeRef.current === null) {
        // Prefer the backend's first-token timestamp; fall back to now()
        // for legacy streams that don't carry reasoning_started_at.
        thinkingStartTimeRef.current = reasoningStartedAt ?? Date.now();
        setThinkingElapsedMs(0);
      }
      const intervalId = setInterval(() => {
        if (thinkingStartTimeRef.current !== null) {
          setThinkingElapsedMs(Date.now() - thinkingStartTimeRef.current);
        }
      }, 1000);
      return () => clearInterval(intervalId);
    } else {
      thinkingStartTimeRef.current = null;
      setThinkingElapsedMs(0);
    }
  }, [isCurrentlyThinking, reasoningStartedAt]);

  if (steps.length === 0) {
    return null;
  }

  function renderCompactTimelineItems(
    items: TimelineItem[],
    keyPrefix: string,
    options?: { nested?: boolean },
  ) {
    // 嵌套渲染（聚合组展开的子项）跳过历史阶段二次折叠，保证子项全部可见
    const nested = options?.nested ?? false;
    // A live agent often emits several records for one phase. Leaving every
    // completed record open turns the transcript into a terminal log, so keep
    // only the active phase in full view while streaming. Once streaming ends
    // (isLiveTimeline=false), all phases auto-expand per spec §当前帧聚焦 —
    // the user can then freely collapse any phase; collapsed state persists.
    const activeTimelineItem =
      compactTimelineItems[compactTimelineItems.length - 1] ??
      items[items.length - 1];
    const activePhaseId = activeTimelineItem
      ? lastTimelineStep(activeTimelineItem).phaseId
      : undefined;
    const historicalPhaseItems = new Map<string, TimelineItem[]>();
    if (!nested) {
      for (const timelineItem of items) {
        const phaseId = lastTimelineStep(timelineItem).phaseId;
        if (!phaseId || phaseId === activePhaseId) continue;
        // While streaming: default collapsed, expandedHistoryPhases overrides.
        // After streaming: default expanded, collapsedHistoryPhases overrides.
        const isCollapsed = isLiveTimeline
          ? !expandedHistoryPhases[phaseId]
          : collapsedHistoryPhases[phaseId] === true;
        if (!isCollapsed) continue;
        const group = historicalPhaseItems.get(phaseId) ?? [];
        group.push(timelineItem);
        historicalPhaseItems.set(phaseId, group);
      }
    }

    const delegationSummaryById = buildDelegationSummary(items);

    return items.map((item) => {
      // Conversation detail level "low": hide intermediate activity rows
      // (thinking / tool execution / process narration) so the transcript
      // reads like a plain chat with only the final answers.
      if (
        (!detailConfig.showThinkingProcess && item.type === "reasoningGroup") ||
        (!detailConfig.showToolCalls &&
          (item.type === "toolCall" ||
            item.type === "aggregatedToolGroup" ||
            item.type === "actionCallbackGroup")) ||
        (!detailConfig.showIntermediateSteps && item.type === "commentary")
      ) {
        return null;
      }
      const step = lastTimelineStep(item);
      const delegationSummary = delegationSummaryById.get(item.id);
      if (delegationSummary && delegationSummary.firstId !== item.id) {
        return null;
      }
      const phaseItems = step.phaseId
        ? historicalPhaseItems.get(step.phaseId)
        : undefined;
      // Public commentary is conversation and must never be replaced by a
      // faded phase receipt. Collapse only reasoning/tool activity that shares
      // the phase; the commentary remains full-strength above that receipt.
      const collapsiblePhaseItems = phaseItems?.filter(
        (phaseItem) => phaseItem.type !== "commentary",
      );
      if (
        item.type !== "commentary" &&
        collapsiblePhaseItems &&
        collapsiblePhaseItems.length > 1
      ) {
        if (collapsiblePhaseItems[0] !== item) return null;
        const completedSummary = summarizeCollapsedPhase(
          collapsiblePhaseItems,
          t,
        );
        const collapsedPhaseId = step.phaseId;
        return (
          <button
            key={`${keyPrefix}-phase-${collapsedPhaseId}`}
            type="button"
            className="flex min-w-0 items-center gap-1.5 py-0.5 text-left text-xs leading-[18px] text-muted-foreground/45 transition-colors hover:text-muted-foreground"
            onClick={() => {
              if (!collapsedPhaseId) return;
              if (isLiveTimeline) {
                // Streaming: expand the collapsed phase.
                setExpandedHistoryPhases((prev) => ({
                  ...prev,
                  [collapsedPhaseId]: true,
                }));
              } else {
                // Post-stream: this button only shows for collapsed phases,
                // so clicking expands (clears the collapsed override).
                setCollapsedHistoryPhases((prev) => {
                  const next = { ...prev };
                  delete next[collapsedPhaseId];
                  return next;
                });
              }
            }}
            data-testid="collapsed-history-phase"
            data-phase-id={collapsedPhaseId}
          >
            <span className="size-1 shrink-0 rounded-full bg-success/70" />
            <span className="truncate">{completedSummary}</span>
            <ChevronDownIcon className="size-3 shrink-0 opacity-60" />
          </button>
        );
      }
      const isLastOverall =
        item === compactTimelineItems[compactTimelineItems.length - 1];
      const state = runStateForCurrentStep(
        step,
        isLiveTimeline && isLastOverall && isLoading,
      );
      if (item.type === "commentary") {
        const commentaryText = publicProcessText(item.step.commentary);
        const commentarySummary = publicProcessText(
          summarizeCurrentStep(item.step, t),
        );
        return (
          <div
            key={`${keyPrefix}-${item.id}`}
            className="flex min-w-0 items-start gap-1"
          >
            <div
              role="button"
              tabIndex={0}
              aria-label={commentarySummary}
              onClick={() => {
                activateTimelineItem(timelineItemLinkageId(item), "chat");
                emitOpenAgentWorkbench({
                  tab: "agent",
                  eventId: item.step.messageId ?? item.step.id,
                  eventKind: "thinking",
                  view: "summary",
                  processEvent: {
                    kind: "thinking",
                    summary: commentarySummary,
                    detail: commentaryText || commentarySummary,
                    status: state,
                    count: 1,
                    phaseId: item.step.phaseId,
                    parentItemId: item.step.parentItemId,
                    timelineSequence: item.step.timelineSequence,
                  },
                });
              }}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                event.currentTarget.click();
              }}
              className="group/progress-row narrative-progress-row my-2.5 flex min-w-0 flex-1 cursor-pointer items-start text-foreground outline-none transition-colors hover:text-foreground focus-visible:text-foreground"
              data-testid="public-progress-event"
              data-process-event-id={item.step.messageId ?? item.step.id}
              data-timeline-item-id={timelineItemLinkageId(item)}
              data-timeline-lane="chat"
              data-process-event-kind="thinking"
              data-process-event-status={state}
              data-phase-id={item.step.phaseId}
              data-parent-item-id={item.step.parentItemId}
              data-progress-sequence={item.step.progressSequence}
              data-timeline-sequence={item.step.timelineSequence}
            >
              <div className="min-w-0 flex-1">
                <MarkdownContent
                  content={commentaryText}
                  isLoading={isLiveTimeline && isLastOverall && isLoading}
                  rehypePlugins={rehypePlugins}
                  className={cn(
                    "narrative-progress-copy",
                    isLiveTimeline &&
                      isLastOverall &&
                      isLoading &&
                      "kimi-streaming-tail",
                  )}
                />
                {item.step.groundingMessage && (
                  <GroundingChip message={item.step.groundingMessage} />
                )}
              </div>
            </div>
          </div>
        );
      }
      const isThinking = item.type === "reasoningGroup";
      // Latest reasoning step's full text — streams token-by-token as
      // messages update, so the live window below renders typewriter-style.
      const liveThinkingText =
        item.type === "reasoningGroup"
          ? (item.steps[item.steps.length - 1]?.reasoning ?? "").trim()
          : "";
      // While the latest thought is still streaming, the live window below
      // carries the full text; hide the row's truncated summary so the two
      // don't duplicate. Public narration uses a separate commentary item.
      const liveThinkingStreamActive =
        isThinking &&
        isLastOverall &&
        isCurrentlyThinking &&
        Boolean(liveThinkingText);
      // Latest shell execution's live stdout (realtime-adapter mirrors
      // commandExecution.aggregatedOutput into tool_call.args.output).
      const liveExecOutput =
        item.type === "toolCall" && typeof item.step.args.output === "string"
          ? item.step.args.output.trim()
          : "";
      // Show the typewriter output window only while the latest step is
      // actually running; once it settles the window folds back to the row.
      const liveExecStreamActive =
        Boolean(liveExecOutput) &&
        isLastOverall &&
        isLiveTimeline &&
        isLoading &&
        state === "running";
      const isAggregatedGroup = item.type === "aggregatedToolGroup";
      const aggregatedExpanded =
        isAggregatedGroup &&
        (expandedAggregatedGroups[item.id] ??
          (detailConfig.level === "high" && item.count <= 8));
      const coveredItems =
        compactExecutionCoverage.get(item.id) ?? ([item] as TimelineItem[]);
      const groupedTargetSummary =
        summarizeCompactExecutionTargets(coveredItems) ??
        (isAggregatedGroup
          ? summarizeCompactExecutionTargets(item.items)
          : null);
      const concreteTargetSummary =
        item.type === "toolCall" ? compactToolTarget(item.step) : null;
      const count = isAggregatedGroup
        ? item.count
        : coveredItems.reduce(
            (total, coveredItem) =>
              total +
              (coveredItem.type === "toolCall" ||
              coveredItem.type === "commentary"
                ? 1
                : coveredItem.type === "aggregatedToolGroup"
                  ? coveredItem.count
                  : coveredItem.steps.length),
            0,
          );
      // Stored thinking duration from the backend (reasoning_duration_ms).
      // Only present for completed reasoning items; undefined for legacy
      // data — in that case hasStoredDuration stays false and the badge
      // is suppressed on replay so we never fabricate a number.
      const groupDurationMs = isThinking
        ? coveredItems.reduce<number>((total, coveredItem) => {
            if (coveredItem.type !== "reasoningGroup") return total;
            return (
              total +
              coveredItem.steps.reduce<number>(
                (sum, step) =>
                  sum +
                  (typeof step.durationMs === "number" ? step.durationMs : 0),
                0,
              )
            );
          }, 0)
        : 0;
      const hasStoredDuration = isThinking
        ? coveredItems.some(
            (coveredItem) =>
              coveredItem.type === "reasoningGroup" &&
              coveredItem.steps.some(
                (step) => typeof step.durationMs === "number",
              ),
          )
        : false;
      // Deep-thinking heuristic per spec §思考块加耗时: distinguish deep vs
      // normal thinking via duration, step count, and total content length.
      // Deep thinking shows a SparklesIcon; normal shows a BrainIcon.
      // Optimized thresholds to avoid false positives:
      // - Duration ≥15s AND steps ≥5 (sustained deep reasoning)
      // - OR steps ≥15 (many reasoning steps)
      // - OR total content ≥3000 chars (substantial reasoning chain)
      const totalSteps = isThinking
        ? coveredItems.reduce(
            (sum, item) =>
              sum + (item.type === "reasoningGroup" ? item.steps.length : 0),
            0,
          )
        : 0;
      const totalContentLength = isThinking
        ? coveredItems.reduce(
            (sum, item) =>
              sum +
              (item.type === "reasoningGroup"
                ? item.steps.reduce(
                    (stepSum, step) => stepSum + (step.reasoning?.length ?? 0),
                    0,
                  )
                : 0),
            0,
          )
        : 0;
      const isDeepThinking = isThinking
        ? groupDurationMs >= 10_000 || // a single long native thinking block
          (groupDurationMs >= 15_000 && totalSteps >= 5) ||
          totalSteps >= 15 ||
          totalContentLength >= 3000
        : false;
      // Use action-display for human-readable verb + icon
      let actionVerb: string;
      let actionObject: string;
      let ActionIcon: React.ComponentType<{ className?: string }>;
      let factSummaryText: string | null = null;
      let actionWorkbenchTab:
        | "agent"
        | "terminal"
        | "browser"
        | "diff"
        | "artifacts" = "agent";

      if (isAggregatedGroup) {
        actionVerb = localizedAggregateVerb(item.aggregateKind, item.count, t);
        actionObject = "";
        ActionIcon = getActionIcon(aggregateIconName(item.aggregateKind));
        factSummaryText = null;
        switch (item.aggregateKind) {
          case "file_write":
            actionWorkbenchTab = "diff";
            break;
          case "command":
            actionWorkbenchTab = "terminal";
            break;
          case "web_search":
          case "browser":
            actionWorkbenchTab = "browser";
            break;
          default:
            actionWorkbenchTab = "agent";
        }
      } else if (item.type === "toolCall") {
        const display = getActionDisplay(item.step.name, item.step.args);
        const narrative = projectToolNarrative({
          id: item.step.id ?? item.id,
          toolName: item.step.name,
          args: item.step.args,
          result: item.step.result,
          phaseId: item.step.phaseId,
          state,
        });
        actionVerb = localizedActionVerb(display, t);
        actionObject = narrative.object ?? "";
        ActionIcon = getActionIcon(display.iconName);
        actionWorkbenchTab =
          narrative.evidenceRefs[0]?.tab ?? display.workbenchTab;
        // 事实摘要：仅当该行单独代表一个工具调用且结果可提取时附加
        factSummaryText =
          coveredItems.length === 1
            ? formatFactSummary(
                narrative.fact ??
                  extractFactSummary(item.step.name, item.step.result),
                t,
              )
            : null;
      } else if (item.type === "actionCallbackGroup") {
        actionVerb = summarizeActionGroup(item, t);
        actionObject = "";
        ActionIcon = WrenchIcon;
        factSummaryText = null;
        actionWorkbenchTab = "agent";
      } else {
        // reasoningGroup: actionVerb not used in isThinking display path
        actionVerb = "";
        actionObject = "";
        ActionIcon = isDeepThinking ? SparklesIcon : BrainIcon;
        factSummaryText = null;
        actionWorkbenchTab = "agent";
      }

      const isSubagentRow =
        !isThinking &&
        ((item.type === "toolCall" && isTeamCallToolName(item.step.name)) ||
          (isAggregatedGroup && item.aggregateKind === "teammate"));

      const subagentIdentity = isSubagentRow
        ? subagentIdentityFromArgs(
            isAggregatedGroup
              ? (item.items[0]?.step.args ?? {})
              : item.type === "toolCall"
                ? item.step.args
                : {},
          )
        : null;
      const owningSubagent = (() => {
        if (isSubagentRow) return null;
        const parentIds = isAggregatedGroup
          ? item.items
              .map((child) => child.step.parentItemId)
              .filter((id): id is string => Boolean(id))
          : item.type === "toolCall"
            ? item.step.parentItemId
              ? [item.step.parentItemId]
              : []
            : [];
        if (parentIds.length === 0) return null;
        const first = parentIds[0]!;
        if (parentIds.every((id) => id === first)) {
          return subagentByParentItemId.get(first) ?? null;
        }
        return null;
      })();

      // Hide both subagent tool calls AND their child operations
      if (suppressSubagentRows && (isSubagentRow || owningSubagent))
        return null;

      const summary =
        item.type === "reasoningGroup"
          ? isDeepThinking
            ? // Deep thinking: show "深度思考 · 思考了 X · N 条"
              hasStoredDuration
              ? `${t.messageGrouping.deepThinking ?? "深度思考"} · ${t.messageGrouping.thoughtFor ?? "思考了"} ${formatThinkingDuration(groupDurationMs)} · ${count} 条`
              : `${t.messageGrouping.deepThinking ?? "深度思考"} · ${count} 条`
            : summarizeReasoningGroup(item, t)
          : item.type === "actionCallbackGroup"
            ? summarizeActionGroup(item, t)
            : isAggregatedGroup
              ? [actionVerb, groupedTargetSummary].filter(Boolean).join(" · ")
              : (groupedTargetSummary ??
                concreteTargetSummary ??
                (actionObject ? `${actionVerb} ${actionObject}` : actionVerb));
      const workbenchEventId = isAggregatedGroup
        ? item.items[item.items.length - 1]?.step.id
        : item.type === "toolCall"
          ? item.step.id
          : step.messageId;
      // 外部副作用待复核凭据。聚合组也必须解析:叙事化时间线会把工具调用
      // 聚合成一行,若只在非聚合分支取凭据,indeterminate 的写文件/网络动作
      // 就再也不会亮出待复核标记 —— 这是安全可见性,不能被聚合吞掉。
      const resolveReceipt = (toolStep: CoTToolCallStep) =>
        toolStep.effectReceipt ??
        (toolStep.id ? receiptsByCallId.get(toolStep.id) : undefined);
      const effectReceipt = isAggregatedGroup
        ? (item.items
            .map((toolItem) => resolveReceipt(toolItem.step))
            .find((receipt) => receipt?.state === "indeterminate") ?? undefined)
        : item.type === "toolCall"
          ? (item.step.effectReceipt ??
            (workbenchEventId
              ? receiptsByCallId.get(workbenchEventId)
              : undefined))
          : undefined;
      const needsEffectReview = effectReceipt?.state === "indeterminate";
      // 待复核时工作台要落到出问题的那一次调用,而不是聚合组的代表事件,
      // 否则用户点开徽标看到的是同组里某个无关的只读调用。
      const effectReceiptCallId = effectReceipt
        ? "call_id" in effectReceipt
          ? effectReceipt.call_id
          : effectReceipt.callId
        : undefined;
      const workbenchOpenEventId =
        (needsEffectReview ? effectReceiptCallId : undefined) ??
        workbenchEventId;
      const processEventDetail = dedupeTimelineChunks(
        isAggregatedGroup
          ? item.items.map((toolItem) =>
              publicProcessText(summarizeCurrentStep(toolItem.step, t)),
            )
          : coveredItems.flatMap((coveredItem) =>
              coveredItem.type === "reasoningGroup"
                ? coveredItem.steps
                    .map((reasoningStep) =>
                      publicProcessText(reasoningStep.reasoning ?? ""),
                    )
                    .filter(Boolean)
                : coveredItem.type === "actionCallbackGroup"
                  ? coveredItem.steps
                      .map((actionStep) =>
                        publicProcessText(actionStep.actionText),
                      )
                      .filter(Boolean)
                  : coveredItem.type === "aggregatedToolGroup"
                    ? coveredItem.items.map((toolItem) =>
                        publicProcessText(
                          summarizeCurrentStep(toolItem.step, t),
                        ),
                      )
                    : coveredItem.type === "toolCall"
                      ? [
                          publicProcessText(
                            summarizeCurrentStep(coveredItem.step, t),
                          ),
                        ]
                      : [publicProcessText(coveredItem.step.commentary)],
            ),
      ).join("\n");
      const processEventSummary = publicProcessText(summary);
      // Wording-based internal reasoning (no structured phase, no native
      // duration, no public checkpoint counterpart) is self-talk: mute it to
      // a "思考过程" label with the content behind the disclosure toggle,
      // instead of presenting the raw notes as public progress. Trace-based
      // and code-mode reasoning keep their compact content summary.
      const isWordingInternal =
        isThinking &&
        !codeMode &&
        !isDeepThinking &&
        !isLiveTimeline &&
        !hasStoredDuration &&
        // A single message's internal self-talk (all chunks share one
        // message id) is muted; a chain of distinct reasoning messages or
        // structured reasoning keeps its content summary.
        new Set(item.steps.map((step) => step.messageId).filter(Boolean))
          .size === 1 &&
        item.steps.every((step) => !step.phaseId) &&
        !timelineItems.some((tl) => tl.type === "commentary");
      const thinkingDisclosureLabel = isDeepThinking
        ? t.messageGrouping.deepThinking
        : isWordingInternal
          ? (t.messageGrouping.thinkingProcess ?? "思考过程")
          : processEventSummary || summary;
      const hasThinkingDetail =
        isThinking &&
        !isLiveTimeline &&
        Boolean(processEventDetail.trim()) &&
        processEventDetail.trim() !== thinkingDisclosureLabel.trim();

      const rowKey = isAggregatedGroup
        ? `${keyPrefix}-agg-${item.items[0]?.id ?? item.aggregateKind}`
        : `${keyPrefix}-${item.id}`;
      return (
        <div key={rowKey} className="min-w-0">
          <div className="group/process-row flex min-w-0 items-center gap-0.5">
            <button
              type="button"
              onClick={() => {
                // Always activate timeline and open workbench for full context
                activateTimelineItem(timelineItemLinkageId(item), "chat");
                emitOpenAgentWorkbench({
                  tab: actionWorkbenchTab,
                  eventId: workbenchOpenEventId,
                  eventKind: isThinking ? "thinking" : "execution",
                  view: isThinking ? "summary" : "trace",
                  processEvent: {
                    kind: isThinking ? "thinking" : "execution",
                    summary: processEventSummary,
                    detail: processEventDetail || processEventSummary,
                    status: state,
                    count,
                    phaseId: step.phaseId,
                    parentItemId: step.parentItemId,
                    timelineSequence: step.timelineSequence,
                  },
                  effectKey: needsEffectReview
                    ? "effect_key" in effectReceipt
                      ? effectReceipt.effect_key
                      : effectReceipt.effectKey
                    : undefined,
                });
              }}
              className={cn(
                "flex min-w-0 flex-1 text-left transition-colors",
                isThinking
                  ? "my-1 min-h-7 items-center gap-1.5 py-1 text-[13px] leading-5"
                  : "my-2 items-center gap-2 py-0.5 text-sm leading-5",
                // Aggregated rows use a slightly larger size and stronger
                // hover target per spec §Design/Style.
                needsEffectReview
                  ? "text-warning/80 hover:text-warning/80 dark:hover:text-warning"
                  : isAggregatedGroup
                    ? "text-muted-foreground hover:text-foreground"
                    : "text-muted-foreground/75 hover:text-muted-foreground",
              )}
              data-process-event-id={workbenchEventId}
              data-timeline-item-id={timelineItemLinkageId(item)}
              data-timeline-lane="chat"
              data-process-event-kind={isThinking ? "thinking" : "execution"}
              data-process-event-status={state}
              data-effect-receipt-state={effectReceipt?.state}
              data-phase-id={step.phaseId}
              data-parent-item-id={step.parentItemId}
              data-timeline-sequence={step.timelineSequence}
              data-testid={`process-timeline-event-${isThinking ? "thinking" : "execution"}`}
            >
              {isThinking ? (
                <span className="flex shrink-0 items-center gap-1">
                  {isDeepThinking ? (
                    <SparklesIcon
                      className="size-3 text-muted-foreground"
                      aria-hidden="true"
                    />
                  ) : (
                    <BrainIcon
                      className="size-3 text-muted-foreground"
                      aria-hidden="true"
                    />
                  )}
                  <span className="relative flex size-1.5 shrink-0 items-center justify-center">
                    <span
                      className={cn(
                        "absolute inline-flex size-1.5 rounded-full opacity-25",
                        needsEffectReview
                          ? "bg-warning"
                          : agentRunStatusLightClass(state),
                        needsEffectReview
                          ? "animate-pulse"
                          : agentRunStatusLightPulseClass(state),
                      )}
                    />
                    <span
                      className={cn(
                        "relative inline-flex size-1 rounded-full",
                        needsEffectReview
                          ? "bg-warning"
                          : agentRunStatusLightClass(state),
                      )}
                    />
                  </span>
                </span>
              ) : isSubagentRow && subagentIdentity?.name ? (
                <AgentAvatar
                  agentDisplayName={subagentIdentity.name}
                  icon={subagentIdentity.icon}
                  avatarUrl={subagentIdentity.avatarUrl}
                  className="size-4 shrink-0 rounded-sm text-micro"
                />
              ) : owningSubagent?.name ? (
                <AgentAvatar
                  agentDisplayName={owningSubagent.name}
                  icon={owningSubagent.icon}
                  avatarUrl={owningSubagent.avatarUrl}
                  className="size-4 shrink-0 rounded-sm text-micro"
                />
              ) : (
                <ActionIcon className="size-4 shrink-0 text-muted-foreground/75" />
              )}
              <span className="flex min-w-0 flex-1 items-center gap-1">
                <span className="flex items-center gap-1">
                  {isThinking ? (
                    // Long, completed private reasoning gets a stable
                    // disclosure label so it cannot be mistaken for public
                    // progress or a formal answer. Short/legacy traces keep
                    // their compact summary for backwards compatibility.
                    liveThinkingStreamActive ? null : (
                      thinkingDisclosureLabel
                    )
                  ) : actionObject ? (
                    <>
                      <span className="font-medium text-muted-foreground/90">
                        {actionVerb}
                      </span>
                      <span className="ml-1.5 text-muted-foreground/70">
                        {" "}
                        {actionObject}
                      </span>
                    </>
                  ) : isAggregatedGroup && isLiveTimeline ? (
                    <FlipDisplay uniqueKey={item.id} className="min-w-0">
                      <span className="block truncate">
                        {processEventSummary || summary}
                      </span>
                    </FlipDisplay>
                  ) : (
                    <span>{processEventSummary || summary}</span>
                  )}
                </span>
                {isThinking &&
                  isLastOverall &&
                  isCurrentlyThinking &&
                  thinkingElapsedMs > 200 && (
                    <span className="shrink-0 tabular-nums text-micro text-muted-foreground/40">
                      {t.messageGrouping.thinkingDuration(
                        formatDuration(thinkingElapsedMs),
                      )}
                    </span>
                  )}
                {isThinking &&
                  hasStoredDuration &&
                  groupDurationMs > 0 &&
                  !(isLastOverall && isCurrentlyThinking) && (
                    <span className="shrink-0 tabular-nums text-micro text-muted-foreground/40">
                      {t.messageGrouping.thinkingDuration(
                        formatDuration(groupDurationMs),
                      )}
                    </span>
                  )}
                {(count > 1 || (delegationSummary?.count ?? 0) > 1) &&
                  !isAggregatedGroup &&
                  !groupedTargetSummary && (
                    <span className="shrink-0 tabular-nums whitespace-nowrap text-mini text-muted-foreground/50">
                      {t.messageGrouping.countItems(
                        delegationSummary?.count ?? count,
                      )}
                    </span>
                  )}
                {needsEffectReview && (
                  <span
                    className="shrink-0 rounded-full bg-warning/10 px-1.5 text-xs font-medium text-warning"
                    data-testid="tool-effect-review-badge"
                  >
                    {t.messageGrouping.effectNeedsReview}
                  </span>
                )}
              </span>
              {isLastOverall && isLiveTimeline && codeMode && (
                <span className="sr-only" data-testid="live-process-strip" />
              )}
            </button>
            {hasThinkingDetail && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setExpandedThinkingRows((current) => ({
                    ...current,
                    [item.id]: !current[item.id],
                  }));
                }}
                className="shrink-0 p-0.5 text-muted-foreground/55 opacity-0 transition-opacity group-hover/process-row:opacity-100 hover:text-muted-foreground focus-visible:opacity-100"
                aria-label={
                  expandedThinkingRows[item.id]
                    ? t.agentWorkbenchPages.collapse
                    : t.agentWorkbenchPages.expandDetails
                }
                title={
                  expandedThinkingRows[item.id]
                    ? t.agentWorkbenchPages.collapse
                    : t.agentWorkbenchPages.expandDetails
                }
                data-testid="thinking-row-toggle"
              >
                <ChevronRightIcon
                  className={cn(
                    "size-3 transition-transform",
                    expandedThinkingRows[item.id] ? "rotate-90" : "",
                  )}
                />
              </button>
            )}
            {isAggregatedGroup && (
              <button
                type="button"
                onClick={() =>
                  setExpandedAggregatedGroups((current) => ({
                    ...current,
                    [item.id]: !aggregatedExpanded,
                  }))
                }
                className="p-0.5 opacity-0 transition-opacity group-hover/process-row:opacity-100 hover:text-muted-foreground"
                aria-label={
                  aggregatedExpanded
                    ? t.agentWorkbenchPages.collapse
                    : t.agentWorkbenchPages.expandDetails
                }
                title={
                  aggregatedExpanded
                    ? t.agentWorkbenchPages.collapse
                    : t.agentWorkbenchPages.expandDetails
                }
                data-testid="aggregated-group-toggle"
              >
                <ChevronDownIcon
                  className={cn(
                    "size-3 transition-transform",
                    aggregatedExpanded ? "rotate-180" : "",
                  )}
                />
              </button>
            )}
          </div>
          {/* Live thinking window: while the latest step is still
              streaming, show its full text typewriter-style in a fixed-
              height window (auto-anchored to the newest text). Once the
              stream settles the window folds away (150ms height collapse)
              and the row returns to its summary — "fold only after the
              stream finishes", with a transition instead of a hard cut.
              The Collapsible stays mounted so the close animation plays;
              only open toggles with the stream state. */}
          {isThinking && (
            <Collapsible open={liveThinkingStreamActive}>
              <CollapsibleContent
                className="overflow-hidden data-[state=open]:animate-[collapsible-down_150ms_ease-out] data-[state=closed]:animate-[collapsible-up_150ms_ease-out]"
                data-testid="live-thinking-window"
              >
                <LiveThinkingWindow text={liveThinkingText} />
              </CollapsibleContent>
            </Collapsible>
          )}
          {item.type === "toolCall" && (
            <Collapsible open={liveExecStreamActive}>
              <CollapsibleContent
                className="overflow-hidden data-[state=open]:animate-[collapsible-down_150ms_ease-out] data-[state=closed]:animate-[collapsible-up_150ms_ease-out]"
                data-testid="live-exec-window"
              >
                <LiveExecWindow text={liveExecOutput} />
              </CollapsibleContent>
            </Collapsible>
          )}
          {hasThinkingDetail && (
            <Collapsible open={expandedThinkingRows[item.id] ?? false}>
              <CollapsibleContent
                className="overflow-hidden data-[state=open]:animate-[collapsible-down_150ms_ease-out] data-[state=closed]:animate-[collapsible-up_150ms_ease-out]"
                data-testid="thinking-row-content"
              >
                <div className="ml-4 whitespace-pre-wrap border-l-2 border-border/50 py-1.5 pl-3 text-[13px] leading-6 text-muted-foreground">
                  <MarkdownContent
                    content={processEventDetail}
                    isLoading={false}
                    rehypePlugins={rehypePlugins}
                  />
                </div>
              </CollapsibleContent>
            </Collapsible>
          )}
          {factSummaryText && (
            <div className="truncate pb-0.5 pl-3 text-xs leading-[18px] text-muted-foreground/60">
              {factSummaryText}
            </div>
          )}
          {isAggregatedGroup && (
            <Collapsible open={aggregatedExpanded}>
              <CollapsibleContent
                className="ml-2 space-y-0.5 border-l border-border/40 pl-2 data-[state=open]:animate-[collapsible-down_150ms_ease-out] data-[state=closed]:animate-[collapsible-up_150ms_ease-out]"
                data-testid="aggregated-group-children"
              >
                {renderCompactTimelineItems(
                  item.items,
                  `${keyPrefix}-${item.id}-sub`,
                  { nested: true },
                )}
              </CollapsibleContent>
            </Collapsible>
          )}
        </div>
      );
    });
  }

  return (
    <ChainOfThought
      className={cn("w-full gap-0", className)}
      open={processReplayExpanded}
      data-process-mode={codeMode ? "code" : "chat"}
    >
      {showProcessReplayDisclosure && (
        <button
          type="button"
          className="group/process-replay flex min-w-0 items-center gap-1.5 py-0.5 text-left text-xs leading-5 text-muted-foreground/65 transition-colors hover:text-muted-foreground"
          aria-expanded={processReplayExpanded}
          data-testid="process-replay-toggle"
          onClick={() => setProcessReplayOpen((open) => !open)}
        >
          <ChevronRightIcon
            className={cn(
              "size-3 shrink-0 transition-transform",
              processReplayExpanded && "rotate-90",
            )}
          />
          <span>
            {processReplayExpanded
              ? t.messageGrouping.hideProcessReplay
              : t.messageGrouping.replayNSteps(steps.length)}
          </span>
        </button>
      )}
      {processReplayExpanded && (
        <div data-testid="process-replay-content">
          {compactItemsBeforeAnswer.length > 0 && (
            <div
              className="narrative-process-flow"
              data-testid="interleaved-process-timeline"
            >
              {renderCompactTimelineItems(compactItemsBeforeAnswer, "before")}
            </div>
          )}
          {streamingAnswerText && (
            <MarkdownContent
              content={streamingAnswerText}
              isLoading={isLoading}
              rehypePlugins={rehypePlugins}
              className="kimi-streaming-tail"
            />
          )}
          {compactItemsAfterAnswer.length > 0 && (
            <div
              className="narrative-process-flow mt-1"
              data-testid="interleaved-process-timeline"
            >
              {renderCompactTimelineItems(compactItemsAfterAnswer, "after")}
            </div>
          )}
          {showFinalAnswerBoundary && (
            // Keep the transition conversational: whitespace separates the live
            // process from the answer without turning the response into a card.
            <div
              aria-hidden="true"
              data-testid="final-answer-boundary"
              className="h-3"
            />
          )}
        </div>
      )}
      {clarificationContent && (
        <ClarificationChoiceCard
          active={enableClarificationActions && !isLoading}
          className="mt-4"
          content={clarificationContent}
          messageId={messages[messages.length - 1]?.id}
        />
      )}
      {showInterruptedReceipt && (
        <div
          className="mt-1 text-xs leading-5 text-muted-foreground/70"
          data-testid="process-interrupted-receipt"
        >
          {t.conversation.interruptedMessage}
        </div>
      )}
    </ChainOfThought>
  );
}

const _PATH_KEYS = [
  "path",
  "file_path",
  "filepath",
  "filename",
  "root",
  "directory",
  "cwd",
  "command",
  "url",
  "query",
] as const;
const _DESC_KEYS = ["description", "desc", "purpose", "reason"] as const;
const _SAFE_CONTEXT_KEYS = [
  "path",
  "file_path",
  "filepath",
  "filename",
  "directory",
  "url",
  "query",
] as const;
const SENSITIVE_ARG_VALUE_RE =
  /(sk-[\w-]+|token|secret|credential|password|passwd|api[_-]?key|bearer\s+[a-z0-9._-]+|id_rsa|id_ed25519|\.pem\b|\.key\b)/i;

function extractPathFromArgs(
  args: Record<string, unknown>,
): string | undefined {
  for (const key of _PATH_KEYS) {
    const val = args[key];
    if (typeof val === "string" && val.trim()) return val.trim();
  }
  return undefined;
}

function extractDescFromArgs(
  args: Record<string, unknown>,
): string | undefined {
  for (const key of _DESC_KEYS) {
    const val = args[key];
    if (typeof val !== "string") continue;
    const text = val.trim();
    if (!text || SENSITIVE_ARG_VALUE_RE.test(text)) continue;
    return text;
  }
  return undefined;
}

function extractSafeContextFromArgs(
  args: Record<string, unknown>,
): string | undefined {
  for (const key of _SAFE_CONTEXT_KEYS) {
    const val = args[key];
    if (typeof val !== "string") continue;
    const text = val.trim();
    if (!text || SENSITIVE_ARG_VALUE_RE.test(text)) continue;
    return text;
  }
  return undefined;
}

function extractTeamCallTarget(
  args: Record<string, unknown>,
): string | undefined {
  for (const key of ["agent_name", "display_name"]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  for (const key of ["agent", "agent_id", "subagent_type", "name", "role"]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) {
      return friendlyRoleName(value.trim());
    }
  }
  for (const key of ["agents", "roles", "team", "specs"]) {
    const value = args[key];
    if (!Array.isArray(value)) continue;
    const names = value
      .map((item) => {
        if (typeof item === "string") return friendlyRoleName(item.trim());
        if (typeof item !== "object" || item === null) return "";
        const record = item as Record<string, unknown>;
        for (const nestedKey of [
          "agent_name",
          "display_name",
          "agent_id",
          "agent",
          "name",
          "role",
        ]) {
          const nested = record[nestedKey];
          if (typeof nested === "string" && nested.trim()) {
            return nestedKey === "agent_name" || nestedKey === "display_name"
              ? nested.trim()
              : friendlyRoleName(nested.trim());
          }
        }
        return "";
      })
      .filter(Boolean);
    if (names.length > 0) return names.join(", ");
  }
  const knownAgentMatch = JSON.stringify(args).match(
    /(Zero|Eve|Kane|Raven|Noah|Luna|Shion|Leon|Market Researcher|Coder|Vibe Selling|Ecommerce Mind)/i,
  );
  return knownAgentMatch?.[1];
}

interface GenericCoTStep<T extends string = string> {
  id?: string;
  messageId?: string;
  type: T;
  iteration?: number;
  phaseId?: string;
  parentItemId?: string;
  progressSequence?: number;
  timelineSequence?: number;
  /** 语义角色，驱动主对话的 compact 时间线选择与弱化展示。 */
  role?: TimelineRole;
  /** true 表示角色来自 fallback 推断（无结构化协议字段） */
  inferred?: boolean;
}

interface CoTReasoningStep extends GenericCoTStep<"reasoning"> {
  reasoning: string | null;
  /** Wall-clock thinking time from the backend (reasoning_duration_ms).
   * Undefined when the backend didn't provide it (legacy data). */
  durationMs?: number;
  /** Grounding reference (source file, etc.) for this reasoning step. */
  groundingMessage?: Message;
}

interface CoTActionCallbackStep extends GenericCoTStep<"actionCallback"> {
  actionText: string;
}

interface CoTCommentaryStep extends GenericCoTStep<"commentary"> {
  commentary: string;
  groundingMessage?: Message;
}

interface CoTToolCallStep extends GenericCoTStep<"toolCall"> {
  name: string;
  args: Record<string, unknown>;
  result?: string | Record<string, unknown> | unknown[];
  effectReceipt?: NonNullable<
    NonNullable<AIMessage["tool_calls"]>[number]["effectReceipt"]
  >;
}

export type CoTStep =
  | CoTReasoningStep
  | CoTActionCallbackStep
  | CoTCommentaryStep
  | CoTToolCallStep;

export interface ReasoningStepGroupItem {
  id: string;
  type: "reasoningGroup";
  steps: CoTReasoningStep[];
  /** 语义角色（取自组内首个步骤，附加信息） */
  role?: TimelineRole;
  inferred?: boolean;
}

export interface ToolCallTimelineItem {
  id: string;
  type: "toolCall";
  step: CoTToolCallStep;
  role?: TimelineRole;
  inferred?: boolean;
}

export interface ActionCallbackGroupItem {
  id: string;
  type: "actionCallbackGroup";
  steps: CoTActionCallbackStep[];
  role?: TimelineRole;
  inferred?: boolean;
}

export interface CommentaryTimelineItem {
  id: string;
  type: "commentary";
  step: CoTCommentaryStep;
  role?: TimelineRole;
  inferred?: boolean;
}

export interface AggregatedToolGroupTimelineItem {
  id: string;
  type: "aggregatedToolGroup";
  aggregateKind: ActionAggregateKind;
  count: number;
  phaseId?: string;
  items: ToolCallTimelineItem[];
  role?: TimelineRole;
  inferred?: boolean;
}

export type TimelineItem =
  | ReasoningStepGroupItem
  | ActionCallbackGroupItem
  | CommentaryTimelineItem
  | ToolCallTimelineItem
  | AggregatedToolGroupTimelineItem;

type DelegationSummary = {
  firstId: string;
  count: number;
  label: string;
};

function buildDelegationSummary(
  items: TimelineItem[],
): Map<string, DelegationSummary> {
  const byTarget = new Map<string, DelegationSummary>();
  const byId = new Map<string, DelegationSummary>();
  for (const item of items) {
    const label = delegationLabel(item);
    if (!label) continue;
    const existing = byTarget.get(label);
    const summary = existing ?? { firstId: item.id, count: 0, label };
    summary.count +=
      item.type === "aggregatedToolGroup"
        ? item.count
        : item.type === "actionCallbackGroup"
          ? item.steps.length
          : 1;
    byTarget.set(label, summary);
    byId.set(item.id, summary);
  }
  return byId;
}

function delegationLabel(item: TimelineItem): string | null {
  if (item.type === "aggregatedToolGroup") {
    if (item.aggregateKind !== "teammate") return null;
    return teammateLabelFromArgs(item.items[0]?.step.args ?? {});
  }
  if (item.type === "toolCall") {
    if (
      getActionDisplay(item.step.name, item.step.args).aggregateKind !==
      "teammate"
    ) {
      return null;
    }
    return teammateLabelFromArgs(item.step.args);
  }
  if (item.type === "actionCallbackGroup") {
    const text = item.steps.map((step) => step.actionText).join(" ");
    if (!/委派任务|delegate|teammate|subagent|call_agent/i.test(text)) {
      return null;
    }
    const match = text.match(/(?:给|to)\s+([^\n,，]+)/i);
    return match?.[1]?.trim() || "other";
  }
  return null;
}

function teammateLabelFromArgs(args: Record<string, unknown>): string {
  for (const key of [
    "agent_name",
    "display_name",
    "role_display_name",
    "codename",
    "agent_id",
    "subagent_type",
    "name",
    "role",
  ]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) {
      return friendlyRoleName(value.trim());
    }
  }
  return "other";
}

export function hasVisibleMessageGroupContent(
  messages: Message[],
  _t?: ReturnType<typeof useI18n>["t"],
): boolean {
  return convertToSteps(messages).length > 0;
}

export function groupConsecutiveReasoningSteps(
  steps: CoTStep[],
): TimelineItem[] {
  const items: TimelineItem[] = [];
  let currentGroup: ReasoningStepGroupItem | null = null;
  let currentActionGroup: ActionCallbackGroupItem | null = null;

  const flushReasoningGroup = () => {
    if (currentGroup) items.push(currentGroup);
    currentGroup = null;
  };
  const flushActionGroup = () => {
    if (currentActionGroup) items.push(currentActionGroup);
    currentActionGroup = null;
  };

  for (const step of steps) {
    if (step.type === "commentary") {
      flushReasoningGroup();
      flushActionGroup();
      items.push({
        id: `${step.id ?? "commentary"}-${items.length}`,
        type: "commentary",
        step,
        // 语义角色直接沿用步骤上已填充的值（附加信息，不影响渲染）
        role: step.role,
        inferred: step.inferred,
      });
      continue;
    }
    if (step.type === "reasoning") {
      flushActionGroup();
      if (!currentGroup) {
        // Only genuinely consecutive reasoning belongs in one row. A tool or
        // public checkpoint is a causal boundary even when it shares the same
        // phase id; reaching backwards across that boundary moves later
        // thinking above work that already happened.
        currentGroup = {
          id: `${step.id ?? "reasoning"}-group`,
          type: "reasoningGroup",
          steps: [],
          // 组内步骤连续且无工具调用穿插，角色与首个步骤一致
          role: step.role,
          inferred: step.inferred,
        };
      } else if (
        step.phaseId &&
        currentGroup.steps[0]?.phaseId &&
        step.phaseId !== currentGroup.steps[0].phaseId
      ) {
        // A phase boundary breaks the run even when reasoning is consecutive:
        // each structured phase keeps its own disclosure row.
        flushReasoningGroup();
        currentGroup = {
          id: `${step.id ?? "reasoning"}-group`,
          type: "reasoningGroup",
          steps: [],
          role: step.role,
          inferred: step.inferred,
        };
      }
      currentGroup.steps.push(step);
      continue;
    }

    if (step.type === "actionCallback") {
      flushReasoningGroup();
      if (!currentActionGroup) {
        currentActionGroup = {
          id: `${step.id ?? "action"}-group`,
          type: "actionCallbackGroup",
          steps: [],
          role: step.role,
          inferred: step.inferred,
        };
      }
      currentActionGroup.steps.push(step);
      continue;
    }

    flushReasoningGroup();
    flushActionGroup();
    items.push({
      id: `${step.messageId ?? step.id ?? "tool"}-${items.length}`,
      type: "toolCall",
      step,
      role: step.role,
      inferred: step.inferred,
    });
  }

  flushReasoningGroup();
  flushActionGroup();
  return items;
}

// 时间线压缩管道（语义采样 / 保留不确定调用 / 聚合同类调用）已抽为
// 纯函数模块 timeline-pipeline.ts。此处 re-export 维持既有测试导入路径。
export { selectCompactTimelineItems } from "./timeline-pipeline";

function executionCoverageByVisibleItem(
  allItems: TimelineItem[],
  visibleItems: TimelineItem[],
): Map<string, TimelineItem[]> {
  const positionByItem = new Map(
    allItems.map((item, index) => [item, index] as const),
  );
  const visibleExecution = visibleItems
    .filter(isExecutionTimelineItem)
    .map((item) => {
      const coveredItems =
        item.type === "aggregatedToolGroup" ? item.items : [item];
      const positions = coveredItems
        .map(
          (covered) => positionByItem.get(covered) ?? Number.MAX_SAFE_INTEGER,
        )
        .sort((a, b) => a - b);
      return {
        item,
        startIdx: positions[0] ?? Number.MAX_SAFE_INTEGER,
        endIdx: positions[positions.length - 1] ?? Number.MAX_SAFE_INTEGER,
      };
    })
    .sort((a, b) => a.startIdx - b.startIdx);
  if (visibleExecution.length === 0) return new Map();

  const coverage = new Map<string, TimelineItem[]>(
    visibleExecution.map(({ item }) => [item.id, []]),
  );
  for (const item of allItems.filter(isExecutionTimelineItem)) {
    const itemIndex = positionByItem.get(item) ?? Number.MAX_SAFE_INTEGER;
    const anchor =
      visibleExecution.find(
        ({ startIdx, endIdx }) => itemIndex >= startIdx && itemIndex <= endIdx,
      ) ??
      visibleExecution.find(({ startIdx }) => startIdx >= itemIndex) ??
      visibleExecution[visibleExecution.length - 1]!;
    coverage.get(anchor.item.id)!.push(item);
  }
  return coverage;
}

/**
 * Source message id for a timeline item, used to order process rows against
 * the answer body they were interleaved with.
 */
export function timelineItemMessageId(item: TimelineItem): string | undefined {
  if (item.type === "toolCall" || item.type === "commentary") {
    return item.step.messageId;
  }
  if (item.type === "aggregatedToolGroup") {
    return item.items[0]?.step.messageId;
  }
  return item.steps[0]?.messageId;
}

function compactToolTarget(step: CoTToolCallStep): string | null {
  const targets = compactToolTargets(step);
  return targets.length > 0 ? targets.join(" · ") : null;
}

/** 渲染层拼句子：按事实 kind 选择 i18n 模板，null 直接透传。 */
function formatFactSummary(
  fact: FactSummary | null,
  t: ReturnType<typeof useI18n>["t"],
): string | null {
  if (!fact) return null;
  switch (fact.kind) {
    case "path":
      return t.messageGrouping.factSummaryPath(fact.value);
    case "count":
      return t.messageGrouping.factSummaryCount(fact.value);
    case "status":
      return t.messageGrouping.factSummaryStatus(fact.value);
    case "title":
      return t.messageGrouping.factSummaryTitle(fact.value);
    case "text":
      return t.messageGrouping.factSummaryText(fact.value);
    case "duration":
      return t.messageGrouping.factSummaryDuration(fact.value);
    case "lines":
      return t.messageGrouping.factSummaryLines(fact.value);
    case "matches":
      return t.messageGrouping.factSummaryMatches(fact.value);
    case "succeeded":
      return t.messageGrouping.factSummarySucceeded;
    case "failed":
      return t.messageGrouping.factSummaryFailed;
    case "exit_code":
      return t.messageGrouping.factSummaryExitCode(fact.value);
  }
}

function localizedActionVerb(
  display: ActionDisplay,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const labels = t.messageGrouping.actionLabels;
  switch (display.labelKey) {
    case "create_file":
      return labels.createFile;
    case "edit_file":
      return labels.editFile;
    case "search_files":
      return labels.searchFiles;
    case "view_directory":
      return labels.viewDirectory;
    case "read_file":
      return labels.readFile;
    case "run_command":
      return labels.runCommand;
    case "search_web":
      return labels.searchWeb;
    case "browse_web":
      return labels.browseWeb;
    case "browser_click":
      return labels.browserClick;
    case "browser_type":
      return labels.browserType;
    case "browser_screenshot":
      return labels.browserScreenshot;
    case "browser_navigate":
      return labels.browserNavigate;
    case "browser_action":
      return labels.browserAction;
    case "update_plan":
      return labels.updatePlan;
    case "use_capability":
      return labels.useCapability;
    case "delegate_task":
      return labels.delegateTask;
    case "submit_result":
      return labels.submitResult;
    case "delete_file":
      return labels.deleteFile;
    case "move_file":
      return labels.moveFile;
    case "start_preview":
      return labels.startPreview;
    case "network_request":
      return labels.networkRequest;
    case "raw":
      return display.verb;
  }
}

function localizedAggregateVerb(
  kind: ActionAggregateKind,
  count: number,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const labels = t.messageGrouping.actionLabels;
  switch (kind) {
    case "file_write":
      return labels.aggregateFileWrite(count);
    case "file_read":
      return labels.aggregateFileRead(count);
    case "command":
      return labels.aggregateCommand(count);
    case "web_search":
      return labels.aggregateWebSearch(count);
    case "browser":
      return labels.aggregateBrowser(count);
    case "teammate":
      return labels.aggregateTeammate(count);
    case "todo":
      return labels.aggregateTodo(count);
    case "other":
      return labels.aggregateOther(count);
  }
}

function compactToolTargets(step: CoTToolCallStep): string[] {
  if (isShellToolName(step.name)) {
    const command = shellCommandFromInput(step.args, step.name);
    if (!command) return [];
    const targets = shellEvidenceTargets(command);
    // One command can contain transport destinations or temporary copies.
    // The first concrete subject is the stable evidence anchor; every other
    // operand remains inspectable in the workbench.
    if (targets.length > 0) {
      return targets.filter(isPublicCompactEvidenceTarget).slice(0, 1);
    }
    return [];
  }
  const target = isReadToolName(step.name)
    ? extractReadEvidenceTarget(step.args)
    : (extractPathFromArgs(step.args) ?? extractTeamCallTarget(step.args));
  if (!target) return [];
  if (
    isReadToolName(step.name) ||
    isFileMutationToolName(step.name) ||
    isPathLikeEvidence(target)
  ) {
    const basename = target.split(/[\\/]/).filter(Boolean).at(-1) ?? target;
    return isPublicCompactEvidenceTarget(basename) ? [basename] : [];
  }
  const summary = compactReasoningSummary(target, 64);
  return isPublicCompactEvidenceTarget(summary) ? [summary] : [];
}

// Planning notes and crawler transport files are implementation evidence, not
// user-facing deliverables. Keep them available in the workbench trace while
// preventing their filenames from being promoted into the conversation lane.
function isPublicCompactEvidenceTarget(target: string): boolean {
  const basename = target.split(/[\\/]/).filter(Boolean).at(-1) ?? target;
  return !(
    /^(?:plan|todos?)\.md$/i.test(basename) ||
    /-full\.jsonl$/i.test(basename) ||
    /\.lock$/i.test(basename)
  );
}

function extractReadEvidenceTarget(
  args: Record<string, unknown>,
): string | undefined {
  // Search/glob tools commonly carry both a broad root and a specific query.
  // The specific object is the useful transcript evidence; the complete root
  // remains available in the workbench trace.
  for (const key of [
    "file_path",
    "filepath",
    "filename",
    "pattern",
    "query",
    "url",
    "path",
    "root",
    "directory",
    "cwd",
  ]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function isPathLikeEvidence(target: string): boolean {
  return !/^https?:\/\//i.test(target) && /[\\/]/.test(target);
}

// Shell commands are evidence, not prose. In the main conversation prefer
// the concrete files/directories they touched and leave the full command in
// the workbench. This prevents machine-local paths and transport workarounds
// such as `cat /Users/...` / `cp /Users/...` from taking over the dialogue.
function shellEvidenceTargets(command: string): string[] {
  const targets: string[] = [];
  const seen = new Set<string>();
  const pathPattern =
    /(?:^|[\s"'=])((?:~|\.{1,2})?\/[\w.@+%:,\-\/]+|[\w.@+%:,\-]+(?:\/[\w.@+%:,\-]+)+)(?=$|[\s"'`;|&<>()[\]{}])/g;
  for (const match of command.matchAll(pathPattern)) {
    const raw = match[1]?.replace(/[,:]+$/, "");
    if (!raw) continue;
    if (isSensitiveShellEvidencePath(raw)) continue;
    const target = raw.split("/").filter(Boolean).at(-1)?.trim();
    if (!target || target === "." || target === "..") continue;
    if (isSensitiveShellEvidenceTarget(target)) continue;
    const key = target.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    targets.push(target);
  }
  return targets;
}

function isSensitiveShellEvidencePath(raw: string): boolean {
  const normalized = raw.replace(/\\/g, "/").toLowerCase();
  return (
    normalized.startsWith("~/.") ||
    normalized.includes("/.ssh/") ||
    normalized.includes("/.gnupg/") ||
    normalized.includes("/keychain/") ||
    normalized.includes("/secrets/") ||
    normalized.includes("/private/var/") ||
    normalized.startsWith("/tmp/") ||
    normalized.includes("/etc/") ||
    /^\/(?:var|private|etc)\//i.test(normalized)
  );
}

function isSensitiveShellEvidenceTarget(target: string): boolean {
  return /(?:id_rsa|id_dsa|id_ecdsa|id_ed25519|known_hosts|authorized_keys|\.pem|\.key|token|secret|credential|password)/i.test(
    target,
  );
}

function summarizeCompactExecutionTargets(
  items: TimelineItem[],
): string | null {
  if (items.length < 2 || !items.every((item) => item.type === "toolCall")) {
    return null;
  }
  const targets = Array.from(
    new Set(
      items
        .flatMap((item) =>
          compactToolTargets((item as ToolCallTimelineItem).step),
        )
        .filter((target): target is string => Boolean(target)),
    ),
  );
  if (targets.length < 2) return null;
  // Long exploratory runs also touch roots, `..`, and search scopes. Prefer
  // concrete file artifacts so the summary reflects what the user can actually
  // inspect, while still falling back to directory/search scopes when artifacts
  // are missing.
  const artifactTargets = targets.filter(isFileArtifactEvidence);
  const sourceTargets = artifactTargets.length >= 2 ? artifactTargets : targets;
  const visibleTargets = sourceTargets.slice(-3);
  const hiddenCount = sourceTargets.length - visibleTargets.length;
  return `${visibleTargets.join(" · ")}${hiddenCount > 0 ? ` +${hiddenCount}` : ""}`;
}

function isFileArtifactEvidence(target: string): boolean {
  return /(?:^|[\\/])[\w@+%:,\-]+\.[a-z0-9]{1,12}$/i.test(target);
}

function lastTimelineStep(item: TimelineItem): CoTStep {
  if (item.type === "toolCall" || item.type === "commentary") return item.step;
  if (item.type === "aggregatedToolGroup")
    return item.items[item.items.length - 1]!.step;
  return item.steps[item.steps.length - 1]!;
}

/**
 * 双向联动共享 id：与侧边栏条目使用同一个工作台事件 id
 * （即行上的 data-process-event-id），两侧只存 id、不复制时间线数据。
 * 事件 id 缺失时回退到 TimelineItem.id，保证 DOM 定位属性稳定。
 */
function timelineItemLinkageId(item: TimelineItem): string {
  if (item.type === "toolCall") return item.step.id ?? item.id;
  if (item.type === "aggregatedToolGroup") return item.id;
  const step = lastTimelineStep(item);
  return step.messageId ?? step.id ?? item.id;
}

function stepText(step: CoTStep): string {
  if (step.type === "reasoning") return step.reasoning ?? "";
  if (step.type === "actionCallback") return step.actionText;
  if (step.type === "commentary") return step.commentary;
  const argsText = JSON.stringify(step.args ?? {});
  return `${step.name}\n${argsText}`;
}

function runStateForCurrentStep(
  step: CoTStep,
  isLoading: boolean,
): AgentRunState {
  if (stepHasError(step)) return "error";
  if (stepIsWaiting(step)) return "waiting";
  if (isLoading) return "running";
  return "done";
}

function stepHasError(step: CoTStep): boolean {
  if (step.type !== "toolCall") return false;
  return isToolResultError(step.result);
}

function stepIsWaiting(step: CoTStep): boolean {
  if (step.type === "toolCall") {
    const name = step.name.toLowerCase();
    if (name === "ask_clarification" || name === "ask_user_question") {
      return true;
    }
    if (typeof step.result === "string" && isApprovalRequest(step.result)) {
      return true;
    }
    if (typeof step.result === "object" && step.result !== null) {
      const record = step.result as Record<string, unknown>;
      return (
        record.status === "waiting" ||
        record.status === "waiting_approval" ||
        record.requires_approval === true
      );
    }
    return false;
  }
  return /(?:\b(?:awaiting|waiting)(?:\s+for)?\s+(?:approval|confirmation|the\s+user|user\s+(?:input|reply))\b|\bapproval\s+(?:needed|required|pending)\b|待(?:用户)?确认|等待(?:用户)?(?:确认|审批|回复|输入)|审批中)/i.test(
    stepText(step),
  );
}

function summarizeCurrentStep(
  step: CoTStep,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (step.type === "reasoning") {
    return compactReasoningSummary(
      stripTraceLabelPrefixes(step.reasoning ?? ""),
      96,
      t,
    );
  }
  if (step.type === "actionCallback") {
    return compactReasoningSummary(
      stripTraceLabelPrefixes(step.actionText),
      96,
      t,
    );
  }
  if (step.type === "commentary") {
    return compactReasoningSummary(step.commentary, 120, t);
  }
  const publicAction = publicActionTextFromTraceTool(
    step.name,
    extractSafeContextFromArgs(step.args) ?? extractTeamCallTarget(step.args),
    t,
  );
  return (
    publicAction ??
    extractDescFromArgs(step.args) ??
    t.messageGrouping.runAction
  );
}

function summarizeReasoningGroup(
  group: ReasoningStepGroupItem,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const text = [...group.steps]
    .reverse()
    .map((step) => step.reasoning ?? "")
    .find((value) => value.trim());
  return compactReasoningSummary(stripTraceLabelPrefixes(text), 120, t);
}

function summarizeActionGroup(
  group: ActionCallbackGroupItem,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const text = [...group.steps]
    .reverse()
    .map((step) => step.actionText)
    .find((value) => value.trim());
  const normalized = stripTraceLabelPrefixes(text).trim();
  const rawToolName = normalized.match(RAW_PUBLIC_TOOL_CALLBACK_RE)?.[1];
  if (rawToolName) {
    const publicAction = publicActionTextFromTraceTool(
      rawToolName,
      undefined,
      t,
    );
    if (publicAction) return publicAction;
  }
  return compactReasoningSummary(normalized, 96, t);
}

function compactReasoningSummary(
  value: string,
  max = 120,
  t?: ReturnType<typeof useI18n>["t"],
): string {
  const normalized = value
    .replace(/\s+/g, " ")
    .replace(/^\s*[-*•]\s+/, "")
    .trim();
  if (!normalized)
    return t?.messageGrouping.reasoningFallback ?? "Summarize public progress";
  if (normalized.length <= max) return normalized;
  return `${normalized.slice(0, max).trimEnd()}...`;
}

/**
 * Format thinking duration in a human-readable compact form.
 * Examples: "2.3s", "15.8s", "1m 23s"
 */
function formatThinkingDuration(durationMs: number): string {
  if (durationMs < 1000) return `${durationMs}ms`;
  const seconds = durationMs / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return remainingSeconds > 0
    ? `${minutes}m ${remainingSeconds}s`
    : `${minutes}m`;
}

/**
 * Build the collapsed-history-phase summary: phase name (from the first
 * commentary/intent item) + key action statistics (aggregated by kind).
 * Mirrors spec §当前帧聚焦 "✓ 了解代码结构 · 查看了 12 个文件".
 */
function summarizeCollapsedPhase(
  phaseItems: TimelineItem[],
  t: ReturnType<typeof useI18n>["t"],
): string {
  // 1. Phase name: first non-empty commentary text in the phase.
  let phaseName: string | null = null;
  for (const item of phaseItems) {
    if (item.type === "commentary") {
      const text = publicProcessText(item.step.commentary);
      if (text.trim()) {
        phaseName = compactReasoningSummary(text, 32, t);
        break;
      }
    }
  }

  // 2. Aggregate tool-call stats by kind. aggregatedToolGroup carries its
  //    own kind+count; individual toolCall items map via getActionDisplay.
  const kindCounts = new Map<ActionAggregateKind, number>();
  for (const item of phaseItems) {
    if (item.type === "aggregatedToolGroup") {
      kindCounts.set(
        item.aggregateKind,
        (kindCounts.get(item.aggregateKind) ?? 0) + item.count,
      );
    } else if (item.type === "toolCall") {
      const display = getActionDisplay(item.step.name, item.step.args);
      kindCounts.set(
        display.aggregateKind,
        (kindCounts.get(display.aggregateKind) ?? 0) + 1,
      );
    }
  }

  // 3. Build stats string. Priority: file_read > file_write > command > web_search.
  //    Cap at 2 stats so the collapsed row stays compact.
  const statsKinds: ActionAggregateKind[] = [
    "file_read",
    "file_write",
    "command",
    "web_search",
  ];
  const statsParts: string[] = [];
  for (const kind of statsKinds) {
    const count = kindCounts.get(kind);
    if (count) {
      statsParts.push(localizedAggregateVerb(kind, count, t));
    }
    if (statsParts.length >= 2) break;
  }

  const parts = [phaseName, ...statsParts].filter(Boolean);
  if (parts.length > 0) return parts.join(" · ");
  // Fallback: "完成了 N 件事" per spec §当前帧聚焦 — count action items,
  // not raw steps, so a 3-tool phase reads as "完成了 3 件事" not "3 步".
  const actionItemCount = phaseItems.filter(
    (item) => item.type === "toolCall" || item.type === "aggregatedToolGroup",
  ).length;
  if (actionItemCount > 0) {
    return t.message.completedThings(actionItemCount);
  }
  return t.message.completedSteps(phaseItems.length);
}

function extractLegacyReasoningSummary(message: Message): string | null {
  if (message.type !== "ai") return null;
  const additional = message.additional_kwargs;
  const direct = additional?.public_reasoning_summary;
  if (typeof direct === "string" && direct.trim()) return direct.trim();

  const echo = additional?.echo;
  if (typeof echo === "object" && echo !== null) {
    const nested = (echo as Record<string, unknown>)
      .public_reasoning_summary;
    if (typeof nested === "string" && nested.trim()) return nested.trim();
  }

  // This compatibility field is readable reasoning, not public commentary.
  // The caller keeps it in the private reasoning lane.
  return null;
}

/**
 * Read the provider thinking that the realtime adapter persisted on the
 * AIMessage (`additional_kwargs.reasoning_content`). The historical render
 * path aligns with live streaming: reasoning stays in a typewriter/collapsible
 * row when the thread is replayed, including when the same projected message
 * also carries public commentary in message.content.
 */
function extractRawReasoningContent(message: Message): string | null {
  if (message.type !== "ai") return null;
  const raw = message.additional_kwargs?.reasoning_content;
  let text: string | null = null;
  if (typeof raw === "string" && raw.trim()) {
    text = raw.trim();
  } else if (Array.isArray(raw)) {
    const joined = raw
      .filter(
        (part): part is string =>
          typeof part === "string" && Boolean(part.trim()),
      )
      .join("\n");
    text = joined.trim() || null;
  }
  if (!text) return null;
  return stripReactProtocolBlocks(text);
}

// ReAct-style trace labels. Thought blocks are the user-facing thinking;
// Action / Observation / Final Answer blocks are internal tool protocol and
// must be dropped entirely (their bodies are tool args + raw tool output).
const REACT_BLOCK_START_RE =
  /^\s*(?:Thought|Action|Observation|Final Answer|Tool|Tool Result)\s*:/;

/**
 * Drop Action/Observation/Final-Answer blocks from a raw reasoning trace,
 * keeping only the Thought narration (labels are stripped downstream by
 * redactPublicProcessText). Plain prose reasoning without ReAct labels is
 * returned untouched.
 */
function stripReactProtocolBlocks(text: string): string | null {
  const lines = text.split(/\r?\n/);
  const kept: string[] = [];
  let inProtocolBlock = false;
  for (const line of lines) {
    if (REACT_BLOCK_START_RE.test(line)) {
      inProtocolBlock = !/^\s*Thought\s*:/.test(line);
      if (!inProtocolBlock) {
        kept.push(line);
      }
      continue;
    }
    if (inProtocolBlock) continue;
    kept.push(line);
  }
  const joined = kept.join("\n").trim();
  return joined || null;
}

export function convertToSteps(messages: Message[]): CoTStep[] {
  const steps: CoTStep[] = [];
  // Deduplicate within a protocol lane only. Identical text in reasoning and
  // commentary is still two different events with different visibility.
  const seenReasoningNarrative = new Set<string>();
  const seenCommentaryNarrative = new Set<string>();
  const seenToolCallIds = new Set<string>();
  // 来源消息带 public_progress 协议标记的消息 id 集合（供语义角色判定）
  const publicProgressMessageIds = new Set<string>();
  for (const message of messages) {
    if (
      message.type === "ai" &&
      message.additional_kwargs?.public_progress === true &&
      message.id
    ) {
      publicProgressMessageIds.add(message.id);
    }
  }
  let iteration = 1;
  let lastStepType: "reasoning" | "toolCall" | null = null;
  let deferredPrelude: { message: Message; commentary: string } | null = null;
  // A message's ``reasoning_duration_ms`` is the TOTAL thinking time for the
  // whole message's reasoning, not a per-chunk figure. splitReasoning cuts one
  // message into many chunks; attributing the full duration to every chunk
  // makes a merged group over-count by the chunk count and makes tool-separated
  // chunks all repeat the same total. Attribute it once, to the first kept
  // reasoning step of the message.
  const durationAssignedForMessage = new Set<string>();

  const pushReasoningStep = (
    message: Message,
    reasoning: string,
    idSuffix = "reasoning",
    groundingMessage?: Message,
  ) => {
    if (!reasoning.trim()) return;
    const fingerprint = timelineNarrativeFingerprint(reasoning);
    if (!fingerprint || seenReasoningNarrative.has(fingerprint)) return;
    seenReasoningNarrative.add(fingerprint);
    if (lastStepType === "toolCall") {
      iteration++;
    }
    const durationMs =
      (typeof message.id !== "string" ||
        !durationAssignedForMessage.has(message.id)) &&
      typeof message.additional_kwargs?.reasoning_duration_ms === "number" &&
      Number.isFinite(message.additional_kwargs.reasoning_duration_ms)
        ? message.additional_kwargs.reasoning_duration_ms
        : undefined;
    if (durationMs !== undefined && typeof message.id === "string") {
      durationAssignedForMessage.add(message.id);
    }
    steps.push({
      id: `${message.id}-${idSuffix}`,
      messageId: message.id,
      type: "reasoning",
      reasoning,
      phaseId:
        typeof message.additional_kwargs?.phase_id === "string"
          ? message.additional_kwargs.phase_id
          : undefined,
      parentItemId:
        typeof message.additional_kwargs?.parent_item_id === "string"
          ? message.additional_kwargs.parent_item_id
          : undefined,
      progressSequence:
        typeof message.additional_kwargs?.progress_sequence === "number"
          ? message.additional_kwargs.progress_sequence
          : undefined,
      timelineSequence:
        typeof message.additional_kwargs?.timeline_sequence === "number"
          ? message.additional_kwargs.timeline_sequence
          : undefined,
      durationMs,
      groundingMessage,
      iteration,
    });
    lastStepType = "reasoning";
  };

  const pushCommentaryStep = (
    message: Message,
    commentary: string,
    idSuffix = "commentary",
  ) => {
    const publicCommentary = publicProcessText(commentary);
    const fingerprint = timelineNarrativeFingerprint(publicCommentary);
    if (
      !publicCommentary ||
      !fingerprint ||
      seenCommentaryNarrative.has(fingerprint)
    ) {
      return;
    }
    seenCommentaryNarrative.add(fingerprint);
    steps.push({
      id: `${message.id}-${idSuffix}`,
      messageId: message.id,
      type: "commentary",
      commentary: publicCommentary,
      phaseId:
        typeof message.additional_kwargs?.phase_id === "string"
          ? message.additional_kwargs.phase_id
          : undefined,
      parentItemId:
        typeof message.additional_kwargs?.parent_item_id === "string"
          ? message.additional_kwargs.parent_item_id
          : undefined,
      progressSequence:
        typeof message.additional_kwargs?.progress_sequence === "number"
          ? message.additional_kwargs.progress_sequence
          : undefined,
      timelineSequence:
        typeof message.additional_kwargs?.timeline_sequence === "number"
          ? message.additional_kwargs.timeline_sequence
          : undefined,
      groundingMessage: Array.isArray(message.additional_kwargs?.grounding)
        ? message
        : undefined,
      iteration,
    });
    lastStepType = "reasoning";
  };

  const flushDeferredPrelude = () => {
    if (!deferredPrelude) return;
    pushCommentaryStep(
      deferredPrelude.message,
      deferredPrelude.commentary,
      "process-prelude",
    );
    deferredPrelude = null;
  };

  const toToolCallStep = (
    message: Message,
    toolCall: NonNullable<AIMessage["tool_calls"]>[number],
  ): CoTToolCallStep => {
    const step: CoTToolCallStep = {
      id: toolCall.id,
      messageId: message.id,
      type: "toolCall",
      name: toolCall.name,
      args: toolCall.args,
      iteration,
      phaseId: toolCall.phaseId ?? undefined,
      parentItemId: toolCall.parentItemId ?? undefined,
      timelineSequence: toolCall.timelineSequence ?? undefined,
      effectReceipt: toolCall.effectReceipt ?? undefined,
    };
    const toolCallId = toolCall.id;
    if (toolCallId) {
      const toolCallResult = findToolCallResult(toolCallId, messages);
      if (toolCallResult) {
        try {
          const json = JSON.parse(toolCallResult);
          step.result = json;
        } catch (e) {
          swallow(e);
          step.result = toolCallResult;
        }
      }
    }
    return step;
  };

  for (
    let messageIndex = 0;
    messageIndex < messages.length;
    messageIndex += 1
  ) {
    const message = messages[messageIndex]!;
    if (message.type === "ai") {
      if (isProcessPrelude(message, messageIndex, messages)) {
        const commentary = extractContentFromMessage(message);
        if (commentary.trim()) deferredPrelude = { message, commentary };
        continue;
      }
      const tc = (message as AIMessage).tool_calls;
      const visibleToolCalls = (tc ?? []).filter((toolCall) => {
        if (isHiddenTimelineToolName(toolCall.name)) return false;
        if (!toolCall.id) return true;
        if (seenToolCallIds.has(toolCall.id)) return false;
        seenToolCallIds.add(toolCall.id);
        return true;
      });
      // Reasoning remains private regardless of whether it came from the
      // current ``reasoning_content`` field or a legacy readable summary.
      // Public progress is carried only by message.content on a commentary
      // item, never inferred from these strings.
      const legacyReasoning = extractLegacyReasoningSummary(message);
      const rawReasoning = extractRawReasoningContent(message);
      const isPublicProgress =
        message.additional_kwargs?.public_progress === true;
      const reasoningText = rawReasoning || legacyReasoning;
      const reasoningIsLegacySummary =
        !rawReasoning && Boolean(legacyReasoning);
      const reasoningChunks = dedupeTimelineChunks(
        reasoningText
          ? splitReasoningIntoTimelineChunks(reasoningText)
              .map((chunk) => normalizePublicTimelineChunk(chunk))
              .filter((chunk): chunk is string => Boolean(chunk?.trim()))
          : [],
      );

      if (isPublicProgress) {
        // A checkpoint follows the previous tool result and the current
        // private reasoning. Preserve that real order instead of zipping the
        // three channels by array index.
        flushDeferredPrelude();
        for (const toolCall of visibleToolCalls) {
          steps.push(toToolCallStep(message, toolCall));
          lastStepType = "toolCall";
        }
        const commentary = publicProcessText(
          extractContentFromMessage(message),
        );
        const commentaryFingerprint = commentary
          ? timelineNarrativeFingerprint(commentary)
          : "";
        // A projected commentary message may also carry preceding reasoning
        // metadata. Keep it in the private reasoning row; message.content is
        // the only public prose.
        for (let index = 0; index < reasoningChunks.length; index += 1) {
          const reasoningChunk = reasoningChunks[index];
          if (
            reasoningChunk &&
            !(
              reasoningIsLegacySummary &&
              commentaryFingerprint &&
              timelineNarrativeFingerprint(reasoningChunk) ===
                commentaryFingerprint
            )
          ) {
            pushReasoningStep(message, reasoningChunk, `reasoning-${index}`);
          }
        }
        if (
          commentary &&
          commentaryFingerprint &&
          !seenCommentaryNarrative.has(commentaryFingerprint)
        ) {
          seenCommentaryNarrative.add(commentaryFingerprint);
          steps.push({
            id: `${message.id}-commentary`,
            messageId: message.id,
            type: "commentary",
            commentary,
            phaseId:
              typeof message.additional_kwargs?.phase_id === "string"
                ? message.additional_kwargs.phase_id
                : undefined,
            parentItemId:
              typeof message.additional_kwargs?.parent_item_id === "string"
                ? message.additional_kwargs.parent_item_id
                : undefined,
            progressSequence:
              typeof message.additional_kwargs?.progress_sequence === "number"
                ? message.additional_kwargs.progress_sequence
                : undefined,
            timelineSequence:
              typeof message.additional_kwargs?.timeline_sequence === "number"
                ? message.additional_kwargs.timeline_sequence
                : undefined,
            groundingMessage: Array.isArray(
              message.additional_kwargs?.grounding,
            )
              ? message
              : undefined,
            iteration,
          });
          lastStepType = "reasoning";
        }
        continue;
      }

      const maxStepCount = Math.max(
        reasoningChunks.length,
        visibleToolCalls.length,
      );
      for (let index = 0; index < maxStepCount; index += 1) {
        const reasoningChunk = reasoningChunks[index];
        if (reasoningChunk) {
          pushReasoningStep(message, reasoningChunk, `reasoning-${index}`);
        }
        const tool_call = visibleToolCalls[index];
        if (reasoningChunk || tool_call) flushDeferredPrelude();
        if (!tool_call) continue;
        const step = toToolCallStep(message, tool_call);
        steps.push(step);
        lastStepType = "toolCall";
      }
    }
  }
  // 出口处填充语义角色（纯附加信息：不改变步骤顺序、内容与去重结果）
  return assignTimelineRoles(steps, { publicProgressMessageIds });
}

function splitReasoningIntoTimelineChunks(reasoning: string): string[] {
  const trimmed = reasoning.trim();
  if (!trimmed) return [];

  const paragraphChunks = trimmed
    .split(/\n{2,}/)
    .map((chunk) => chunk.trim())
    .filter(Boolean);
  if (paragraphChunks.length > 1) return paragraphChunks;

  return trimmed
    .split(
      /(?<=[.!?。！？])\s+(?=(?:Now|Let me|The |This |I |We |继续|现在|接下来|然后)\b)/,
    )
    .map((chunk) => chunk.trim())
    .filter(Boolean);
}
