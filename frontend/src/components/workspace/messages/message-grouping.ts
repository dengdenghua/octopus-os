/**
 * Front-end aggregation of continuous tool_call activity into
 * collapsible summary bubbles. See `CollapsibleActivityGroup` for the UI.
 *
 * The function is purposefully pure and free of React deps so it can be
 * unit-tested with `node --test` (see `message-grouping.test.ts`).
 */
import { swallow } from "@/core/utils/log";
import type {
  AIMessage,
  Message,
  MessageContent,
  ToolCall,
  ToolMessage,
} from "@/core/api/types";

import type { ActivityItem, ActivityKind } from "./collapsible-activity-group";
import {
  isFileMutationToolName,
  isReadToolName,
  isShellToolName,
} from "../tool-name-groups";

// `import type` is erased at build time so there is no runtime dependency
// on the React component above — this module stays pure and testable under
// `node --test --experimental-strip-types`.

/** Minimum number of consecutive tool_calls required to aggregate into
 *  a single collapsible bubble. Singles fall back to the normal render path
 *  so we don't hide trivial single-step interactions behind an extra click. */
export const MIN_AGGREGATION_SIZE = 2;

const PLAN_TOOL_NAME_HINTS = ["plan", "execution_plan"];

// Tool names that should NEVER be aggregated away — they have bespoke UI
// or side-effects (approval flow, subagent spawning, present_files).
const PASSTHROUGH_TOOLS = new Set([
  "task",
  "present_files",
  "ask_clarification",
  "ask_user_question",
  "write_todos",
]);

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------

function isPlanTool(name: string): boolean {
  const lower = name.toLowerCase();
  return PLAN_TOOL_NAME_HINTS.some((hint) => lower.includes(hint));
}

/**
 * Anthropic extended-thinking content part. NOT in `MessageContentComplex`
 * (see `core/api/types.ts`); arrives untyped from the upstream LLM stream.
 * Exported so the test file can exercise the predicate in isolation.
 */
export interface ThinkingContentPart {
  type: "thinking";
  thinking?: string;
}

export function isThinkingContentPart(
  part: unknown,
): part is ThinkingContentPart {
  if (typeof part !== "object" || part === null) return false;
  return (part as { type?: unknown }).type === "thinking";
}

/**
 * Pull every thinking block out of an `AIMessage.content` and join them
 * with newlines. Returns "" for string content (the OpenAI simple case).
 */
export function extractThinkingText(content: MessageContent): string {
  if (!Array.isArray(content)) return "";
  return content
    .filter(isThinkingContentPart)
    .map((c) => (c as unknown as ThinkingContentPart).thinking ?? "")
    .join("\n")
    .trim();
}

// First-person plan narration ("我将先检查目录结构…", "接下来我会查看
// config") reads as user-facing prose — the model is telling the user what
// it is about to do. True chain-of-thought ("用户可能期望 X 但 Y 成本更高")
// has no first-person action intro and stays collapsible. Deliberately
function classifyToolCall(name: string): ActivityKind | "passthrough" {
  if (PASSTHROUGH_TOOLS.has(name)) return "passthrough";
  if (isPlanTool(name)) return "plan";
  if (isFileMutationToolName(name)) return "file_ops";
  if (isShellToolName(name) || isReadToolName(name)) return "tool_calls";
  return "passthrough";
}

// ---------------------------------------------------------------------------
// Label builders
// ---------------------------------------------------------------------------

