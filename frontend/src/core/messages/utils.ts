import type { AIMessage, Message, ToolMessage } from "@/core/api/types";

interface GenericMessageGroup<T = string> {
  type: T;
  id: string | undefined;
  messages: Message[];
}

interface HumanMessageGroup extends GenericMessageGroup<"human"> {}

interface AssistantProcessingGroup extends GenericMessageGroup<"assistant:processing"> {}

interface AssistantMessageGroup extends GenericMessageGroup<"assistant"> {}

interface AssistantPresentFilesGroup extends GenericMessageGroup<"assistant:present-files"> {}

interface AssistantClarificationGroup extends GenericMessageGroup<"assistant:clarification"> {}

interface AssistantSubagentGroup extends GenericMessageGroup<"assistant:subagent"> {}

export type MessageGroup =
  | HumanMessageGroup
  | AssistantProcessingGroup
  | AssistantMessageGroup
  | AssistantPresentFilesGroup
  | AssistantClarificationGroup
  | AssistantSubagentGroup;

function normalizedNarrativeText(value: unknown): string {
  return typeof value === "string"
    ? value.replace(/\s+/g, " ").trim().toLowerCase()
    : "";
}

function publicNarrativeCandidates(message: Message): string[] {
  if (message.type !== "ai") return [];
  const additional = message.additional_kwargs;
  const echo =
    additional?.echo && typeof additional.echo === "object"
      ? (additional.echo as Record<string, unknown>)
      : null;
  return [
    extractContentFromMessage(message),
    additional?.public_reasoning_summary,
    echo?.public_reasoning_summary,
  ]
    .map(normalizedNarrativeText)
    .filter(Boolean);
}

/**
 * Check whether an AI message is an intermediate process prelude rather than
 * the final answer. This covers cases where the model emits a plan/commentary
 * message (e.g. "接下来我会先圈定3个...") BEFORE any tool calls. If subsequent
 * AI messages in the same turn carry tool calls or public_progress markers,
 * this message belongs on the process timeline, not as a standalone answer
 * bubble above the thinking. groupMessages uses an equivalent pre-indexed
 * predicate; this exported helper remains useful to callers classifying one
 * isolated message.
 */
export function isProcessPrelude(
  message: Message,
  index: number,
  messages: Message[],
): boolean {
  if (message.type !== "ai") return false;
  if (hasToolCalls(message)) return false;
  if (message.additional_kwargs?.public_progress === true) return false;
  // A provider may emit bookkeeping tools (for example the final todo
  // completion) after it has already published the answer. Protocol-marked
  // answers — and legacy messages that satisfy the same final-answer
  // compatibility predicate — must stay in the answer lane instead of being
  // reclassified as a process prelude merely because work follows them.
  if (isLikelyFinalAnswerContent(message)) return false;
  // Only messages with visible content can be preludes; reasoning-only
  // messages are already routed by the hasReasoning && !hasContent branch.
  if (!hasContent(message)) return false;

  for (let nextIndex = index + 1; nextIndex < messages.length; nextIndex += 1) {
    const next = messages[nextIndex]!;
    if (next.type === "human") break;
    if (next.type !== "ai") continue;
    // A later AI message in the same turn that carries tool calls or
    // public progress signals means processing continues after this message →
    // this message is commentary/plan, not the final answer.
    if (
      next.additional_kwargs?.public_progress === true ||
      hasToolCalls(next)
    ) {
      return true;
    }
  }
  return false;
}

interface FutureMessageSignals {
  /** A later tool/progress AI message exists before the next human turn. */
  hasLaterProcessMessage: boolean[];
  /** A later structured process message repeats this message's narrative. */
  hasLaterDuplicateNarrative: boolean[];
  /** Any later non-error assistant message has visible content. */
  hasLaterSuccessfulAssistant: boolean[];
}

/**
 * Build the forward-looking predicates used by groupMessages in one reverse
 * walk. The former implementation sliced/scanned the remaining transcript
 * for every message, which made every streamed frame approach O(messages²)
 * on long-running tasks.
 */
