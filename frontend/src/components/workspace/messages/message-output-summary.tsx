import type {
  AIMessage,
  Message,
  ToolCall,
  ToolMessage,
} from "@/core/api/types";
import type { FileHunk } from "@/core/realtime";
import { singleHunkDiff } from "@/components/realtime/item-views/file-change-view";
import {
  artifactDisplayPath,
  normalizeWorkspaceArtifactRef,
} from "@/core/artifacts/utils";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import {
  stripInternalToolProtocol,
  stripLeakedRendererMarkup,
  stripUploadedFilesTag,
} from "@/core/messages/utils";
import { swallow } from "@/core/utils/log";
import {
  getFileExtensionDisplayName,
  getFileIcon,
  getFileName,
} from "@/core/utils/files";
import { cn } from "@/lib/utils";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
  FilePenLineIcon,
  FilesIcon,
  LinkIcon,
  Loader2Icon,
  WandSparklesIcon,
  XCircleIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { useArtifacts } from "../artifacts";
import { emitOpenAgentWorkbench } from "../agent-workbench-events";
import { isInternalWorkingFilePath } from "../agent-workbench-utils";
import { RoutedWebLink } from "@/components/ui/routed-web-link";

type OutputArtifact = {
  path: string;
  title?: string | null;
  kind?: string | null;
};

type OutputChange = {
  created: boolean;
  diff?: string;
  diffTruncated: boolean;
  path: string;
  op?: string | null;
  added: number;
  removed: number;
  hunks: FileHunk[];
};

type VerificationEntry = {
  id: string;
  toolName: string;
  passed: boolean;
  detail?: string;
};

