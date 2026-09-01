import {
  AlertCircleIcon,
  CheckCircle2Icon,
  ClockIcon,
  FileTextIcon,
  GlobeIcon,
  Loader2Icon,
  MonitorIcon,
  SearchIcon,
  TerminalIcon,
  XCircleIcon,
} from "lucide-react";

import type { Translations } from "@/core/i18n/locales/types";
import { deriveAgentPhases, type AgentPhaseStatus } from "./agent-phases";
import type { LiveToolEvent } from "./live-tool-timeline";
import {
  normalizeEventsForSettledDisplay,
  toWorkBlocks,
  workBlockTitle,
  type WorkBlock,
  type WorkBlockStatus,
} from "./work-blocks";

// ── Type definitions ──────────────────────────────────────────────────

export interface AgentTile {
  id: string;
  name: string;
  label: string;
  status: "running" | "waiting_approval" | "done" | "pending" | "error";
  task: string;
  /** Full prompt or mission brief when available. */
  prompt?: string;
  /** Emoji avatar from sub-agent spawn event; falls back to BotIcon. */
  avatar?: string;
  /** Cute codename ("Spark-3a4") taking priority over agent_id. */
  codename?: string;
  /** Sub-agent role label ("researcher" / "critic" / ...). */
  role?: string;
  /** Authoritative display name from the backend built-in role catalog
   * (BUILTIN_ROLES), e.g. "Code Reviewer". Absent for free-form labels the
   * catalog doesn't recognise. */
  roleDisplayName?: string;
  /** Authoritative responsibility blurb from BUILTIN_ROLES. Absent when the
   * role label is free-form. */
  roleDescription?: string;
  specIndex?: number;
  taskLabel?: string;
  parentToolUseId?: string;
  currentTool?: string;
  lastThought?: string;
  resultSummary?: string;
  iterationCount?: number;
  blackboardWrites: string[];
  filesTouched: string[];
  durationMs?: number;
  error?: string;
  eventCount: number;
  startedAt: number;
  finishedAt?: number;
}

export type AgentWorkbenchContentOptions = {
  hasAnswer?: boolean;
  runSettled?: boolean;
  runFailed?: boolean;
  paused?: boolean;
};

export type AgentWorkbenchTabId =
  | "agent"
  | "subagents"
  | "artifacts"
  | "plan"
  | "diff"
  | "terminal"
  | "browser"
  | "workspace"
  | "design"
  | "project";

export type JsonRecord = Record<string, unknown>;

export type WorkspaceFocusView =
  | "trace"
  | "terminal"
  | "browser"
  | "diff"
  | "file"
  | "artifact"
  | "image"
  | "approval"
  | "subagent";

export interface AgentWorkspaceFocus {
  itemId: string;
  view: WorkspaceFocusView;
  title?: string;
  subtitle?: string | null;
  previewUrl?: string | null;
}

export interface DiffEntry {
  created: boolean;
  id: string;
  path: string;
  title: string;
  op?: string | null;
  text: string;
  status: WorkBlockStatus;
}

// ── Constants ─────────────────────────────────────────────────────────

export const DIFF_TAB_LABEL = "Diff";
export const TERMINAL_TAB_LABEL = "终端";
export const BROWSER_TAB_LABEL = "浏览器";
export const FINAL_OUTPUT_PATH_PATTERN = /(?:^|[\\/])output[\\/]final[\\/]/i;
export const FINAL_DELIVERABLE_PATTERN =
  /report|docx|pptx|pdf|xlsx|html|报告|调研|总结|交付|文档|方案/i;
/**
 * Files agents use to coordinate their work. They are useful in the process
 * trace, but are not user-facing deliverables even when written below
 * output/final/.
 */
const INTERNAL_WORKING_FILE_BASENAMES = new Set([
  "plan.md",
  "todo.md",
  "todos.md",
  "note.md",
  "notes.md",
]);