function indexFutureMessageSignals(messages: Message[]): FutureMessageSignals {
  const hasLaterProcessMessage = Array<boolean>(messages.length).fill(false);
  const hasLaterDuplicateNarrative = Array<boolean>(messages.length).fill(
    false,
  );
  const hasLaterSuccessfulAssistant = Array<boolean>(messages.length).fill(
    false,
  );

  let laterProcessMessage = false;
  let laterSuccessfulAssistant = false;
  const laterProcessNarratives = new Set<string>();

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]!;
    hasLaterSuccessfulAssistant[index] = laterSuccessfulAssistant;
    if (
      (message.type === "ai" || message.type === "assistant") &&
      !message.additional_kwargs?.error &&
      hasContent(message)
    ) {
      laterSuccessfulAssistant = true;
    }

    if (message.type === "human") {
      laterProcessMessage = false;
      laterProcessNarratives.clear();
      continue;
    }

    hasLaterProcessMessage[index] = laterProcessMessage;
    if (message.type !== "ai") continue;

    const narrative = normalizedNarrativeText(
      extractContentFromMessage(message),
    );
    hasLaterDuplicateNarrative[index] = Boolean(
      narrative && laterProcessNarratives.has(narrative),
    );

    const isStructuredProcessMessage =
      message.additional_kwargs?.public_progress === true ||
      hasToolCalls(message) ||
      hasReasoning(message);
    if (isStructuredProcessMessage) {
      for (const candidate of publicNarrativeCandidates(message)) {
        laterProcessNarratives.add(candidate);
      }
    }
    if (
      message.additional_kwargs?.public_progress === true ||
      hasToolCalls(message)
    ) {
      laterProcessMessage = true;
    }
  }

  return {
    hasLaterProcessMessage,
    hasLaterDuplicateNarrative,
    hasLaterSuccessfulAssistant,
  };
}