const VERIFICATION_DETAIL_SECRET_RE =
  /\b(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[baprs])[-_][A-Za-z0-9]{8,}\b|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}|\b(?:Bearer|Authorization:?)\s+[A-Za-z0-9._-]{10,}|(["']?(?:api[_-]?key|secret|password|passwd|token)["']?\s*[:=]\s*)["']?[^\s"',}]{4,}/gi;
const VERIFICATION_RAW_TOOL_RE =
  /\b(?:read_file|exec_shell|shell_command|run_command|todo_write|apply_patch|write_file|edit_file|str_replace)\b/gi;
const VERIFICATION_PROTOCOL_PREFIX_RE =
  /^\s*(?:Thought|Action|Observation|Final Answer|Tool|Tool Result)\s*:\s*/gim;

function sanitizeVerificationText(value: string): string {
  return stripLeakedRendererMarkup(stripInternalToolProtocol(value), {
    trim: true,
  })
    .replace(VERIFICATION_DETAIL_SECRET_RE, (_match, prefix?: string) =>
      prefix ? `${prefix}«redacted»` : "«redacted»",
    )
    .replace(VERIFICATION_RAW_TOOL_RE, "operation")
    .replace(VERIFICATION_PROTOCOL_PREFIX_RE, "")
    .trim();
}

type OutputSummary = {
  artifacts: OutputArtifact[];
  changes: OutputChange[];
  verifications: VerificationEntry[];
};

export type FailurePresentation = {
  message: string;
  detail: string;
  /** A later user turn completed successfully, so this is historical audit. */
  resolved?: boolean;
  kind:
    | "error"
    | "network"
    | "verification"
    | "guard"
    | "lifecycle"
    | "environment"
    | "blocked";
  code?: string;
  eventId?: string;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function diffCounts(diff: unknown): { added: number; removed: number } {
  if (typeof diff !== "string" || diff.length === 0) {
    return { added: 0, removed: 0 };
  }
  let added = 0;
  let removed = 0;
  for (const line of diff.split(/\r?\n/)) {
    if (line.startsWith("+++") || line.startsWith("---")) continue;
    if (line.startsWith("+")) added += 1;
    else if (line.startsWith("-")) removed += 1;
  }
  return { added, removed };
}

function isCreatedFileChange(
  op: string | null,
  diff: unknown,
  counts: { added: number; removed: number },
) {
  const normalizedOp = op?.toLowerCase() ?? null;
  if (
    normalizedOp &&
    [
      "add",
      "added",
      "create",
      "created",
      "generate",
      "generated",
      "new",
    ].includes(normalizedOp)
  ) {
    return true;
  }
  if (typeof diff === "string") {
    return (
      /^---\s+\/dev\/null/m.test(diff) ||
      /^new file mode\b/m.test(diff) ||
      /^@@\s+-0,0\s+\+\d+(?:,\d+)?\s+@@/m.test(diff)
    );
  }
  return !normalizedOp && counts.added > 0 && counts.removed === 0;
}

function isFinalOutputArtifactPath(path: string): boolean {
  const normalized = path.replaceAll("\\", "/").toLowerCase();
  return (
    !isInternalWorkingFilePath(path) &&
    (normalized.includes("/output/final/") ||
      normalized.startsWith("output/final/"))
  );
}

function changeDisplayPath(path: string): string {
  const displayPath = artifactDisplayPath(path);
  const normalized = path.replaceAll("\\", "/").toLowerCase();
  const isOutputPath =
    normalized.includes("/output/final/") ||
    normalized.startsWith("output/final/");
  return isOutputPath ? getFileName(displayPath) : displayPath;
}

function artifactFromToolCall(toolCall: ToolCall): OutputArtifact | null {
  if (toolCall.name !== "artifact") return null;
  const path = toolCall.args.path;
  if (typeof path !== "string" || !path.trim()) return null;
  return {
    path,
    title: typeof toolCall.args.title === "string" ? toolCall.args.title : null,
    kind: typeof toolCall.args.kind === "string" ? toolCall.args.kind : null,
  };
}

function hunksFromChange(change: Record<string, unknown>): FileHunk[] {
  if (!Array.isArray(change.hunks)) return [];
  const out: FileHunk[] = [];
  for (const raw of change.hunks) {
    const hunk = asRecord(raw);
    if (!hunk) continue;
    if (typeof hunk.id !== "string" || typeof hunk.body !== "string") continue;
    out.push({
      id: hunk.id,
      oldStart: typeof hunk.oldStart === "number" ? hunk.oldStart : 0,
      oldLines: typeof hunk.oldLines === "number" ? hunk.oldLines : 0,
      newStart: typeof hunk.newStart === "number" ? hunk.newStart : 0,
      newLines: typeof hunk.newLines === "number" ? hunk.newLines : 0,
      body: hunk.body,
      decision:
        hunk.decision === "accepted" || hunk.decision === "rejected"
          ? hunk.decision
          : "pending",
    });
  }
  return out;
}

function changesFromToolCall(toolCall: ToolCall): OutputChange[] {
  if (
    toolCall.name !== "file_change" ||
    !Array.isArray(toolCall.args.changes)
  ) {
    return [];
  }
  const out: OutputChange[] = [];
  for (const raw of toolCall.args.changes) {
    const change = asRecord(raw);
    if (!change) continue;
    const path = change?.path;
    if (typeof path !== "string" || !path.trim()) continue;
    const op = typeof change.op === "string" ? change.op : null;
    const counts = diffCounts(change.diff);
    out.push({
      created: isCreatedFileChange(op, change.diff, counts),
      diff: typeof change.diff === "string" ? change.diff : undefined,
      diffTruncated: change.diffTruncated === true,
      path,
      op,
      added: counts.added,
      removed: counts.removed,
      hunks: hunksFromChange(change),
    });
  }
  return out;
}

const VERIFICATION_TOOL_NAMES = new Set([
  "run_tests",
  "lint_check",
  "test",
  "verify",
  "build",
  "format_code",
]);

function isVerificationToolName(name: string): boolean {
  return (
    VERIFICATION_TOOL_NAMES.has(name) ||
    /^(?:verify|verification|check|test|lint|build)[_-]/i.test(name)
  );
}

function verificationFromToolMessage(
  toolMessage: ToolMessage,
  toolCallMap: Map<string, ToolCall>,
): VerificationEntry | null {
  const toolCall = toolCallMap.get(toolMessage.tool_call_id);
  if (!toolCall || !isVerificationToolName(toolCall.name)) return null;

  // Try to parse structured result from content
  const content =
    typeof toolMessage.content === "string"
      ? toolMessage.content
      : Array.isArray(toolMessage.content)
        ? toolMessage.content
            .map((c) => (typeof c === "string" ? c : ""))
            .join("")
        : "";

  let result: Record<string, unknown> | null = null;
  try {
    const trimmed = content.trim();
    if (trimmed.startsWith("{")) {
      result = JSON.parse(trimmed);
    } else {
      const jsonMatch = trimmed.match(/\{[\s\S]*\}/);
      if (jsonMatch) result = JSON.parse(jsonMatch[0]);
    }
  } catch {
    // Not JSON; fall back to status field
  }

  const passed =
    toolMessage.status === "success" ||
    result?.success === true ||
    result?.exit_code === 0;

  const rawDetail =
    typeof result?.error === "string"
      ? result.error
      : typeof result?.stdout === "string"
        ? result.stdout.slice(0, 200)
        : "";
  const detail = rawDetail ? sanitizeVerificationText(rawDetail) : undefined;

  return {
    id: toolMessage.tool_call_id,
    toolName: toolCall.name,
    passed,
    detail,
  };
}

// Per-message extraction caches. Message objects are immutable and keep
// reference identity across streaming frames (realtime-adapter), so the
// expensive parsing (diff splitting, hunk extraction, JSON.parse of tool
// results) runs once per message instead of once per summarizeOutputs call.
// The merge/accumulate step stays in summarizeOutputs, keeping ordering and
// accumulation semantics identical.
type ToolCallOutputs = {
  toolCalls: ToolCall[];
  artifacts: OutputArtifact[];
  changes: OutputChange[];
};

const toolCallOutputsCache = new WeakMap<Message, ToolCallOutputs>();

function toolCallOutputsFromMessage(message: Message): ToolCallOutputs {
  const cached = toolCallOutputsCache.get(message);
  if (cached) return cached;
  const extracted: ToolCallOutputs = {
    toolCalls: [],
    artifacts: [],
    changes: [],
  };
  if (message.type === "ai") {
    for (const toolCall of (message as AIMessage).tool_calls ?? []) {
      extracted.toolCalls.push(toolCall);
      const artifact = artifactFromToolCall(toolCall);
      if (artifact) extracted.artifacts.push(artifact);
      extracted.changes.push(...changesFromToolCall(toolCall));
    }
  }
  toolCallOutputsCache.set(message, extracted);
  return extracted;
}

type VerificationCacheEntry = {
  toolCall: ToolCall | undefined;
  entry: VerificationEntry | null;
};

const verificationEntryCache = new WeakMap<Message, VerificationCacheEntry>();

function verificationFromToolMessageCached(
  toolMessage: ToolMessage,
  toolCallMap: Map<string, ToolCall>,
): VerificationEntry | null {
  const toolCall = toolCallMap.get(toolMessage.tool_call_id);
  const cached = verificationEntryCache.get(toolMessage);
  if (cached && cached.toolCall === toolCall) return cached.entry;
  const entry = verificationFromToolMessage(toolMessage, toolCallMap);
  verificationEntryCache.set(toolMessage, { toolCall, entry });
  return entry;
}

function summarizeOutputs(messages: Message[]): OutputSummary {
  const artifacts = new Map<string, OutputArtifact>();
  const changes = new Map<string, OutputChange>();
  const verifications = new Map<string, VerificationEntry>();
  const toolCallMap = new Map<string, ToolCall>();

  // First pass: collect tool calls by id so we can join with ToolMessage results
  for (const message of messages) {
    const extracted = toolCallOutputsFromMessage(message);
    for (const toolCall of extracted.toolCalls) {
      if (toolCall.id) toolCallMap.set(toolCall.id, toolCall);
    }
    for (const artifact of extracted.artifacts) {
      artifacts.set(artifact.path, artifact);
    }
    for (const change of extracted.changes) {
      const existing = changes.get(change.path);
      const seen = new Set(change.hunks.map((h) => h.id));
      const carried = (existing?.hunks ?? []).filter((h) => !seen.has(h.id));
      changes.set(change.path, {
        ...change,
        added: (existing?.added ?? 0) + change.added,
        created: Boolean(existing?.created || change.created),
        removed: (existing?.removed ?? 0) + change.removed,
        diffTruncated: Boolean(existing?.diffTruncated || change.diffTruncated),
        hunks: [...carried, ...change.hunks],
      });
    }
  }

  // Second pass: collect verification results from tool messages
  for (const message of messages) {
    if (message.type !== "tool") continue;
    const entry = verificationFromToolMessageCached(
      message as ToolMessage,
      toolCallMap,
    );
    if (entry) verifications.set(entry.id, entry);
  }

  return {
    artifacts: [...artifacts.values()],
    changes: [...changes.values()],
    verifications: [...verifications.values()],
  };
}

export function hasMessageOutputSummary(messages: Message[]): boolean {
  const summary = summarizeOutputs(messages);
  return (
    summary.artifacts.length > 0 ||
    summary.changes.length > 0 ||
    summary.verifications.length > 0
  );
}

function messageText(content: Message["content"]): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => (part.type === "text" ? part.text : ""))
      .join("");
  }
  return "";
}