export function isInternalWorkingFilePath(path: string | null | undefined) {
  if (!path) return false;
  const normalized = normalizeSlashes(path).replace(/[\\/]+$/, "");
  const basename = normalized.slice(normalized.lastIndexOf("/") + 1);
  return INTERNAL_WORKING_FILE_BASENAMES.has(basename.toLowerCase());
}
const FINAL_DELIVERABLE_WRITE_OPS = new Set([
  "add",
  "added",
  "create",
  "created",
  "edit",
  "edited",
  "generate",
  "generated",
  "modify",
  "modified",
  "new",
  "update",
  "updated",
  "write",
  "written",
]);

// Mirror runtime.execution.subagents.bridge._ROLE_AVATAR so the
// frontend can pick a per-role emoji even when the backend hasn't
// piped the lifecycle ``subagent_spawned`` event through to the
// LiveToolEvent stream yet. Keep these two maps in sync.
export const ROLE_AVATAR: Record<string, string> = {
  researcher: "🔍",
  research: "🔍",
  explorer: "🧭",
  fact_checker: "✅",
  "fact-checker": "✅",
  critic: "🛡️",
  reviewer: "🛡️",
  security: "🛡️",
  "security-review": "🛡️",
  performance: "⚡",
  style: "🎨",
  synthesizer: "✍️",
  writer: "✍️",
  architect: "🏗️",
  designer: "📐",
  implementer: "🔧",
  coder: "🔧",
  reproducer: "🐛",
  hypothesizer: "💡",
  verifier: "🧪",
  debugger: "🐛",
  planner: "📋",
  evaluator: "⚖️",
  generator: "✨",
};
export const DEFAULT_AVATAR = "🐙";

// Role → one-line description of what the role does. The nameplate
// (角色卡) is each role's 说明 (identity/responsibility), not the turn's
// work brief — so this is the primary copy, and the delegated task/prompt
// only fills in as a fallback for roles we don't recognise.
export const ROLE_DESCRIPTION: Record<string, string> = {
  researcher:
    "Investigates the question, gathers evidence, and reports findings.",
  research:
    "Investigates the question, gathers evidence, and reports findings.",
  explorer: "Maps the unknown — explores the space and surfaces what is there.",
  fact_checker: "Verifies claims against sources and flags inaccuracies.",
  "fact-checker": "Verifies claims against sources and flags inaccuracies.",
  critic: "Reviews work against the requirements and challenges weak points.",
  reviewer: "Reviews work against the requirements and challenges weak points.",
  security: "Audits for security risks and unsafe patterns.",
  "security-review": "Audits for security risks and unsafe patterns.",
  performance: "Profiles and tunes for speed, memory, and throughput.",
  style: "Polishes prose and presentation for clarity and tone.",
  synthesizer: "Synthesizes many findings into one coherent conclusion.",
  writer: "Drafts clear, well-structured prose and deliverables.",
  architect: "Designs the structure and breaks the work into parts.",
  designer: "Shapes interfaces and visual decisions.",
  implementer: "Writes and wires up the actual implementation.",
  coder: "Writes and wires up the actual implementation.",
  reproducer: "Reproduces reported issues into minimal failing cases.",
  hypothesizer: "Forms testable hypotheses from the evidence.",
  verifier: "Runs checks and tests to confirm a change actually works.",
  debugger: "Isolates and fixes defects.",
  planner: "Lays out the plan, steps, and task breakdown.",
  evaluator: "Judges results against criteria and scores them.",
  generator: "Generates content and artifacts on demand.",
};

export function roleDescription(
  role: string | undefined | null,
): string | undefined {
  if (!role || typeof role !== "string") return undefined;
  return ROLE_DESCRIPTION[role.trim().toLowerCase()];
}

// ── Utility functions ─────────────────────────────────────────────────