function basename(path: string): string {
  if (!path) return path;
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function truncate(input: string, max: number): string {
  if (input.length <= max) return input;
  return input.slice(0, max) + "...";
}

function parseResultJSON(result: string): unknown {
  try {
    return JSON.parse(result);
  } catch (e) {
    swallow(e);
    return null;
  }
}

interface LineCounts {
  added?: number;
  removed?: number;
}

function extractLineCounts(
  args: Record<string, unknown>,
  result: string | undefined,
): LineCounts {
  const counts: LineCounts = {};

  const pickNumber = (source: unknown, key: string): number | undefined => {
    if (!source || typeof source !== "object") return undefined;
    const value = (source as Record<string, unknown>)[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
      const parsed = Number.parseInt(value, 10);
      if (Number.isFinite(parsed)) return parsed;
    }
    return undefined;
  };

  const addedKeys = ["lines_added", "added_lines", "new_lines", "additions"];
  const removedKeys = [
    "lines_removed",
    "removed_lines",
    "deleted_lines",
    "deletions",
  ];

  for (const key of addedKeys) {
    const n = pickNumber(args, key);
    if (n !== undefined) {
      counts.added = n;
      break;
    }
  }
  for (const key of removedKeys) {
    const n = pickNumber(args, key);
    if (n !== undefined) {
      counts.removed = n;
      break;
    }
  }

  // Infer from args.content length for full-file writes when we didn't
  // find an explicit counter.
  if (counts.added === undefined && typeof args.content === "string") {
    counts.added = args.content.split(/\r?\n/).length;
  }

  // Best-effort parse of the tool result (often JSON with { lines_added, ... }).
  if (result) {
    const parsed = parseResultJSON(result);
    if (parsed && typeof parsed === "object") {
      for (const key of addedKeys) {
        if (counts.added !== undefined) break;
        const n = pickNumber(parsed, key);
        if (n !== undefined) counts.added = n;
      }
      for (const key of removedKeys) {
        if (counts.removed !== undefined) break;
        const n = pickNumber(parsed, key);
        if (n !== undefined) counts.removed = n;
      }
    }
  }

  return counts;
}

/**
 * Extract the raw unified diffs produced by a file-mutation tool call so
 * the activity view can render the +/- lines (red/green) mid-turn, like
 * Claude's edit blocks. Sources, in order:
 *
 *   1. ``args.changes[].diff`` — edit_file's structured changes carry the
 *      full unified diff per file (same source MessageOutputSummary uses).
 *   2. The tool result JSON's ``changes[].diff`` — fallback for tools that
 *      echo the changes back in their result.
 *
 * Returns one entry per file; empty when no diff is available.
 */
function extractToolCallDiffs(
  args: Record<string, unknown>,
  result: string | undefined,
): string[] {
  const out: string[] = [];
  const pushFrom = (source: unknown): void => {
    if (!source || typeof source !== "object") return;
    const changes = (source as Record<string, unknown>).changes;
    if (!Array.isArray(changes)) return;
    for (const raw of changes) {
      if (!raw || typeof raw !== "object") continue;
      const diff = (raw as Record<string, unknown>).diff;
      if (typeof diff === "string" && diff.trim()) out.push(diff);
    }
  };
  pushFrom(args);
  if (!out.length && result) {
    const parsed = parseResultJSON(result);
    if (parsed && typeof parsed === "object") pushFrom(parsed);
  }
  return out;
}

/**
 * Shape of the label bag consumers pass to localize activity labels.
 * Mirrors the `messageGrouping` section of the Translations interface.
 * When the label bag is omitted we fall back to English hardcodes so
 * the pure unit tests (node --test) keep working without mocking i18n.
 */
export interface MessageGroupingLabels {
  fileFallback: string;
  writeFile: (file: string) => string;
  writeFileWithLines: (file: string, added: number) => string;
  editFile: (file: string) => string;
  editFileAddRemove: (file: string, added: number, removed: number) => string;
  editFileAdded: (file: string, added: number) => string;
  editFileRemoved: (file: string, removed: number) => string;
  executeCommand: string;
  executeCommandWith: (cmd: string) => string;
  planStep: string;
  think: string;
  searchSources?: string;
  readFile?: string;
  readWebpage?: string;
}

const DEFAULT_LABELS: MessageGroupingLabels = {
  fileFallback: "file",
  writeFile: (f) => `Write ${f}`,
  writeFileWithLines: (f, added) => `Write ${f} +${added} lines`,
  editFile: (f) => `Edit ${f}`,
  editFileAddRemove: (f, added, removed) => `Edit ${f} (+${added} -${removed})`,
  editFileAdded: (f, added) => `Edit ${f} +${added} lines`,
  editFileRemoved: (f, removed) => `Edit ${f} -${removed} lines`,
  executeCommand: "Run checks",
  executeCommandWith: () => "Run checks",
  planStep: "Plan step",
  think: "Thinking",
  searchSources: "Search sources",
  readFile: "Read file",
  readWebpage: "Read webpage",
};

function buildFileOpLabel(
  name: string,
  args: Record<string, unknown>,
  counts: LineCounts,
  labels: MessageGroupingLabels,
): string {
  const path =
    (args.path as string | undefined) ??
    (args.filepath as string | undefined) ??
    (args.file_path as string | undefined) ??
    (args.filename as string | undefined);
  const file = path ? basename(path) : labels.fileFallback;

  const hasAdded = counts.added !== undefined && counts.added !== 0;
  const hasRemoved = counts.removed !== undefined && counts.removed !== 0;

  if (
    name === "write_file" ||
    name === "write_text_file" ||
    name === "create_file"
  ) {
    if (hasAdded) return labels.writeFileWithLines(file, counts.added!);
    return labels.writeFile(file);
  }
  // edit / str_replace / apply_patch
  if (hasAdded && hasRemoved) {
    return labels.editFileAddRemove(file, counts.added!, counts.removed!);
  }
  if (hasAdded) return labels.editFileAdded(file, counts.added!);
  if (hasRemoved) return labels.editFileRemoved(file, counts.removed!);
  return labels.editFile(file);
}

function buildShellLabel(
  args: Record<string, unknown>,
  labels: MessageGroupingLabels,
): string {
  const explicitSummary =
    typeof args.description === "string"
      ? args.description.trim()
      : typeof args.label === "string"
        ? args.label.trim()
        : typeof args.title === "string"
          ? args.title.trim()
          : "";
  return explicitSummary
    ? labels.executeCommandWith(truncate(explicitSummary, 40))
    : labels.executeCommand;
}

const SENSITIVE_LABEL_VALUE_RE =
  /(sk-[\w-]+|token|secret|credential|password|passwd|api[_-]?key|bearer\s+[a-z0-9._-]+|id_rsa|id_ed25519|\.pem\b|\.key\b)/i;

function safeTargetLabel(value: string): string {
  const text = value.trim();
  if (!text || SENSITIVE_LABEL_VALUE_RE.test(text)) return "";
  if (/^https?:\/\//i.test(text)) {
    try {
      return new URL(text).hostname || text;
    } catch {
      return truncate(text, 48);
    }
  }
  if (/[\\/]/.test(text)) return basename(text);
  return truncate(text, 48);
}

function buildReadLabel(
  name: string,
  args: Record<string, unknown>,
  labels: MessageGroupingLabels,
): string {
  // First string-ish arg (path / pattern / query).
  const keys = ["path", "url", "pattern", "query", "filepath", "file_path"];
  let value: string | undefined;
  for (const key of keys) {
    const v = args[key];
    if (typeof v === "string" && v.length > 0) {
      value = v;
      break;
    }
  }
  const target = value ? safeTargetLabel(value) : "";
  const lower = name.toLowerCase();
  const action =
    lower.includes("search") ||
    lower.includes("grep") ||
    lower.includes("glob") ||
    lower === "find"
      ? (labels.searchSources ?? DEFAULT_LABELS.searchSources!)
      : lower.includes("web") || lower.includes("fetch")
        ? (labels.readWebpage ?? DEFAULT_LABELS.readWebpage!)
        : (labels.readFile ?? DEFAULT_LABELS.readFile!);
  return target ? `${action}: ${target}` : action;
}

function buildPlanLabel(
  args: Record<string, unknown>,
  labels: MessageGroupingLabels,
): string {
  const title =
    (args.title as string | undefined) ??
    (args.description as string | undefined) ??
    (args.step as string | undefined) ??
    labels.planStep;
  return truncate(title, 60);
}

// ---------------------------------------------------------------------------
// Result lookup helpers
// ---------------------------------------------------------------------------

function findToolResultText(
  messages: Message[],
  toolCallId: string | undefined,
): string | undefined {
  if (!toolCallId) return undefined;
  for (const msg of messages) {
    if (
      msg.type === "tool" &&
      (msg as ToolMessage).tool_call_id === toolCallId
    ) {
      if (typeof msg.content === "string") return msg.content;
      if (Array.isArray(msg.content)) {
        return msg.content
          .map((part) => (part.type === "text" ? part.text : ""))
          .join("");
      }
    }
  }
  return undefined;
}

function findToolResultStatus(
  messages: Message[],
  toolCallId: string | undefined,
): ActivityItem["status"] {
  if (!toolCallId) return "running";
  for (const msg of messages) {
    if (
      msg.type === "tool" &&
      (msg as ToolMessage).tool_call_id === toolCallId
    ) {
      const status = (msg as ToolMessage).status;
      if (status === "error") return "error";
      return "done";
    }
  }
  return "running";
}

// ---------------------------------------------------------------------------
// Public types and API
// ---------------------------------------------------------------------------

export type ActivityChunk =
  | {
      kind: "activity";
      activityKind: ActivityKind;
      items: ActivityItem[];
      /** First message id contributing to this chunk (stable React key). */
      id: string;
      /** Indexes in the original messages array that were consumed. */
      messageIndexes: number[];
    }
  | {
      kind: "passthrough";
      /** Indexes in the original messages array that should be rendered
       *  via the existing pipeline. */
      messageIndexes: number[];
      id: string;
    };

/**
 * Build an `ActivityItem` from a single AI tool_call and its tool result.
 * Returns null when the tool is classified as passthrough.
 */
function toActivityItem(
  aiMsg: AIMessage,
  toolCall: ToolCall,
  messages: Message[],
  labels: MessageGroupingLabels,
): { item: ActivityItem; kind: ActivityKind } | null {
  const classification = classifyToolCall(toolCall.name);
  if (classification === "passthrough") return null;

  const resultText = findToolResultText(messages, toolCall.id);
  const status = findToolResultStatus(messages, toolCall.id);

  const id = toolCall.id ?? `${aiMsg.id ?? "msg"}-${toolCall.name}`;
  const args = toolCall.args ?? {};

  if (classification === "file_ops") {
    const counts = extractLineCounts(args, resultText);
    const label = buildFileOpLabel(toolCall.name, args, counts, labels);
    // Carry the raw unified diffs (from the tool-call args, e.g.
    // edit_file's `changes[].diff`) so the activity view can render the
    // +/- lines in red/green mid-turn, like Claude's edit blocks. Each
    // entry is one file's diff.
    const diffs = extractToolCallDiffs(args, resultText);
    return {
      item: {
        id,
        label,
        status,
        meta: {
          tool_name: toolCall.name,
          lines_added: counts.added ?? 0,
          lines_removed: counts.removed ?? 0,
          ...(diffs.length ? { diffs } : {}),
        },
      },
      kind: "file_ops",
    };
  }

  if (classification === "plan") {
    return {
      item: {
        id,
        label: buildPlanLabel(args, labels),
        status,
        meta: { tool_name: toolCall.name },
      },
      kind: "plan",
    };
  }

  // tool_calls (shell + read)
  let label: string;
  if (isShellToolName(toolCall.name)) {
    label = buildShellLabel(args, labels);
  } else {
    label = buildReadLabel(toolCall.name, args, labels);
  }
  return {
    item: {
      id,
      label,
      status,
      meta: { tool_name: toolCall.name },
    },
    kind: "tool_calls",
  };
}

/**
 * Walk the messages and produce an ordered list of chunks. Each tool-call
 * message produces either activity items (possibly a run of several) or a
 * passthrough segment. Consecutive activity items of the same kind merge
 * into a single chunk; different kinds or non-tool-call messages cut the
 * run. Tool result messages are consumed alongside their AI owner.
 *
 * @param messages Thread messages in emission order.
 * @param minSize Minimum consecutive items to fold — smaller runs fall back
 *  to passthrough so we don't hide singletons behind a click. Defaults to
 *  `MIN_AGGREGATION_SIZE` (2).
 */
export function groupActivities(
  messages: Message[],
  minSize: number = MIN_AGGREGATION_SIZE,
  labels: MessageGroupingLabels = DEFAULT_LABELS,
): ActivityChunk[] {
  const chunks: ActivityChunk[] = [];

  type ActivityAccumulator = {
    kind: ActivityKind;
    items: ActivityItem[];
    messageIndexes: number[];
    id: string;
  };
  type PassthroughAccumulator = {
    messageIndexes: number[];
    id: string;
  };

  let currentActivity: ActivityAccumulator | null = null;
  let currentPassthrough: PassthroughAccumulator | null = null;

  const flushPassthrough = () => {
    if (currentPassthrough && currentPassthrough.messageIndexes.length > 0) {
      chunks.push({ kind: "passthrough", ...currentPassthrough });
    }
    currentPassthrough = null;
  };

  const flushActivity = () => {
    if (!currentActivity) return;
    if (currentActivity.items.length >= minSize) {
      chunks.push({
        kind: "activity",
        activityKind: currentActivity.kind,
        items: currentActivity.items,
        messageIndexes: currentActivity.messageIndexes,
        id: currentActivity.id,
      });
    } else {
      // Fall back to passthrough — don't hide singletons.
      if (!currentPassthrough) {
        currentPassthrough = {
          messageIndexes: [...currentActivity.messageIndexes],
          id: currentActivity.id,
        };
      } else {
        currentPassthrough.messageIndexes.push(
          ...currentActivity.messageIndexes,
        );
      }
    }
    currentActivity = null;
  };

  const pushPassthrough = (index: number, id: string) => {
    flushActivity();
    if (!currentPassthrough) {
      currentPassthrough = { messageIndexes: [index], id };
    } else {
      currentPassthrough.messageIndexes.push(index);
    }
  };

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (!msg) continue;

    // Tool result messages: treat as "joined" to whatever chunk the
    // owning AI message landed in. Find owner by tool_call_id.
    if (msg.type === "tool") {
      const toolMsg = msg as ToolMessage;
      let ownerIndex = -1;
      for (let j = i - 1; j >= 0; j--) {
        const cand = messages[j];
        if (cand?.type === "ai") {
          const ai = cand as AIMessage;
          if (ai.tool_calls?.some((tc) => tc.id === toolMsg.tool_call_id)) {
            ownerIndex = j;
            break;
          }
        }
      }
      let attached = false;
      if (ownerIndex >= 0) {
        if (
          currentActivity !== null &&
          currentActivity.messageIndexes.includes(ownerIndex)
        ) {
          currentActivity.messageIndexes.push(i);
          attached = true;
        } else if (
          currentPassthrough !== null &&
          (
            currentPassthrough as PassthroughAccumulator
          ).messageIndexes.includes(ownerIndex)
        ) {
          (currentPassthrough as PassthroughAccumulator).messageIndexes.push(i);
          attached = true;
        }
      }
      if (!attached) {
        // Orphan / unmatched — route through passthrough.
        pushPassthrough(i, msg.id ?? `tool-${i}`);
      }
      continue;
    }

    if (msg.type !== "ai") {
      pushPassthrough(i, msg.id ?? `msg-${i}`);
      continue;
    }

    const aiMsg = msg as AIMessage;

    // Check for extended-thinking style "reasoning" block. If the message
    // carries a thinking block and no tool calls, treat as `think` activity.
    const isThinkOnly =
      Array.isArray(aiMsg.content) &&
      aiMsg.content.length > 0 &&
      (aiMsg.content[0] as { type?: string }).type === "thinking" &&
      (!aiMsg.tool_calls || aiMsg.tool_calls.length === 0);

    if (isThinkOnly) {
      const text = extractThinkingText(aiMsg.content);
      // A thinking content block remains reasoning regardless of wording.
      // Public progress must arrive through the commentary protocol; text
      // such as "接下来我会…" is not evidence of public visibility.
      if (!currentActivity || currentActivity.kind !== "think") {
        flushActivity();
        flushPassthrough();
        currentActivity = {
          kind: "think",
          items: [],
          messageIndexes: [],
          id: aiMsg.id ?? `think-${i}`,
        };
      }
      currentActivity.items.push({
        id: aiMsg.id ?? `think-${i}`,
        label: truncate(text || labels.think, 80),
        meta: { duration_seconds: 0 },
        status: "done",
      });
      currentActivity.messageIndexes.push(i);
      continue;
    }

    if (!aiMsg.tool_calls || aiMsg.tool_calls.length === 0) {
      pushPassthrough(i, msg.id ?? `msg-${i}`);
      continue;
    }

    // This message has tool calls. Check whether ALL of them classify to
    // the same activity kind — if so we can aggregate; if there are any
    // passthrough / mixed kinds, we fall through to passthrough to avoid
    // dropping bespoke UI like tool approvals or subagent cards.
    const perCall = aiMsg.tool_calls.map((tc) =>
      toActivityItem(aiMsg, tc, messages, labels),
    );

    const allNull = perCall.every((p) => p === null);
    const anyNull = perCall.some((p) => p === null);
    if (anyNull) {
      // Mixed message — keep the original rendering path intact.
      pushPassthrough(i, msg.id ?? `msg-${i}`);
      continue;
    }
    if (allNull) {
      pushPassthrough(i, msg.id ?? `msg-${i}`);
      continue;
    }

    // All tool calls in this message classify — they must share a kind to
    // go into a single activity run. (Extremely rare for a single AI
    // message to emit mixed kinds; if it does, we passthrough.)
    const kinds = new Set(perCall.map((p) => p!.kind));
    if (kinds.size !== 1) {
      pushPassthrough(i, msg.id ?? `msg-${i}`);
      continue;
    }
    const kind = perCall[0]!.kind;

    // Begin or extend an activity run of this kind. When transitioning
    // from a passthrough span (e.g. the initial human message) we must
    // flush the passthrough first so ordering is preserved.
    if (!currentActivity || currentActivity.kind !== kind) {
      flushActivity();
      flushPassthrough();
      currentActivity = {
        kind,
        items: [],
        messageIndexes: [],
        id: aiMsg.id ?? `activity-${i}`,
      };
    }
    for (const entry of perCall) {
      currentActivity.items.push(entry!.item);
    }
    currentActivity.messageIndexes.push(i);
  }

  flushActivity();
  flushPassthrough();

  // Merge adjacent passthrough chunks — these arise when a failed
  // activity (below MIN_AGGREGATION_SIZE) falls back after we had
  // already flushed the preceding passthrough to preserve ordering.
  const merged: ActivityChunk[] = [];
  for (const chunk of chunks) {
    const prev = merged[merged.length - 1];
    if (chunk.kind === "passthrough" && prev && prev.kind === "passthrough") {
      prev.messageIndexes.push(...chunk.messageIndexes);
    } else {
      merged.push(chunk);
    }
  }

  return merged;
}