/**
 * First human message text — used only to retry a failed task. Backend-injected
 * <uploaded_files> blocks are internal markup and must not leak into the
 * new prompt.
 */
function extractOriginalPrompt(messages: Message[]): string | null {
  for (const message of messages) {
    if (message.type !== "human") continue;
    const text = stripUploadedFilesTag(messageText(message.content)).trim();
    if (text) return text;
  }
  return null;
}

function extractRetryPrompt(
  turnMessages: Message[],
  contextMessages: Message[],
): string | null {
  const turnPrompt = extractOriginalPrompt(turnMessages);
  // A punctuation-only nudge ("?", "？？", "...") carries no task intent.
  // Replaying it in a fresh task produces an empty-looking conversation and
  // loses the objective that was interrupted. Fall back to the first real
  // user prompt from the conversation in that case.
  if (turnPrompt && /[\p{L}\p{N}]/u.test(turnPrompt)) return turnPrompt;
  return extractOriginalPrompt(contextMessages) ?? turnPrompt;
}

// Cloudflare Pages deploys to <project>.pages.dev (no "cloudflare." label).
const DEPLOY_URL_PATTERN =
  /\bhttps?:\/\/(?:localhost:\d+|127\.0\.0\.1:\d+|(?:[a-z0-9-]+\.)+(?:vercel\.app|netlify\.app|onrender\.com|herokuapp\.com|pages\.dev|gitpod\.io|github\.io|preview\.app))\b[^\s)\]]*/i;