export function groupMessages<T>(
  messages: Message[],
  mapper: (group: MessageGroup) => T,
): T[] {
  if (messages.length === 0) {
    return [];
  }

  const groups: MessageGroup[] = [];
  const futureSignals = indexFutureMessageSignals(messages);
  let currentTurnProcessingGroup: MessageGroup | null = null;
  const toolCallOwners = new Map<string, MessageGroup>();

  function indexToolCallOwner(
    message: Message,
    group: MessageGroup,
    prefer = false,
  ) {
    if (message.type !== "ai") return;
    for (const toolCall of (message as AIMessage).tool_calls ?? []) {
      if (!toolCall.id) continue;
      if (prefer || !toolCallOwners.has(toolCall.id)) {
        toolCallOwners.set(toolCall.id, group);
      }
    }
  }

  // Returns the last group if it can still accept tool messages
  // (i.e. it's an in-flight processing group, not a terminal human/assistant group).
  function lastOpenGroup() {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.type !== "human" &&
      last.type !== "assistant" &&
      last.type !== "assistant:clarification"
    ) {
      return last;
    }
    return null;
  }

  // Terminal receipts (interrupted/failed/final answer) can be reduced before
  // an in-flight tool callback reaches the client. Keep looking within the
  // current human turn so those late process events return to the original
  // process lane instead of opening a new row below the terminal answer.
  function lastProcessingGroupInCurrentTurn() {
    return currentTurnProcessingGroup;
  }

  function groupOwningToolCall(toolCallId: string | undefined) {
    if (!toolCallId) return null;
    return toolCallOwners.get(toolCallId) ?? null;
  }

  function appendToCurrentProcessingGroup(message: Message) {
    const current = lastProcessingGroupInCurrentTurn();
    if (current) {
      current.messages.push(message);
      indexToolCallOwner(message, current, true);
      return current;
    }
    const group: MessageGroup = {
      id: message.id,
      type: "assistant:processing",
      messages: [message],
    };
    groups.push(group);
    currentTurnProcessingGroup = group;
    indexToolCallOwner(message, group, true);
    return group;
  }

  // AI progress/reasoning is chronological conversation content. If a final
  // answer has already been emitted, do not append a later thought back into
  // the older processing group above it — that makes the thought stream
  // visually stay at the top while the answer keeps growing below. Tool
  // callbacks retain the association-aware helper above because they may
  // arrive late and must stay attached to their originating call.
  function appendToLatestProcessingGroup(message: Message) {
    const latest = groups[groups.length - 1];
    if (latest?.type === "assistant:processing") {
      latest.messages.push(message);
      currentTurnProcessingGroup = latest;
      return;
    }
    const group: MessageGroup = {
      id: message.id,
      type: "assistant:processing",
      messages: [message],
    };
    groups.push(group);
    currentTurnProcessingGroup = group;
  }

  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index]!;
    if (isHiddenFromUIMessage(message)) {
      continue;
    }

    if (
      isSupersededApprovalTimeoutMessage(
        message,
        futureSignals.hasLaterSuccessfulAssistant[index]!,
      )
    ) {
      continue;
    }

    if (message.name === "todo_reminder") {
      continue;
    }

    if (message.type === "human") {
      groups.push({ id: message.id, type: "human", messages: [message] });
      currentTurnProcessingGroup = null;
      continue;
    }

    if (message.type === "tool") {
      if (isClarificationToolMessage(message)) {
        // Add to the preceding processing group to preserve tool-call association,
        // then also open a standalone clarification group for prominent display.
        lastOpenGroup()?.messages.push(message);
        groups.push({
          id: message.id,
          type: "assistant:clarification",
          messages: [message],
        });
      } else {
        const open =
          groupOwningToolCall((message as ToolMessage).tool_call_id) ??
          lastProcessingGroupInCurrentTurn() ??
          lastOpenGroup();
        if (open) {
          open.messages.push(message);
        } else {
          // Orphaned tool message (e.g., approval request from PermissionMiddleware).
          // Wrap it in its own processing group instead of dropping it.
          const group: MessageGroup = {
            id: message.id,
            type: "assistant:processing",
            messages: [message],
          };
          groups.push(group);
          currentTurnProcessingGroup = group;
        }
      }
      continue;
    }

    if (message.type === "ai") {
      if (hasPresentFiles(message)) {
        const group: MessageGroup = {
          id: message.id,
          type: "assistant:present-files",
          messages: [message],
        };
        groups.push(group);
        indexToolCallOwner(message, group);
      } else if (hasSubagent(message)) {
        const group: MessageGroup = {
          id: message.id,
          type: "assistant:subagent",
          messages: [message],
        };
        groups.push(group);
        indexToolCallOwner(message, group);
      } else if (message.additional_kwargs?.public_progress === true) {
        // Public checkpoints are answer-like prose, but they belong to the
        // chronological process lane rather than becoming standalone final
        // answer bubbles with a repeated assistant header.
        appendToLatestProcessingGroup(message);
      } else if (hasToolCalls(message)) {
        // Tool-call message: render public thinking / execution first.
        // If this same message carries a long final answer, append it
        // after the processing group so the report streams last.
        // Tool-call message → processing group (rendered as ChainOfThought
        // by MessageGroup, which shows the tool steps + a collapsed fold
        // for the reasoning trace).
        appendToCurrentProcessingGroup(message);
        if (hasContent(message) && isLikelyFinalAnswerContent(message)) {
          groups.push({
            id: message.id,
            type: "assistant",
            messages: [message],
          });
        }
      } else if (message.additional_kwargs?.response_state === "interrupted") {
        // The final draft is intentionally blank in the transcript, but the
        // turn still needs a small terminal receipt. Keep this as an ordinary
        // assistant group so MessageListItem can render that status without
        // inventing user-visible answer text.
        groups.push({ id: message.id, type: "assistant", messages: [message] });
      } else if (
        message.additional_kwargs?.response_state === "failed" ||
        message.additional_kwargs?.response_state === "blocked" ||
        message.additional_kwargs?.error
      ) {
        // Failed turns carry their detailed diagnostic as structured metadata.
        // Keep a terminal assistant group so MessageList can render one compact
        // receipt without replaying the raw guard/stack text in the transcript.
        groups.push({ id: message.id, type: "assistant", messages: [message] });
      } else if (
        hasContent(message) &&
        (futureSignals.hasLaterDuplicateNarrative[index] ||
          (!isLikelyFinalAnswerContent(message) &&
            futureSignals.hasLaterProcessMessage[index]))
      ) {
        appendToLatestProcessingGroup(message);
      } else if (hasReasoning(message) && !hasContent(message)) {
        // Reasoning-only intermediate message (no content, no tool
        // calls yet). Append to the processing group so it renders in
        // correct chronological order (thinking → commentary → actions)
        // inside MessageGroup, rather than creating a separate assistant
        // group that would render AFTER the processing lane and reverse
        // the visual order.
        appendToLatestProcessingGroup(message);
      } else if (hasContent(message)) {
        // Plain AI response (with or without reasoning). Render as a
        // normal assistant message — MessageListItem will draw a
        // collapsed reasoning fold above the content if reasoning is
        // present, so the chain of thought stays accessible without
        // dominating the visible answer.
        groups.push({ id: message.id, type: "assistant", messages: [message] });
      }
    }
  }

  return groups
    .map(mapper)
    .filter((result) => result !== undefined && result !== null) as T[];
}

export function extractTextFromMessage(message: Message) {
  if (typeof message.content === "string") {
    const content =
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim();
    return message.type === "ai" ? visibleAIContent(content) : content;
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) =>
        content.type === "text"
          ? message.type === "ai"
            ? visibleAIContent(content.text)
            : content.text.trim()
          : "",
      )
      .join("\n")
      .trim();
  }
  return "";
}

const THINK_TAG_RE = /<think>\s*([\s\S]*?)\s*<\/think>/g;

function splitInlineReasoning(content: string) {
  const reasoningParts: string[] = [];
  const cleaned = content
    .replace(THINK_TAG_RE, (_, reasoning: string) => {
      const normalized = reasoning.trim();
      if (normalized) {
        reasoningParts.push(normalized);
      }
      return "";
    })
    .trim();

  return {
    content: cleaned,
    reasoning: reasoningParts.length > 0 ? reasoningParts.join("\n\n") : null,
  };
}