export function blockIcon(kind: WorkBlock["kind"]) {
  if (kind === "file" || kind === "read") return FileTextIcon;
  if (kind === "search") return SearchIcon;
  if (kind === "browser") return GlobeIcon;
  if (kind === "terminal") return TerminalIcon;
  return MonitorIcon;
}

export function statusIcon(status: WorkBlockStatus | AgentPhaseStatus) {
  if (status === "running") return Loader2Icon;
  if (status === "warning") return AlertCircleIcon;
  if (status === "error") return XCircleIcon;
  if (status === "done") return CheckCircle2Icon;
  return ClockIcon;
}

export function kindLabel(
  kind: WorkBlock["kind"],
  labels: Translations["agentWorkbench"],
): string {
  if (kind === "terminal") return labels.kindTerminal;
  if (kind === "browser") return labels.kindBrowser;
  if (kind === "search") return labels.kindSearch;
  if (kind === "read") return labels.kindRead;
  if (kind === "file") return labels.kindFile;
  if (kind === "todo") return labels.kindTodos;
  return labels.kindAgent;
}

export function eventTime(block: WorkBlock) {
  const date = new Date(block.startedAt || Date.now());
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function copyText(text: string) {
  if (typeof navigator === "undefined") return;
  void navigator.clipboard?.writeText(text);
}

const MOJIBAKE_MARKERS = /[ÃÂâäåæçèéïð]/;

function cjkCount(value: string): number {
  return (value.match(/[\u3400-\u9FFF]/g) ?? []).length;
}

function mojibakeMarkerCount(value: string): number {
  return (value.match(/[ÃÂâäåæçèéïð]/g) ?? []).length;
}

export function repairMojibakeText(text: string): string {
  if (!MOJIBAKE_MARKERS.test(text) || typeof TextDecoder === "undefined") {
    return text;
  }
  try {
    const bytes = Uint8Array.from(text, (char) => char.charCodeAt(0) & 0xff);
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (
      cjkCount(decoded) > cjkCount(text) &&
      mojibakeMarkerCount(decoded) < mojibakeMarkerCount(text)
    ) {
      return decoded;
    }
  } catch {
    return text;
  }
  return text;
}

export function hasAgentWorkbenchContent(
  events: LiveToolEvent[],
  options: AgentWorkbenchContentOptions = {},
): boolean {
  const displayEvents = normalizeEventsForSettledDisplay(events, options);
  const { blocks, phases, currentPhase } = deriveAgentPhases(
    displayEvents,
    options,
  );
  return Boolean(currentPhase && phases.length > 0 && blocks.length > 0);
}

export function avatarForRole(
  role: string | undefined | null,
): string | undefined {
  if (!role || typeof role !== "string") return undefined;
  return ROLE_AVATAR[role.trim().toLowerCase()] ?? DEFAULT_AVATAR;
}

// Exported for tests. Not for product code — render through the panel.
export const __testing = { avatarForRole, ROLE_AVATAR, DEFAULT_AVATAR };

export function uniqueStrings(
  values: Array<string | undefined | null>,
): string[] {
  const seen = new Set<string>();
  for (const value of values) {
    const clean = typeof value === "string" ? value.trim() : "";
    if (clean) seen.add(clean);
  }
  return Array.from(seen);
}

export function maxDefined(a?: number, b?: number): number | undefined {
  if (a === undefined) return b;
  if (b === undefined) return a;
  return Math.max(a, b);
}

export function agentEventGroupId(event: LiveToolEvent): string | undefined {
  if (event.agentId) return event.agentId;
  if (event.parentToolUseId && event.subAgentRole) {
    return `${event.parentToolUseId}:${event.subAgentRole}`;
  }
  return event.subagentCodename ?? event.subAgentRole ?? event.agentName;
}

export function readableToolName(event: LiveToolEvent): string {
  if (event.lifecycle === "spawned") return "启动子智能体";
  if (event.lifecycle === "finished") return "完成派生任务";
  return event.name.replace(/^mcp:/, "").replace(/[_-]+/g, " ");
}

export function thoughtFromEvent(event: LiveToolEvent): string | undefined {
  if (event.thought?.trim()) return event.thought.trim();
  if (event.observation?.trim()) return event.observation.trim();
  const input = event.input;
  if (isRecord(input)) {
    const text = stringFromKeys(input, [
      "prompt_preview",
      "prompt",
      "task",
      "message",
      "query",
      "explanation",
    ]);
    if (text) return text;
  }
  const output = event.output;
  if (isRecord(output)) {
    const text = stringFromKeys(output, [
      "summary",
      "output_preview",
      "result",
    ]);
    if (text) return text;
  }
  if (typeof output === "string" && output.trim()) return output.trim();
  return undefined;
}

export function blackboardWritesFromEvent(event: LiveToolEvent): string[] {
  const normalized = event.name.toLowerCase();
  if (!/bb_write|blackboard/.test(normalized)) return [];
  const key = stringFromKeys(event.input, ["key", "name", "path", "title"]);
  return [key || "blackboard"];
}

export function filesFromAgentEvent(event: LiveToolEvent): string[] {
  return uniqueStrings([
    ...(event.filesTouched ?? []),
    ...pathsFromUnknown(event.input),
    ...pathsFromUnknown(event.output),
  ]).slice(0, 16);
}

export function errorFromAgentEvent(event: LiveToolEvent): string | undefined {
  if (event.status !== "error") return undefined;
  const output = event.output;
  if (isRecord(output)) {
    const error = stringFromKeys(output, [
      "error",
      "message",
      "detail",
      "output_preview",
      "summary",
    ]);
    if (error) return error;
  }
  if (typeof output === "string" && output.trim()) return output.trim();
  return event.name;
}

/** Internal auto-parallel bootstrap can fail before producing any usable
 * child output, then deliberately fall back to the main model. Keep that
 * diagnostic in the execution trace, but do not present it as a real Agent
 * seat beside the successful explicit delegation that follows. */
export function isInternalAutoParallelFailure(event: LiveToolEvent): boolean {
  if (event.name !== "subagent" || event.status !== "error") return false;
  const error = [
    event.error,
    stringFromKeys(event.output, ["error", "error_type", "message"]),
    stringFromKeys(event.input, ["error", "error_type", "message"]),
  ]
    .filter((value): value is string => Boolean(value))
    .join(" ")
    .toLowerCase();
  return error.includes("empty_result_contract_violation");
}

export function compactDetail(text: string, max = 160) {
  const clean = repairMojibakeText(text).replace(/\s+/g, " ").trim();
  return clean.length <= max ? clean : `${clean.slice(0, max - 1)}…`;
}

export function agentProgressPercent(status: AgentTile["status"]) {
  if (status === "done") return 100;
  if (status === "running") return 48;
  if (status === "waiting_approval") return 48;
  if (status === "error") return 100;
  return 0;
}

export function durationLabel(ms?: number): string {
  if (ms === undefined || !Number.isFinite(ms)) return "暂无";
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

export function timeLabel(ms?: number): string {
  if (ms === undefined || !Number.isFinite(ms)) return "暂无";
  return new Date(ms).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function stringFromKeys(value: unknown, keys: string[]): string {
  if (!isRecord(value)) return "";
  for (const key of keys) {
    const raw = value[key];
    if (typeof raw === "string" && raw.trim()) {
      return repairMojibakeText(raw.trim());
    }
    if (typeof raw === "number") return String(raw);
  }
  return "";
}

export function workspaceFocusFromEvents(
  events: LiveToolEvent[],
): AgentWorkspaceFocus | null {
  for (const event of [...events].reverse()) {
    const focus = event.input?.workspaceFocus;
    if (!isRecord(focus)) continue;
    const itemId = focus.itemId;
    const view = focus.view;
    if (typeof itemId !== "string" || typeof view !== "string") continue;
    if (
      ![
        "trace",
        "terminal",
        "browser",
        "diff",
        "file",
        "artifact",
        "image",
        "approval",
        "subagent",
      ].includes(view)
    ) {
      continue;
    }
    const title = focus.title;
    const subtitle = focus.subtitle;
    const previewUrl = focus.previewUrl;
    return {
      itemId,
      view: view as WorkspaceFocusView,
      title: typeof title === "string" ? title : undefined,
      subtitle: typeof subtitle === "string" ? subtitle : null,
      previewUrl: typeof previewUrl === "string" ? previewUrl : null,
    };
  }
  return null;
}

export function workspaceFocusTabFromEvents(
  events: LiveToolEvent[],
): AgentWorkbenchTabId | null {
  const focus = workspaceFocusFromEvents(events);
  if (!focus) return null;
  // 浏览器预览是给用户看的，agent 使用无头浏览器时不自动切换
  if (focus.view === "browser") return null;
  if (focus.view === "terminal") return "terminal";
  if (focus.view === "diff") return "diff";
  if (focus.view === "file") return "agent";
  if (focus.view === "artifact" || focus.view === "image") return "agent";
  if (focus.view === "subagent") return "subagents";
  return "agent";
}

export function textFromUnknown(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return repairMojibakeText(value);
  try {
    return repairMojibakeText(JSON.stringify(value, null, 2));
  } catch {
    return repairMojibakeText(String(value));
  }
}

export function normalizeSlashes(path: string): string {
  return path.replace(/\\/g, "/");
}

export function isAbsolutePathLike(path: string): boolean {
  return (
    /^[a-z]:[\\/]/i.test(path) ||
    path.startsWith("/") ||
    path.startsWith("\\\\")
  );
}

export function dirname(path: string): string {
  const normalized = normalizeSlashes(path).replace(/\/+$/, "");
  const index = normalized.lastIndexOf("/");
  if (index <= 0) return path;
  const dir = normalized.slice(0, index);
  return path.includes("\\") ? dir.replace(/\//g, "\\") : dir;
}

export function workspaceRootFromPath(path: string): string | null {
  const slash = normalizeSlashes(path);
  const workspaceMatch = slash.match(/^(.*\/workspaces\/[^/]+)(?:\/|$)/i);
  if (workspaceMatch?.[1]) {
    return path.includes("\\")
      ? workspaceMatch[1].replace(/\//g, "\\")
      : workspaceMatch[1];
  }
  const outputIndex = slash.search(/\/output(?:\/|$)/i);
  if (outputIndex > 0) {
    const root = slash.slice(0, outputIndex);
    return path.includes("\\") ? root.replace(/\//g, "\\") : root;
  }
  return null;
}

export function pathsFromUnknown(value: unknown): string[] {
  const paths = new Set<string>();
  const addPath = (candidate: unknown) => {
    if (typeof candidate === "string" && candidate.trim()) {
      paths.add(candidate.trim());
    }
  };
  if (isRecord(value)) {
    for (const key of [
      "path",
      "artifact_path",
      "file_path",
      "filePath",
      "filepath",
      "filename",
      "output_path",
      "relative_path",
      "written_path",
      "target_path",
      "source_path",
    ]) {
      addPath(value[key]);
    }
    const changes = value.changes;
    if (Array.isArray(changes)) {
      for (const change of changes) {
        if (isRecord(change)) {
          addPath(change.path);
          addPath(change.artifact_path);
          addPath(change.file_path);
          addPath(change.filePath);
          addPath(change.output_path);
          addPath(change.relative_path);
          addPath(change.written_path);
          addPath(change.target_path);
        }
      }
    }
    const files = value.files;
    if (Array.isArray(files)) {
      for (const file of files) addPath(file);
    }
  }
  return Array.from(paths);
}

export function pathsForBlock(block: WorkBlock): string[] {
  const paths = new Set<string>();
  for (const path of pathsFromUnknown(block.event.input)) paths.add(path);
  for (const path of pathsFromUnknown(block.event.output)) paths.add(path);
  for (const path of block.event.filesTouched ?? []) {
    if (path.trim()) paths.add(path.trim());
  }
  return Array.from(paths);
}

export function inferWorkbenchCwd(
  blocks: WorkBlock[],
  explicitWorkDir?: string,
): string | undefined {
  if (explicitWorkDir?.trim()) return explicitWorkDir.trim();
  for (const block of blocks) {
    const cwd = stringFromKeys(block.event.input, [
      "cwd",
      "workdir",
      "work_dir",
      "workspace_path",
      "workspace",
    ]);
    if (cwd) return cwd;
  }
  for (const block of blocks) {
    for (const path of pathsForBlock(block)) {
      if (!isAbsolutePathLike(path)) continue;
      return workspaceRootFromPath(path) ?? dirname(path);
    }
  }
  return undefined;
}

export function looksLikeDiff(text: string): boolean {
  return (
    /^diff --git /m.test(text) ||
    /^---\s+/m.test(text) ||
    /^\+\+\+\s+/m.test(text) ||
    /^@@\s+/m.test(text) ||
    /^\*\*\* Begin Patch/m.test(text)
  );
}

export function diffTextFromUnknown(value: unknown): string {
  if (typeof value === "string") return looksLikeDiff(value) ? value : "";
  if (!isRecord(value)) return "";
  for (const key of ["diff", "patch", "unified_diff", "diff_preview"]) {
    const raw = value[key];
    if (typeof raw === "string" && raw.trim()) return raw.trim();
  }
  const changes = value.changes;
  if (Array.isArray(changes)) {
    const pieces = changes
      .map((change) => diffTextFromUnknown(change))
      .filter(Boolean);
    if (pieces.length > 0) return pieces.join("\n\n");
  }
  return "";
}

function changeRecordsFromUnknown(value: unknown): JsonRecord[] {
  if (!isRecord(value) || !Array.isArray(value.changes)) return [];
  return value.changes.filter((change): change is JsonRecord =>
    isRecord(change),
  );
}

function isFinalOutputPath(path: string | null | undefined) {
  if (!path || isInternalWorkingFilePath(path)) return false;
  const normalized = normalizeSlashes(path).toLowerCase();
  return (
    normalized.includes("/output/final/") ||
    normalized.startsWith("output/final/")
  );
}

function fallbackCreateOpFromEventName(name: string) {
  return /(^|[_.:-])(write_text_file|write_file|create_file|artifact)($|[_.:-])/i.test(
    name,
  )
    ? "create"
    : null;
}

function isCreatedDiffEntry(op: unknown, text: string, path?: string | null) {
  const normalizedOp =
    typeof op === "string" && op.trim() ? op.trim().toLowerCase() : null;
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
  if (isFinalOutputPath(path)) return true;
  return (
    /^---\s+\/dev\/null/m.test(text) ||
    /^new file mode\b/m.test(text) ||
    /^@@\s+-0,0\s+\+\d+(?:,\d+)?\s+@@/m.test(text)
  );
}

export function diffEntriesFromBlocks(blocks: WorkBlock[]): DiffEntry[] {
  const entries = blocks
    .filter(
      (block) =>
        // Diff/changed-file surfaces must represent mutations, not every
        // event that happens to carry a path. Read blocks expose their input
        // path too; admitting them here made a read-only audit appear to have
        // edited every inspected source file.
        block.kind === "file" ||
        /file_change|write_file|edit_file|create_file|patch_file/i.test(
          block.event.name,
        ),
    )
    .flatMap((block): DiffEntry[] => {
      const changeRecords = [
        ...changeRecordsFromUnknown(block.event.output),
        ...changeRecordsFromUnknown(block.event.input),
      ];
      if (changeRecords.length > 0) {
        return changeRecords
          .map((change, index): DiffEntry | null => {
            const text = diffTextFromUnknown(change);
            const path =
              stringFromKeys(change, [
                "path",
                "artifact_path",
                "file_path",
                "filePath",
                "output_path",
                "relative_path",
                "written_path",
                "target_path",
              ]) ||
              pathsForBlock(block)[index] ||
              block.subtitle;
            if (!text && !path) return null;
            const op =
              typeof change.op === "string"
                ? change.op
                : fallbackCreateOpFromEventName(block.event.name);
            return {
              created: isCreatedDiffEntry(op, text, path),
              id: `${block.id}:${path || index}`,
              op,
              path,
              title: path || workBlockTitle(block),
              text,
              status: block.status,
            };
          })
          .filter((entry): entry is DiffEntry => Boolean(entry));
      }
      const text =
        diffTextFromUnknown(block.event.output) ||
        diffTextFromUnknown(block.event.input) ||
        (looksLikeDiff(block.outputText) ? block.outputText : "");
      const path = pathsForBlock(block)[0] ?? block.subtitle;
      if (!text && !path) return [];
      // Show only the file path as title, not the action name
      const displayTitle = path ?? block.title;
      const op =
        stringFromKeys(block.event.output, ["op", "operation"]) ||
        stringFromKeys(block.event.input, ["op", "operation"]) ||
        fallbackCreateOpFromEventName(block.event.name);
      return [
        {
          created: isCreatedDiffEntry(op, text, path),
          id: block.id,
          op,
          path,
          title: displayTitle,
          text,
          status: block.status,
        },
      ];
    })
    .filter((entry): entry is DiffEntry => Boolean(entry));
  return dedupeDiffEntries(entries);
}

function isFinalDeliverableEntry(entry: DiffEntry) {
  const target = entry.path || entry.title;
  if (!target || entry.status === "error") return false;
  if (
    FINAL_OUTPUT_PATH_PATTERN.test(target) &&
    !isInternalWorkingFilePath(target)
  ) {
    return true;
  }
  if (!FINAL_DELIVERABLE_PATTERN.test(target)) return false;
  if (entry.created) return true;
  const op = typeof entry.op === "string" ? entry.op.trim().toLowerCase() : "";
  return FINAL_DELIVERABLE_WRITE_OPS.has(op);
}

export function finalOutputArtifactEntries(events: LiveToolEvent[]) {
  return diffEntriesFromBlocks(toWorkBlocks(events)).filter(
    isFinalDeliverableEntry,
  );
}

export function hasFinalOutputArtifact(events: LiveToolEvent[]) {
  return finalOutputArtifactEntries(events).length > 0;
}

function diffEntryDedupeKey(entry: DiffEntry) {
  const normalized = normalizeSlashes(entry.path || entry.title)
    .toLowerCase()
    .replace(/\/+$/, "");
  return normalized.split("/").pop() || normalized;
}

function diffEntryQuality(entry: DiffEntry) {
  return (
    (entry.text ? 4 : 0) +
    (isAbsolutePathLike(entry.path) ? 2 : 0) +
    (entry.created ? 1 : 0)
  );
}

function dedupeDiffEntries(entries: DiffEntry[]) {
  const byKey = new Map<string, DiffEntry>();
  for (const entry of entries) {
    const key = diffEntryDedupeKey(entry);
    const existing = byKey.get(key);
    if (!existing || diffEntryQuality(entry) > diffEntryQuality(existing)) {
      byKey.set(key, entry);
    }
  }
  return Array.from(byKey.values());
}

export function commandForBlock(block: WorkBlock): string {
  return (
    stringFromKeys(block.event.input, ["command", "cmd", "script"]) ||
    block.subtitle ||
    workBlockTitle(block)
  );
}