/**
 * Scans AI messages (latest first) for a URL that looks like a deployed
 * preview — used to surface a "结果链接" direct link in the receipt.
 */
export function extractResultUrl(messages: Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message || message.type !== "ai") continue;
    const match = messageText(message.content).match(DEPLOY_URL_PATTERN);
    if (match) return match[0];
  }
  return null;
}

export function MessageOutputSummary({
  messages,
  turnMessages,
  retryContextMessages,
  threadId,
  onOpenArtifact: onOpenArtifactProp,
  onRetryTask,
  failure,
  className,
  presentation = "final",
}: {
  auditNotice?: string | null;
  messages: Message[];
  /** Full message slice of the turn this receipt belongs to. The plain
   *  assistant group never contains human or tool messages, so verification
   *  results, the original prompt and deploy URLs can only be found here.
   *  Falls back to `messages` when absent. */
  turnMessages?: Message[];
  /** Full conversation used when the failed turn contains only punctuation. */
  retryContextMessages?: Message[];
  threadId?: string;
  /** Keep artifact navigation inside the host workbench when available. */
  onOpenArtifact?: (path: string) => void;
  /** Retry the failed task while preserving the host conversation context. */
  onRetryTask?: (prompt: string) => void;
  failure?: FailurePresentation | null;
  className?: string;
  presentation?: "final" | "process";
}) {
  const { t } = useI18n();
  const { select, setOpen } = useArtifacts();
  const scanMessages = turnMessages ?? messages;
  const summary = useMemo(() => summarizeOutputs(scanMessages), [scanMessages]);
  const resultUrl = useMemo(
    () => extractResultUrl(scanMessages),
    [scanMessages],
  );
  const originalPrompt = useMemo(
    () =>
      extractRetryPrompt(scanMessages, retryContextMessages ?? scanMessages),
    [retryContextMessages, scanMessages],
  );
  if (
    !failure &&
    summary.artifacts.length === 0 &&
    summary.changes.length === 0 &&
    summary.verifications.length === 0
  ) {
    return null;
  }

  const isFailure = Boolean(failure);
  const isResolvedFailure = Boolean(failure?.resolved);
  // Network loss, an environment block, and a "needs your input" hand-off are
  // not agent failures — render them as amber/warning, not destructive red.
  const isWarningFailure =
    failure?.kind === "network" ||
    failure?.kind === "environment" ||
    failure?.kind === "blocked";

  const friendlyVerificationName = (toolName: string): string => {
    switch (toolName) {
      case "run_tests":
      case "test":
        return t.message.testsPassed;
      case "lint_check":
        return t.message.lintClean;
      case "build":
        return t.message.buildSucceeded;
      default:
        return t.message.verificationRan;
    }
  };

  const handleMakeSimilar = () => {
    if (!originalPrompt) return;
    if (onRetryTask) {
      onRetryTask(originalPrompt);
      return;
    }
    window.location.hash = `/workspace/realtime/new?prompt=${encodeURIComponent(
      originalPrompt,
    )}`;
  };

  const openArtifact = (path: string) => {
    if (onOpenArtifactProp) {
      onOpenArtifactProp(path);
      return;
    }
    select(normalizeWorkspaceArtifactRef(path, threadId));
    setOpen(true);
  };

  const finalOutputChanges = summary.changes.filter(
    (change) => change.created && isFinalOutputArtifactPath(change.path),
  );
  const finalOutputRefs = new Set(
    finalOutputChanges.map((change) =>
      normalizeWorkspaceArtifactRef(change.path, threadId),
    ),
  );
  const reviewableChanges = summary.changes;
  const visibleArtifacts = summary.artifacts.filter(
    (artifact) =>
      !isInternalWorkingFilePath(artifact.path) &&
      !finalOutputRefs.has(
        normalizeWorkspaceArtifactRef(artifact.path, threadId),
      ),
  );
  const totalAdded = reviewableChanges.reduce(
    (sum, item) => sum + item.added,
    0,
  );
  const totalRemoved = reviewableChanges.reduce(
    (sum, item) => sum + item.removed,
    0,
  );
  const sourceChanges = reviewableChanges.filter(
    (change) => !finalOutputChanges.includes(change),
  );
  const changeSummaryLabel =
    finalOutputChanges.length > 0 && sourceChanges.length === 0
      ? t.message.artifactsCreated(finalOutputChanges.length)
      : finalOutputChanges.length > 0
        ? t.message.artifactsCreatedAndFilesEdited(
            finalOutputChanges.length,
            sourceChanges.length,
          )
        : t.message.filesEdited(sourceChanges.length);
  // File changes are a completed change set, not another execution step.
  // They receive their own review surface below; keep the generic receipt
  // banner only for failures or turns without a diff.
  const showReceiptBanner = isFailure || reviewableChanges.length === 0;
  const openChangesReview = () => emitOpenAgentWorkbench({ tab: "diff" });

  if (presentation === "process") {
    if (reviewableChanges.length === 0) return null;
    return (
      <section
        aria-label={t.message.changesSummary}
        className={cn("min-w-0", className)}
        data-testid="process-change-summary"
      >
        <ul className="max-h-72 space-y-0.5 overflow-y-auto">
          {reviewableChanges.map((change) => (
            <ChangeRow
              key={change.path}
              change={change}
              threadId={threadId}
              onOpen={
                finalOutputChanges.includes(change)
                  ? () => openArtifact(change.path)
                  : undefined
              }
              presentation="process"
            />
          ))}
        </ul>
      </section>
    );
  }

  return (
    <div
      className={cn(
        "mt-2 flex min-w-0 max-w-full flex-col gap-2 overflow-x-clip",
        className,
      )}
    >
      {showReceiptBanner && (
        <div
          className={cn(
            "flex min-w-0 flex-wrap items-center gap-2 rounded-md border px-3 py-1.5 text-xs",
            isResolvedFailure
              ? "border-border-default bg-muted/35"
              : isFailure
                ? isWarningFailure
                  ? "border-warning/20 bg-warning/50/[0.04]"
                  : "border-destructive/20 bg-destructive/[0.035]"
                : "border-success/20 bg-success/50/[0.055]",
          )}
        >
          {isResolvedFailure ? (
            <CheckCircle2Icon className="size-4 shrink-0 text-success/75" />
          ) : isFailure ? (
            isWarningFailure ? (
              <AlertTriangleIcon className="size-4 shrink-0 text-warning" />
            ) : (
              <XCircleIcon className="size-4 shrink-0 text-destructive" />
            )
          ) : (
            <CheckCircle2Icon className="size-4 shrink-0 text-success" />
          )}
          <div className="min-w-0 flex-1">
            <div
              title={isResolvedFailure ? failure?.message : undefined}
              className={cn(
                "font-medium text-foreground/85",
                isFailure
                  ? "whitespace-pre-wrap break-words leading-5"
                  : "truncate",
              )}
            >
              {isResolvedFailure
                ? t.message.previousAttemptRecovered
                : isFailure
                  ? (failure?.message ?? t.message.taskFailed)
                  : t.message.taskOutputs}
            </div>
            {!isFailure && (
              <div className="truncate text-xs text-muted-foreground">
                {t.message.taskCompleted}
                {reviewableChanges.length > 0 ? ` · ${changeSummaryLabel}` : ""}
                {visibleArtifacts.length > 0
                  ? ` · ${t.message.artifactsCreated(visibleArtifacts.length)}`
                  : ""}
                {summary.verifications.length > 0
                  ? ` · ${t.message.verificationRan} ${
                      summary.verifications.filter((entry) => entry.passed)
                        .length
                    }/${summary.verifications.length}`
                  : ""}
              </div>
            )}
          </div>
          {reviewableChanges.length > 0 && (
            <div className="shrink-0 rounded-full bg-background/75 px-2 py-1 font-mono text-xs shadow-[var(--shadow-xs)]">
              <span className="text-success">+{totalAdded}</span>
              <span className="mx-1 text-muted-foreground"> </span>
              <span className="text-destructive">-{totalRemoved}</span>
            </div>
          )}
          {resultUrl && (
            <RoutedWebLink
              href={resultUrl}
              openTargetSource="task-result"
              className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-border-default bg-background/70 px-2 text-xs font-medium text-muted-foreground transition-colors hover:border-border hover:bg-muted/55 hover:text-foreground"
              title={t.message.resultUrl}
            >
              <LinkIcon className="size-3" />
              {t.message.openResult}
            </RoutedWebLink>
          )}
          {isFailure && !isResolvedFailure && originalPrompt && (
            <button
              type="button"
              onClick={handleMakeSimilar}
              title={t.message.retryTaskHint}
              className={cn(
                "inline-flex h-7 shrink-0 items-center gap-1 rounded-md border px-2 text-xs font-medium transition-colors",
                "border-destructive/30 bg-destructive/[0.08] text-destructive hover:border-destructive/50 hover:bg-destructive/15",
              )}
            >
              <WandSparklesIcon className="size-3" />
              {t.message.retryTask}
            </button>
          )}
        </div>
      )}
      {visibleArtifacts.length > 0 && (
        <section aria-label={t.message.artifactsSummary} className="space-y-2">
          <div className="text-sm font-semibold text-foreground">
            {t.message.artifactsSummary}
          </div>
          <div className="flex flex-col gap-2">
            {visibleArtifacts.map((artifact) => (
              <button
                key={artifact.path}
                type="button"
                onClick={() => openArtifact(artifact.path)}
                className={cn(
                  "group flex w-full items-center gap-3 rounded-lg border border-border-default bg-muted/25 px-3 py-2.5 text-left",
                  "transition-colors hover:border-border hover:bg-muted/45",
                )}
              >
                <span className="shrink-0">
                  {getFileIcon(artifactDisplayPath(artifact.path), "size-7")}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-foreground">
                    {artifact.title ||
                      getFileName(artifactDisplayPath(artifact.path))}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {artifact.kind ||
                      getFileExtensionDisplayName(
                        artifactDisplayPath(artifact.path),
                      )}
                  </span>
                </span>
                <ExternalLinkIcon className="size-4 shrink-0 text-muted-foreground opacity-70 transition-opacity group-hover:opacity-100" />
              </button>
            ))}
          </div>
        </section>
      )}

      {summary.verifications.length > 0 && (
        <section aria-label={t.message.verificationRan} className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <CheckCircle2Icon className="size-4 shrink-0 text-muted-foreground/60" />
            {t.message.verificationRan}
            <span className="rounded-full bg-muted/60 px-1.5 py-0.5 font-mono text-xs font-medium text-muted-foreground">
              {summary.verifications.filter((v) => v.passed).length}/
              {summary.verifications.length}
            </span>
          </div>
          <ul className="overflow-hidden rounded-lg border border-border-default bg-muted/25 divide-y divide-border-default">
            {summary.verifications.map((entry) => {
              const passed = entry.passed;
              const label = friendlyVerificationName(entry.toolName);
              return (
                <li
                  key={entry.id}
                  className="flex items-start gap-2 px-3 py-2 text-xs"
                >
                  {passed ? (
                    <CheckCircle2Icon className="mt-0.5 size-3.5 shrink-0 text-success" />
                  ) : (
                    <XCircleIcon className="mt-0.5 size-3.5 shrink-0 text-destructive" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium text-foreground">
                        {label}
                      </span>
                      <span
                        className={cn(
                          "rounded px-1 py-0.5 text-xs font-mono font-medium",
                          passed
                            ? "bg-success/10 text-success"
                            : "bg-destructive/10 text-destructive",
                        )}
                      >
                        {passed
                          ? t.message.verificationPassed
                          : t.message.verificationFailed}
                      </span>
                    </div>
                    {entry.detail && (
                      <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/60 p-1.5 font-mono text-xs leading-relaxed text-muted-foreground">
                        {entry.detail}
                      </pre>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {reviewableChanges.length > 0 && (
        <section
          aria-label={t.message.changesSummary}
          className="mt-3 min-w-0 overflow-hidden rounded-xl border border-border-default bg-muted/[0.12]"
          data-testid="output-change-set"
        >
          <div className="flex min-w-0 items-center gap-3 border-b border-border-default bg-muted/25 px-3 py-2.5">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-background text-muted-foreground shadow-[var(--shadow-xs)]">
              <FilesIcon className="size-4" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-foreground/90">
                {t.message.completedChanges}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                {changeSummaryLabel}
              </span>
            </span>
            <span
              className="shrink-0 font-mono text-xs"
              aria-label={`+${totalAdded} -${totalRemoved}`}
            >
              <span className="text-success">+{totalAdded}</span>
              <span className="mx-1 text-muted-foreground/40"> </span>
              <span className="text-destructive">-{totalRemoved}</span>
            </span>
            <button
              type="button"
              onClick={openChangesReview}
              className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
              aria-label={t.message.changesSummary}
            >
              <span className="hidden sm:inline">
                {t.message.changesSummary}
              </span>
              <ChevronRightIcon className="size-3.5" aria-hidden="true" />
            </button>
          </div>
          <ul className="max-h-72 divide-y divide-border-default overflow-y-auto">
            {reviewableChanges.map((change) => (
              <ChangeRow
                key={change.path}
                change={change}
                threadId={threadId}
                onOpen={
                  finalOutputChanges.includes(change)
                    ? () => openArtifact(change.path)
                    : openChangesReview
                }
                presentation="final"
              />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/**
 * One file row in the changes summary. When the server streamed
 * per-hunk metadata (FileChangeItem.changes[].hunks) the row becomes
 * expandable and each hunk can be accepted or rejected individually.
 * Reject reverse-applies just that hunk through the same
 * ``/api/fs/revert-diff`` endpoint the "撤销" (revert-all) button uses;
 * accept is informational — the file already contains the change.
 */
function ChangeRow({
  change,
  threadId,
  onOpen,
  presentation,
}: {
  change: OutputChange;
  threadId?: string;
  onOpen?: () => void;
  presentation: "final" | "process";
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [decisions, setDecisions] = useState<
    Record<string, "accepted" | "rejected">
  >({});
  const [rejecting, setRejecting] = useState<string | null>(null);
  const hasHunks = change.hunks.length > 0;

  const rejectHunk = async (hunk: FileHunk) => {
    if (rejecting) return;
    setRejecting(hunk.id);
    try {
      const base = getBackendBaseURL();
      const response = await fetch(`${base}/api/fs/revert-diff`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: change.path,
          diff: singleHunkDiff(change.path, hunk, change.diff ?? null),
          thread_id: threadId,
          delete_empty: false,
        }),
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response));
      }
      setDecisions((prev) => ({ ...prev, [hunk.id]: "rejected" }));
      notifyWorkspaceChanged();
      toast.success(t.message.hunkReverted);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.message.hunkRevertFailed,
      );
    } finally {
      setRejecting(null);
    }
  };

  return (
    <li
      className={cn(
        "min-w-0 text-sm",
        presentation === "final" && "bg-background/35",
      )}
    >
      <div
        className={cn(
          "flex min-w-0 items-center gap-2 text-[13px] leading-5 text-muted-foreground transition-colors",
          presentation === "final"
            ? "group/change-row px-3 py-2.5 hover:bg-muted/20"
            : "group/process-row py-1",
        )}
      >
        <FilePenLineIcon
          className={cn(
            "shrink-0 text-muted-foreground/70",
            presentation === "final" ? "size-4" : "size-3.5",
          )}
        />
        {onOpen ? (
          <button
            type="button"
            title={change.path}
            onClick={onOpen}
            className="min-w-0 flex-1 truncate text-left font-mono text-foreground/80 transition-colors hover:text-foreground hover:underline"
          >
            {changeDisplayPath(change.path)}
          </button>
        ) : (
          <span className="min-w-0 flex-1 truncate font-mono text-foreground/80">
            {changeDisplayPath(change.path)}
          </span>
        )}
        {change.diffTruncated && (
          <span
            title={t.message.diffTruncatedTooltip}
            className="shrink-0 rounded bg-warning/15 px-1.5 py-0.5 text-xs text-warning"
          >
            {t.message.diffTruncated}
          </span>
        )}
        <span className="shrink-0 font-mono text-xs">
          <span className="text-success">+{change.added}</span>
          <span className="mx-1 text-muted-foreground"> </span>
          <span className="text-destructive">-{change.removed}</span>
        </span>
        {/* Disclosure sits at the row's trailing edge: the file name is what a
            reader scans for, so the affordance follows the content instead of
            indenting every path behind a chevron. */}
        {hasHunks && (
          <button
            type="button"
            aria-label={t.message.changesSummary}
            aria-expanded={open}
            onClick={() => setOpen((prev) => !prev)}
            className="shrink-0 rounded-sm text-muted-foreground/60 transition-colors hover:text-foreground"
          >
            <ChevronDownIcon
              className={cn(
                "size-3.5 transition-transform",
                !open && "-rotate-90",
              )}
            />
          </button>
        )}
      </div>
      {hasHunks && open && (
        <div className="ml-4 flex flex-col gap-1.5 border-l-2 border-border/50 py-1.5 pl-3">
          {change.hunks.map((hunk) => (
            <HunkDecisionRow
              key={hunk.id}
              hunk={hunk}
              decision={decisions[hunk.id] ?? hunk.decision}
              rejecting={rejecting === hunk.id}
              onAccept={() =>
                setDecisions((prev) => ({ ...prev, [hunk.id]: "accepted" }))
              }
              onReject={() => void rejectHunk(hunk)}
            />
          ))}
        </div>
      )}
    </li>
  );
}

function HunkDecisionRow({
  hunk,
  decision,
  rejecting,
  onAccept,
  onReject,
}: {
  hunk: FileHunk;
  decision: "pending" | "accepted" | "rejected";
  rejecting: boolean;
  onAccept: () => void;
  onReject: () => void;
}) {
  const { t } = useI18n();
  const header = `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`;
  return (
    <div
      className="overflow-hidden rounded border border-border-subtle bg-card/60 text-xs"
      data-hunk-decision={decision}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border-subtle bg-muted/30 px-2 py-1">
        <span className="font-mono text-xs text-muted-foreground">
          {header}
        </span>
        <div className="flex items-center gap-1.5">
          {decision === "pending" ? (
            <>
              <button
                type="button"
                onClick={onAccept}
                className="inline-flex h-6 items-center rounded-md border border-border-default px-2 text-xs font-medium text-success transition-colors hover:bg-success/10 dark:text-success"
              >
                {t.message.accept}
              </button>
              <button
                type="button"
                onClick={onReject}
                disabled={rejecting}
                className="inline-flex h-6 items-center gap-1 rounded-md border border-border-default px-2 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-55 dark:text-destructive"
              >
                {rejecting && (
                  <Loader2Icon className="size-3 animate-spin text-muted-foreground/70" />
                )}
                {t.message.reject}
              </button>
            </>
          ) : (
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-xs font-medium uppercase tracking-wide",
                decision === "accepted"
                  ? "bg-success/15 text-success"
                  : "bg-destructive/15 text-destructive",
              )}
            >
              {decision === "accepted"
                ? t.message.accepted
                : t.message.rejected}
            </span>
          )}
        </div>
      </div>
      <pre className="max-h-48 overflow-auto font-mono text-xs leading-snug">
        {hunk.body.split(/\r?\n/).map((line, idx) =>
          line ? (
            <div key={idx} className={cn("px-2", hunkLineTone(line))}>
              {line}
            </div>
          ) : null,
        )}
      </pre>
    </div>
  );
}

function hunkLineTone(line: string): string {
  if (line.startsWith("@@")) return "text-muted-foreground";
  if (line.startsWith("+")) return "bg-success/10 text-success";
  if (line.startsWith("-")) return "bg-destructive/10 text-destructive";
  return "text-foreground/80";
}

function notifyWorkspaceChanged() {
  window.dispatchEvent(new CustomEvent("echo:workspace-changed"));
}

async function responseErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") return payload.detail;
    if (payload?.detail?.error) return String(payload.detail.error);
  } catch (e) {
    swallow(e);
  }
  return response.statusText || `Request failed (${response.status})`;
}