function splitInlineReasoningFromAIMessage(message: Message) {
  if (message.type !== "ai" || typeof message.content !== "string") {
    return null;
  }
  return splitInlineReasoning(message.content);
}

const INTERNAL_TOOL_FENCE_RE =
  /```(?:tool|tools?|tool_call|echo-tool)\b[\s\S]*?(?:```|$)/gi;
const JSON_COMMAND_TOOL_FENCE_RE =
  /(?:\*\*Task:[^\n]*\*\*\s*)?```json\s*\{\s*"command"\s*:\s*"(?:fs_writer|fs_writen|fs_written|write_file|write_text_file|edit_text_file|str_replace|apply_patch)"[\s\S]*?(?:```|$)/gi;
/** Tool-call protocol emitted as a ``json`` fence whose payload is an ARRAY of
 * ``{"tool": ..., "args": ...}`` entries (batched calls), or a single such
 * object. ``JSON_COMMAND_TOOL_FENCE_RE`` only covers ``{"command": <writer>}``,
 * so batched array payloads used to leak verbatim into the public narrative —
 * a model that narrates its plan as ```json [{"tool": "list_cwd"}, ...]``` had
 * the whole protocol block rendered as chat copy. */
const JSON_TOOL_ARRAY_FENCE_RE =
  /```(?:json|jsonc|json5)?\s*\n?\s*[[{][\s\S]*?"(?:tool|tool_name)"\s*:\s*"[^"]+"[\s\S]*?(?:```|$)/gi;
const BARE_INTERNAL_TOOL_PAYLOAD_RE =
  /^\s*(?:fs_writer|fs_writen|fs_written|write_file|write_text_file|edit_file|edit_text_file|str_replace|apply_patch|todo_write|todo_update|exec_shell|shell_command|run_command|web_search|fetch_url|web_fetch|read_file|read_text_file|glob_files|find_files|grep_text|list_cwd|search_capabilities|query_skill|browser_open|browser_get_content|artifact|present_files)\s*(?:\n|\()\s*[\s\S]*$/i;
const XML_TOOL_CALL_RE = /<tool_call>[\s\S]*?(?:<\/tool_call>|$)/gi;
const SEED_TOOL_CALL_RE =
  /<seed:tool_call\b[^>]*>[\s\S]*?(?:<\/seed:tool_call>|$)/gi;
const XML_TOOL_INVOCATION_RE =
  /<tool_invocation\b[^>]*(?:\/>|>[\s\S]*?(?:<\/tool_invocation>|$))/gi;
const XML_GENERIC_FUNCTION_CALL_RE =
  /<function(?:=[^>\s]+|\b[^>]*)>[\s\S]*?(?:<\/function>|$)/gi;
const XML_FUNCTION_CALL_RE =
  /<function=(?:fs_writer|fs_writen|fs_written|write_file|write_text_file|edit_text_file|str_replace|apply_patch|deep_research|web_search|search)>[\s\S]*?(?:<\/function>|$)/gi;
const INTERNAL_PROMPT_ENVELOPE_NAMES = [
  "original-user-request",
  "just-completed-evidence",
  "next-public-scope",
  "bounded-read-evidence",
] as const;
const INTERNAL_CONTROL_TAG_LINE_RE =
  /^\s*(?:<read_only>\s*<\/read_only>|<\/?read_only>)\s*$/i;
const INTERNAL_CONTROL_TAG_INLINE_RE =
  /<read_only>\s*<\/read_only>|<\/?read_only>/gi;
const INTERNAL_RENDERER_COMPONENT_TAG_INLINE_RE =
  /`?<\/?(?:TextBlock|ReasoningBlock|ToolCallBlock|ToolResultBlock|ThinkingBlock|ExecutionBlock)\b[^<>`]*>`?/g;
const REACT_ACTION_BLOCK_RE =
  /^\s*Action:\s*(?!(?:none|null|n\/a)\s*$).*?(?=\n\s*(?:Thought|Action|Observation|Final Answer):|\s*$)/gims;
const REACT_OBSERVATION_BLOCK_RE =
  /^\s*Observation:\s*[\s\S]*?(?=\n\s*(?:Thought|Action|Observation|Final Answer):|\s*$)/gim;
const SENSITIVE_ASSIGNMENT_RE =
  /\b(token|secret|api[_-]?key|password|authorization)\s*[:=]\s*([^\s,;]+)/gi;
const ROLE_NO_OUTPUT_PLACEHOLDER_RE =
  /^\s*\[[^\]\n]{1,80}\]\s*\((?:no output|no visible output|empty output)\)\s*$/i;
const NO_OUTPUT_PLACEHOLDER_RE =
  /^\s*\((?:no output|no visible output|empty output)\)\s*$/i;
const TEAM_ROLE_START_RE =
  /^\s*\[(?:planner|researcher|critic|arbiter|synthesizer|writer|reviewer|analyst|coder|designer|executor|tester)\]\s*starting\s*(?:[·•-]|\u00b7)\s*agent=[^\n]*\n*/i;
const TEAM_ROLE_PREFIX_RE =
  /^\s*\[(?:planner|researcher|critic|arbiter|synthesizer|writer|reviewer|analyst|coder|designer|executor|tester)\]\s*/i;
const NULLISH_PLACEHOLDER_RE = /^\s*(?:null|undefined|none|n\/a)\s*$/i;
const REPEATED_NULL_PLACEHOLDER_RE = /^\s*(?:null\s*)+$/i;

function stripLeakedTeamRoleNoise(content: string): string {
  return content
    .replace(TEAM_ROLE_START_RE, "")
    .replace(TEAM_ROLE_PREFIX_RE, "")
    .trim();
}

/**
 * Remove model-facing prompt envelopes that a provider echoed into public
 * assistant text. Process fenced code separately so documentation and code
 * examples remain literal. A dangling opening tag consumes the rest of that
 * non-code segment: truncated provider output must not expose half an internal
 * request or evidence packet while it is still streaming.
 */
function stripInternalPromptEnvelopes(content: string): string {
  const hadPromptEnvelope = new RegExp(
    `\\[(?:${INTERNAL_PROMPT_ENVELOPE_NAMES.join("|")})\\]`,
    "i",
  ).test(content);
  const cleanedContent = content
    .split(/(```[\s\S]*?(?:```|$))/g)
    .map((segment) => {
      if (segment.startsWith("```")) return segment;
      let cleaned = segment;
      for (const name of INTERNAL_PROMPT_ENVELOPE_NAMES) {
        const paired = new RegExp(
          `\\[${name}\\][\\s\\S]*?\\[\\/${name}\\]`,
          "gi",
        );
        const dangling = new RegExp(`\\[${name}\\][\\s\\S]*$`, "gi");
        const closing = new RegExp(`\\[\\/${name}\\]`, "gi");
        cleaned = cleaned
          .replace(paired, "")
          .replace(dangling, "")
          .replace(closing, "");
      }
      return cleaned.replace(
        /^\s*\[explicit-read-scope\][^\n]*(?:\n|$)/gim,
        "",
      );
    })
    .join("");
  // If removing an echoed packet leaves only a short provider label such as
  // "mock-echo:" or "Answer:", suppress the row entirely. Rendering that
  // shell creates an empty assistant bubble above the actual answer.
  if (
    hadPromptEnvelope &&
    /^[\p{L}\p{N}_ -]{0,40}[:：-]?\s*$/u.test(cleanedContent)
  ) {
    return "";
  }
  return cleanedContent;
}

export function stripLeakedRendererMarkup(
  content: string,
  options: { trim?: boolean } = {},
): string {
  let insideFence = false;
  const lines: string[] = [];
  for (const line of content.split(/\r?\n/)) {
    if (/^\s*```/.test(line)) {
      insideFence = !insideFence;
      lines.push(line);
      continue;
    }
    if (!insideFence && INTERNAL_CONTROL_TAG_LINE_RE.test(line)) continue;
    lines.push(
      insideFence
        ? line
        : line
            .replace(INTERNAL_CONTROL_TAG_INLINE_RE, "")
            .replace(INTERNAL_RENDERER_COMPONENT_TAG_INLINE_RE, "")
            .replace(/[ \t]{2,}/g, " ")
            .replace(/\s+([，。！？；：,.!?;:])/g, "$1"),
    );
  }
  const stripped = lines.join("\n").replace(/^\n+/, "");
  return options.trim === false ? stripped : stripped.trim();
}

/**
 * Repair failure prose persisted before terminal diagnostics were separated
 * from model-facing guard feedback.  Keep this deliberately narrow: ordinary
 * discussions of guards (for example in a code review) must remain intact.
 */
export function sanitizeLegacyGuardDiagnostic(content: string): string {
  const text = content.trim();
  const guardMatch = text.match(
    /(?:「|\b)([a-z][a-z0-9-]*(?:[ -][a-z0-9-]+)* guard)(?:」|\b)/i,
  );
  const isFailureDiagnostic =
    /任务未(?:能|完成)|这轮任务没有完成|质量提示|未通过证据门禁|最后一次拦截原因/i.test(
      text,
    ) ||
    (/(?:系统|system).{0,120}(?:拦截|阻止|拒绝|blocked|rejected)/i.test(text) &&
      /(?:我.{0,80}(?:回复|最终答案|收尾)|my final answer)/i.test(text));
  if (!guardMatch || !isFailureDiagnostic) return content;

  const label = guardMatch[1]?.toLowerCase() ?? "";
  const reason = label.includes("final-answer completeness")
    ? "收尾内容仍像进行中说明，尚未形成可交付结果。"
    : label.includes("todo-protocol")
      ? "任务清单状态与实际执行结果没有同步。"
      : label.includes("code-mode")
        ? "代码任务缺少可确认的修改或验证结果。"
        : "系统未能确认本轮已经形成可交付结果。";

  return [
    "这轮任务没有完成。我已停止重复尝试，并保留了当前进度。",
    `原因：${reason}`,
    "可以点击重试继续；如果仍失败，请补充必要的信息、权限或换一种执行方式。",
  ].join("\n\n");
}

function stripReactProtocol(content: string): string {
  let cleaned = content
    .replace(REACT_ACTION_BLOCK_RE, "")
    .replace(REACT_OBSERVATION_BLOCK_RE, "")
    .trim();
  const finalAnswer = cleaned.match(/(?:^|\n)Final Answer:\s*([\s\S]*)$/i);
  if (finalAnswer?.[1]?.trim()) {
    cleaned = finalAnswer[1].trim();
  }
  // Context compaction can prompt a model to emit an internal continuation
  // hand-off. It is useful for recovery, but it is not a user-facing answer
  // and often contains paths, iteration counters, or protocol vocabulary.
  cleaned = cleaned
    .replace(
      /(^|[\n。！？.!?])\s*(?:\*\*|__)?\s*(?:resume\s+state|continuation\s+note|恢复状态|恢复摘要)\s*:\s*(?:\*\*|__)?\s*[\s\S]*?(?=\n\s*\n|$)/im,
      "$1",
    )
    .trim();
  return cleaned
    .replace(
      /^\s*Thought:\s*[\s\S]*?(?=\n\s*Final Answer:|\n\s*Action:|$)/gim,
      "",
    )
    .replace(/^\s*Final Answer:\s*/gim, "")
    .replace(SENSITIVE_ASSIGNMENT_RE, "$1=[redacted]")
    .trim();
}

export function stripInternalToolProtocol(content: string): string {
  const cleaned = stripInternalPromptEnvelopes(content)
    .replace(INTERNAL_TOOL_FENCE_RE, "")
    .replace(JSON_COMMAND_TOOL_FENCE_RE, "")
    .replace(JSON_TOOL_ARRAY_FENCE_RE, "")
    .replace(SEED_TOOL_CALL_RE, "")
    .replace(XML_TOOL_CALL_RE, "")
    .replace(XML_TOOL_INVOCATION_RE, "")
    .replace(XML_GENERIC_FUNCTION_CALL_RE, "")
    .replace(XML_FUNCTION_CALL_RE, "")
    .replace(BARE_INTERNAL_TOOL_PAYLOAD_RE, "")
    .trim();
  const reactCleaned = stripReactProtocol(cleaned);
  if (!reactCleaned.startsWith("{")) return reactCleaned;

  try {
    const payload = JSON.parse(reactCleaned) as unknown;
    if (
      payload &&
      typeof payload === "object" &&
      "command" in payload &&
      typeof (payload as { command?: unknown }).command === "string" &&
      /^(?:fs_writer|fs_writen|fs_written|write_file|write_text_file|edit_text_file|str_replace|apply_patch)$/i.test(
        (payload as { command: string }).command,
      )
    ) {
      return "";
    }
  } catch {
    return reactCleaned;
  }

  return reactCleaned;
}

function visibleAIContent(content: string): string {
  const visible = stripLeakedTeamRoleNoise(
    stripLeakedRendererMarkup(stripInternalToolProtocol(content.trim())),
  );
  return ROLE_NO_OUTPUT_PLACEHOLDER_RE.test(visible) ||
    NO_OUTPUT_PLACEHOLDER_RE.test(visible) ||
    NULLISH_PLACEHOLDER_RE.test(visible) ||
    REPEATED_NULL_PLACEHOLDER_RE.test(visible)
    ? ""
    : visible;
}

export function extractContentFromMessage(message: Message) {
  if (typeof message.content === "string") {
    const content =
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim();
    return message.type === "ai" ? visibleAIContent(content) : content;
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) => {
        switch (content.type) {
          case "text":
            return message.type === "ai"
              ? visibleAIContent(content.text)
              : content.text.trim();
          case "image_url":
            const imageURL = extractURLFromImageURLContent(content.image_url);
            return `![image](${imageURL})`;
          // Extended-thinking blocks ("我将先检查…" style outward-facing
          // narration) render as normal body text when the grouping layer
          // decides they are user-facing, not collapsible inner thought.
          case "thinking": {
            const thinking =
              typeof content.thinking === "string"
                ? content.thinking
                : typeof content.text === "string"
                  ? content.text
                  : "";
            return thinking.trim();
          }
          default:
            return "";
        }
      })
      .join("\n")
      .trim();
  }
  return "";
}

export function isSettledAssistantAnswer(
  message: Message,
  {
    allowToolCalls = false,
    minTextLength = 1,
  }: {
    allowToolCalls?: boolean;
    minTextLength?: number;
  } = {},
): boolean {
  if (message.type !== "ai") return false;
  const metadata = message.additional_kwargs;
  if (
    metadata?.message_kind === "commentary" ||
    metadata?.public_progress === true ||
    metadata?.response_state === "interrupted" ||
    metadata?.response_state === "paused" ||
    metadata?.response_state === "cancelled" ||
    metadata?.response_state === "failed" ||
    metadata?.response_state === "blocked" ||
    metadata?.run_status === "streaming"
  ) {
    return false;
  }
  if (!allowToolCalls && hasToolCalls(message)) return false;
  return extractContentFromMessage(message).trim().length >= minTextLength;
}

export type AssistantTerminalState =
  | "paused"
  | "cancelled"
  | "interrupted"
  | "failed"
  | "blocked";

export function isAssistantStopTerminalState(
  state: AssistantTerminalState | null,
): boolean {
  return state === "cancelled" || state === "interrupted";
}

export function latestAssistantTerminalState(
  messages: Message[],
): AssistantTerminalState | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.type !== "ai") continue;
    const state = message.additional_kwargs?.response_state;
    if (
      state === "paused" ||
      state === "cancelled" ||
      state === "interrupted" ||
      state === "failed" ||
      state === "blocked"
    ) {
      return state;
    }
  }
  return null;
}

/**
 * Older saved turns predate `response_state: "blocked"` and therefore look
 * completed even when the final assistant message is explicitly handing the
 * turn back to the user. Keep this deliberately conservative: only inspect
 * the latest visible, non-process assistant answer and only match direct
 * requests for input or confirmation.
 */
export function assistantAnswerRequestsUserInput(messages: Message[]): boolean {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.type !== "ai") continue;
    if (
      message.additional_kwargs?.public_progress === true ||
      message.additional_kwargs?.message_kind === "commentary" ||
      hasToolCalls(message)
    ) {
      continue;
    }
    const content = extractContentFromMessage(message)
      .replace(/\s+/g, " ")
      .trim();
    if (!content) continue;
    return /(?:请|麻烦)(?:你)?(?:确认|提供|告诉|选择|回复|上传|授权|补充)|(?:需要|还需要)(?:你|您)(?:来)?(?:确认|提供|告诉|选择|回复|上传|授权|补充)|等待(?:你|您|用户)(?:的)?(?:输入|确认|回复)|(?:please|could you|can you)\s+(?:confirm|provide|tell|choose|reply|upload|authorize|share)|(?:i|we)\s+need\s+you\s+to\s+(?:confirm|provide|tell|choose|reply|upload|authorize|share)/i.test(
      content,
    );
  }
  return false;
}

export function extractReasoningContentFromMessage(message: Message) {
  if (message.type !== "ai") {
    return null;
  }
  if (
    message.additional_kwargs &&
    "reasoning_content" in message.additional_kwargs
  ) {
    return message.additional_kwargs.reasoning_content as string | null;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    if (part && "thinking" in part) {
      return part.thinking as string;
    }
  }
  if (typeof message.content === "string") {
    return splitInlineReasoning(message.content).reasoning;
  }
  return null;
}

export function removeReasoningContentFromMessage(message: Message) {
  if (message.type !== "ai" || !message.additional_kwargs) {
    return;
  }
  delete message.additional_kwargs.reasoning_content;
}

export function extractURLFromImageURLContent(
  content:
    | string
    | {
        url: string;
      },
) {
  if (typeof content === "string") {
    return content;
  }
  return content.url;
}

export function hasContent(message: Message) {
  if (typeof message.content === "string") {
    return extractContentFromMessage(message).length > 0;
  }
  if (Array.isArray(message.content)) {
    return extractContentFromMessage(message).length > 0;
  }
  return false;
}

export function isLikelyFinalAnswerContent(message: Message) {
  const messageKind = message.additional_kwargs?.message_kind;
  // Realtime protocol semantics are authoritative. This keeps a short answer
  // in one stable lane from its first streamed token and prevents commentary
  // from being promoted merely because it contains a heading or grows long.
  if (messageKind === "answer") return hasContent(message);
  if (messageKind === "commentary") return false;

  // Compatibility only for legacy/API messages that predate messageKind.
  const text = extractContentFromMessage(message);
  if (!text) return false;
  if (text.length > 320) return true;
  if (/^#{1,3}\s+\S+/m.test(text)) return true;
  if (/^\s*[一二三四五六七八九十]+[、.．]\s*\S+/m.test(text)) return true;
  if (/^\s*\d+[.)、]\s+\S+/m.test(text) && text.split(/\n+/).length >= 4) {
    return true;
  }
  if (/\|.+\|/.test(text) && /-{3,}/.test(text)) return true;
  return false;
}

export function hasReasoning(message: Message) {
  if (message.type !== "ai") {
    return false;
  }
  if (typeof message.additional_kwargs?.reasoning_content === "string") {
    return true;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    // Compatible with the Anthropic gateway
    return (part as unknown as { type: "thinking" })?.type === "thinking";
  }
  if (typeof message.content === "string") {
    return splitInlineReasoning(message.content).reasoning !== null;
  }
  return false;
}

export function hasToolCalls(message: Message) {
  if (message.type !== "ai") return false;
  const aiMsg = message as AIMessage;
  return aiMsg.tool_calls != null && aiMsg.tool_calls.length > 0;
}

export function hasPresentFiles(message: Message) {
  if (message.type !== "ai") return false;
  const aiMsg = message as AIMessage;
  return (
    aiMsg.tool_calls?.some((toolCall) => toolCall.name === "present_files") ??
    false
  );
}

export function isClarificationToolMessage(message: Message) {
  return (
    message.type === "tool" &&
    (message.name === "ask_clarification" ||
      message.name === "ask_user_question")
  );
}

export function extractPresentFilesFromMessage(message: Message) {
  if (message.type !== "ai" || !hasPresentFiles(message)) {
    return [];
  }
  const aiMsg = message as AIMessage;
  const files: string[] = [];
  for (const toolCall of aiMsg.tool_calls ?? []) {
    if (
      toolCall.name === "present_files" &&
      Array.isArray(toolCall.args.filepaths)
    ) {
      files.push(...(toolCall.args.filepaths as string[]));
    }
  }
  return files;
}

export function hasSubagent(message: Message) {
  if (message.type !== "ai") return false;
  const aiMsg = message as AIMessage;
  for (const toolCall of aiMsg.tool_calls ?? []) {
    if (toolCall.name === "task") {
      return true;
    }
  }
  return false;
}

export function findToolCallResult(toolCallId: string, messages: Message[]) {
  for (const message of messages) {
    if (
      message.type === "tool" &&
      (message as ToolMessage).tool_call_id === toolCallId
    ) {
      const content = extractTextFromMessage(message);
      if (content) {
        return content;
      }
    }
  }
  return undefined;
}

export function isHiddenFromUIMessage(message: Message) {
  return message.additional_kwargs?.hide_from_ui === true;
}

function isSupersededApprovalTimeoutMessage(
  message: Message,
  hasLaterSuccessfulAssistant: boolean,
) {
  if (message.type !== "ai") return false;
  const error = message.additional_kwargs?.error;
  if (!error || typeof error !== "object") return false;
  const text = extractTextFromMessage(message);
  if (
    !/timed out waiting for item\/commandExecution\/requestApproval/i.test(text)
  ) {
    return false;
  }
  return hasLaterSuccessfulAssistant;
}

/**
 * Represents a file stored in message additional_kwargs.files.
 * Used for optimistic UI (uploading state) and structured file metadata.
 */
export interface FileInMessage {
  filename: string;
  size: number; // bytes
  path?: string; // virtual path, may not be set during upload
  status?: "uploading" | "uploaded";
}

/**
 * Strip <uploaded_files> tag from message content.
 * Returns the content with the tag removed.
 */
export function stripUploadedFilesTag(content: string): string {
  return content
    .replace(/<uploaded_files>[\s\S]*?<\/uploaded_files>/g, "")
    .trim();
}

export function parseUploadedFiles(content: string): FileInMessage[] {
  // Match <uploaded_files>...</uploaded_files> tag
  const uploadedFilesRegex = /<uploaded_files>([\s\S]*?)<\/uploaded_files>/;

  const match = content.match(uploadedFilesRegex);

  if (!match) {
    return [];
  }

  const uploadedFilesContent = match[1];

  // Check if it's "No files have been uploaded yet."
  if (uploadedFilesContent?.includes("No files have been uploaded yet.")) {
    return [];
  }

  // Check if the backend reported no new files were uploaded in this message
  if (uploadedFilesContent?.includes("(empty)")) {
    return [];
  }

  // Parse file list
  // Format: - filename (size)\n  Path: /path/to/file
  const fileRegex = /- ([^\n(]+)\s*\(([^)]+)\)\s*\n\s*Path:\s*([^\n]+)/g;
  const files: FileInMessage[] = [];
  let fileMatch;

  while ((fileMatch = fileRegex.exec(uploadedFilesContent ?? "")) !== null) {
    const name = fileMatch[1]?.trim();
    const sizeRaw = fileMatch[2]?.trim();
    const path = fileMatch[3]?.trim();
    if (!name || !path) continue;
    files.push({
      filename: name,
      size: parseInt(sizeRaw ?? "", 10) || 0,
      path,
    });
  }

  return files;
}
