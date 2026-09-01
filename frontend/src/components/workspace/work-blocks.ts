import { swallow } from "@/core/utils/log";
import type { LiveToolEvent } from "./live-tool-timeline";
import { effectiveToolInput } from "./messages/action-display";

export type WorkBlockKind =
  | "agent"
  | "browser"
  | "file"
  | "read"
  | "search"
  | "skill"
  | "swarm"
  | "terminal"
  | "todo";

export type WorkBlockActionKey =
  | "awaitVerification"
  | "spawnAgent"
  | "finishAgent"
  | "writeTodoList"
  | "parallelDispatch"
  | "submitResult"
  | "loadSkill"
  | "terminalFailed"
  | "terminalRecovered"
  | "runTerminal"
  | "read"
  | "createFile"
  | "deleteFile"
  | "editFile"
  | "browse"
  | "search"
  | "execute";

export type WorkBlockTitle =
  | { key: "awaitVerification" }
  | { key: "spawnAgent"; name: string }
  | { key: "finishAgent"; name: string }
  | { key: "action" }
  | { key: "actionTarget"; target: string }
  | { key: "parallelDispatch"; count: number }
  | { key: "skill"; skill: string }
  | { key: "skillDeepResearch" }
  | { key: "skillReportWriting" }
  | { key: "skillDocx" }
  | { key: "connectModel" }
  | { key: "raw"; text: string };

export interface WorkBlock {
  id: string;
  event: LiveToolEvent;
  kind: WorkBlockKind;
  actionKey: WorkBlockActionKey;
  target: string;
  title: WorkBlockTitle;
  subtitle: string;
  status: WorkBlockStatus;
  startedAt: number;
  inputText: string;
  outputText: string;
}

export type WorkBlockStatus = LiveToolEvent["status"] | "warning";

export type WorkBlockStatusLabels = Partial<
  Record<WorkBlockStatus, string>
>;

export interface WorkBlockLabels {
  actions: Record<WorkBlockActionKey, string>;
  actionTarget: (action: string, target: string) => string;
  spawnAgent: (name: string) => string;
  finishAgent: (name: string) => string;
  parallelDispatch: (count: number) => string;
  parallelTarget: (count: number) => string;
  skillNamed: (skill: string) => string;
  skillDeepResearch: string;
  skillReportWriting: string;
  skillDocx: string;
  connectModel: string;
  subagentFallback: string;
}

export interface WorkBlockLabelsShape {
  actions: Partial<Record<WorkBlockActionKey, string>>;
  actionTarget?: string;
  spawnAgent?: string;
  finishAgent?: string;
  parallelDispatch?: string;
  parallelDispatchWithCount?: string;
  parallelTarget?: string;
  parallelTargetWithCount?: string;
  skillNamed?: string;
  skillDeepResearch?: string;
  skillReportWriting?: string;
  skillDocx?: string;
  connectModel?: string;
  subagentFallback?: string;
}

const DEFAULT_WORK_BLOCK_LABELS: WorkBlockLabels = {
  actions: {
    awaitVerification: "Awaiting verification",
    spawnAgent: "Create agent",
    finishAgent: "Agent finished",
    writeTodoList: "Write to-do list",
    parallelDispatch: "Dispatch in parallel",
    submitResult: "Submit result",
    loadSkill: "Load skill",
    terminalFailed: "Terminal run failed",
    terminalRecovered: "Terminal recovered",
    runTerminal: "Run terminal",
    read: "Read",
    createFile: "Create file",
    deleteFile: "Delete file",
    editFile: "Edit",
    browse: "Browse",
    search: "Search",
    execute: "Execute",
  },
  actionTarget: (action, target) => `${action} ${target}`,
  spawnAgent: (name) => `Create agent ${name}`,
  finishAgent: (name) => `Agent ${name} finished`,
  parallelDispatch: (count) =>
    count > 0
      ? `Dispatch ${count} subtasks in parallel`
      : "Dispatch subtasks in parallel",
  parallelTarget: (count) => (count > 0 ? `${count} subtasks` : "Subtasks"),
  skillNamed: (skill) => `Load skill ${skill}`,
  skillDeepResearch: "Load deep research swarm skill",
  skillReportWriting: "Load report writing skill",
  skillDocx: "Assemble DOCX deliverable",
  connectModel: "Connect model",
  subagentFallback: "Subagent",
};

function fillTemplate(
  template: string,
  vars: Record<string, string | number>,
): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => {
    const value = vars[key];
    return value === undefined ? match : String(value);
  });
}

export function workBlockLabelsFromShape(
  raw: unknown,
): WorkBlockLabels | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const shape = raw as WorkBlockLabelsShape;
  if (!shape.actions || typeof shape.actions !== "object") return undefined;
  const fallback = DEFAULT_WORK_BLOCK_LABELS;
  const actions = { ...fallback.actions, ...shape.actions };
  const spawnAgentTemplate = shape.spawnAgent;
  const finishAgentTemplate = shape.finishAgent;
  const parallelTemplate = shape.parallelDispatch;
  const parallelWithCountTemplate = shape.parallelDispatchWithCount;
  const parallelTargetTemplate = shape.parallelTarget;
  const parallelTargetWithCountTemplate = shape.parallelTargetWithCount;
  const skillNamedTemplate = shape.skillNamed;
  return {
    actions,
    actionTarget: shape.actionTarget
      ? (action, target) =>
          fillTemplate(shape.actionTarget as string, { action, target })
      : fallback.actionTarget,
    spawnAgent: spawnAgentTemplate
      ? (name) => fillTemplate(spawnAgentTemplate, { name })
      : fallback.spawnAgent,
    finishAgent: finishAgentTemplate
      ? (name) => fillTemplate(finishAgentTemplate, { name })
      : fallback.finishAgent,
    parallelDispatch: (count) => {
      if (count > 0 && parallelWithCountTemplate) {
        return fillTemplate(parallelWithCountTemplate, { count });
      }
      if (count > 0) return fallback.parallelDispatch(count);
      return parallelTemplate ?? fallback.parallelDispatch(0);
    },
    parallelTarget: (count) => {
      if (count > 0 && parallelTargetWithCountTemplate) {
        return fillTemplate(parallelTargetWithCountTemplate, { count });
      }
      if (count > 0) return fallback.parallelTarget(count);
      return parallelTargetTemplate ?? fallback.parallelTarget(0);
    },
    skillNamed: skillNamedTemplate
      ? (skill) => fillTemplate(skillNamedTemplate, { skill })
      : fallback.skillNamed,
    skillDeepResearch: shape.skillDeepResearch ?? fallback.skillDeepResearch,
    skillReportWriting: shape.skillReportWriting ?? fallback.skillReportWriting,
    skillDocx: shape.skillDocx ?? fallback.skillDocx,
    connectModel: shape.connectModel ?? fallback.connectModel,
    subagentFallback: shape.subagentFallback ?? fallback.subagentFallback,
  };
}

export function workBlockActionLabel(
  block: WorkBlock,
  labels?: WorkBlockLabels,
): string {
  return (labels ?? DEFAULT_WORK_BLOCK_LABELS).actions[block.actionKey];
}

export function workBlockTitle(
  block: WorkBlock,
  labels?: WorkBlockLabels,
): string {
  const resolved = labels ?? DEFAULT_WORK_BLOCK_LABELS;
  const title = block.title;
  switch (title.key) {
    case "awaitVerification":
      return resolved.actions.awaitVerification;
    case "spawnAgent":
      return resolved.spawnAgent(title.name || resolved.subagentFallback);
    case "finishAgent":
      return resolved.finishAgent(title.name || resolved.subagentFallback);
    case "action":
      return resolved.actions[block.actionKey];
    case "actionTarget":
      return resolved.actionTarget(
        resolved.actions[block.actionKey],
        title.target,
      );
    case "parallelDispatch":
      return resolved.parallelDispatch(title.count);
    case "skill":
      return resolved.skillNamed(title.skill);
    case "skillDeepResearch":
      return resolved.skillDeepResearch;
    case "skillReportWriting":
      return resolved.skillReportWriting;
    case "skillDocx":
      return resolved.skillDocx;
    case "connectModel":
      return resolved.connectModel;
    case "raw":
      return title.text;
  }
}

export function workBlockTarget(
  block: WorkBlock,
  labels?: WorkBlockLabels,
): string {
  const resolved = labels ?? DEFAULT_WORK_BLOCK_LABELS;
  const title = block.title;
  if (title.key === "parallelDispatch") {
    return resolved.parallelTarget(title.count);
  }
  if (title.key === "spawnAgent" || title.key === "finishAgent") {
    return title.name || resolved.subagentFallback;
  }
  return block.target;
}

export interface SettledRunDisplayOptions {
  hasAnswer?: boolean;
  runSettled?: boolean;
  runFailed?: boolean;
  paused?: boolean;
}

const LOW_LEVEL_EVENTS = new Set([
  "turn_request",
  "stream_connection",
  "response_stream",
  "model_gateway",
  "model_reasoning",
]);

export function toWorkBlocks(events: LiveToolEvent[]): WorkBlock[] {
  return coalesceWorkEvents(events)
    .filter(isVisibleWorkEvent)
    .sort((a, b) => a.startedAt - b.startedAt)
    .map(toWorkBlock);
}

function coalesceWorkEvents(events: LiveToolEvent[]): LiveToolEvent[] {
  const byId = new Map<string, LiveToolEvent>();
  for (const event of events) {
    const previous = byId.get(event.id);
    if (!previous) {
      byId.set(event.id, event);
      continue;
    }
    byId.set(event.id, {
      ...previous,
      ...event,
      input: event.input ?? previous.input,
      output: event.output ?? previous.output,
      startedAt: Math.min(previous.startedAt, event.startedAt),
      finishedAt: event.finishedAt ?? previous.finishedAt,
      durationMs: event.durationMs ?? previous.durationMs,
    });
  }
  return [...byId.values()];
}

export function normalizeEventsForSettledDisplay(
  events: LiveToolEvent[],
  options: SettledRunDisplayOptions = {},
): LiveToolEvent[] {
  if (
    !options.runSettled ||
    options.runFailed ||
    options.paused ||
    !options.hasAnswer
  ) {
    return events;
  }
  return events.map((event) => {
    if (event.status !== "running" && event.status !== "waiting_approval") {
      return event;
    }
    return {
      ...event,
      status: "done",
      finishedAt: event.finishedAt ?? event.startedAt,
      durationMs: event.durationMs ?? 0,
    };
  });
}

export function pickCurrentWorkBlock(blocks: WorkBlock[]): WorkBlock | null {
  return (
    [...blocks]
      .reverse()
      .find(
        (block) =>
          block.status === "running" || block.status === "waiting_approval",
      ) ??
    blocks[blocks.length - 1] ??
    null
  );
}

export function progressForWorkBlocks(blocks: WorkBlock[], current: WorkBlock) {
  const selectedIndex = Math.max(
    0,
    blocks.findIndex((block) => block.id === current.id),
  );
  const terminal = blocks.filter(
    (block) =>
      block.status === "done" ||
      block.status === "warning" ||
      block.status === "error",
  ).length;
  const currentIndex = Math.max(
    1,
    Math.min(blocks.length, Math.max(terminal, selectedIndex + 1)),
  );
  return { current: currentIndex, total: blocks.length };
}

export function isWorkRunning(blocks: WorkBlock[]): boolean {
  return blocks.some(
    (block) =>
      block.status === "running" || block.status === "waiting_approval",
  );
}

export function statusText(
  status: WorkBlockStatus,
  labels?: WorkBlockStatusLabels,
): string {
  const fallback: Record<WorkBlockStatus, string> = {
    running: "正在执行",
    waiting_approval: "等待确认",
    warning: "已恢复",
    error: "执行失败",
    done: "已完成",
  };
  return labels?.[status] || fallback[status];
}

function toWorkBlock(event: LiveToolEvent): WorkBlock {
  const kind = workKind(event.name);
  const status = workBlockStatus(event);
  const actionKey = workActionKey(event, kind, status);
  const target = workTarget(event, kind);
  const title = workTitle(event, kind, target);
  const subtitle = workSubtitle(event, target);
  return {
    id: event.id,
    event,
    kind,
    actionKey,
    target,
    title,
    subtitle,
    status,
    startedAt: event.startedAt,
    inputText: detailText(event.input),
    outputText: detailText(event.output),
  };
}

function workBlockStatus(event: LiveToolEvent): WorkBlockStatus {
  if (event.status === "error" && isManualVerificationRequiredEvent(event)) {
    return "waiting_approval";
  }
  if (event.status === "error" && isRecoverableToolFailureEvent(event)) {
    return "warning";
  }
  return event.status;
}

function isVisibleWorkEvent(event: LiveToolEvent): boolean {
  if (
    event.parentToolUseId &&
    !event.agentId &&
    !event.agentName &&
    !event.subAgentRole &&
    !event.lifecycle
  ) {
    return false;
  }
  if (LOW_LEVEL_EVENTS.has(event.name)) return false;
  return true;
}

function workKind(name: string): WorkBlockKind {
  if (name === "call_agent_parallel") return "swarm";
  if (name === "todo_write") return "todo";
  if (
    /skill|deep-research|report-writing|docx|pptx-swarm|webapp-building-swarm/i.test(
      name,
    )
  )
    return "skill";
  if (/shell|bash|terminal|cmd|exec|python/i.test(name)) return "terminal";
  if (/fetch|browser|url|web/i.test(name)) return "browser";
  if (/search|grep|glob|list/i.test(name)) return "search";
  if (/read/i.test(name)) return "read";
  if (/(write|edit|replace|create|artifact)/i.test(name)) return "file";
  return "agent";
}

function workTitle(
  event: LiveToolEvent,
  kind: WorkBlockKind,
  target: string,
): WorkBlockTitle {
  if (isManualVerificationRequiredEvent(event)) {
    return { key: "awaitVerification" };
  }
  if (event.lifecycle === "spawned" || /subagent_spawned/i.test(event.name)) {
    return { key: "spawnAgent", name: agentDisplayName(event) };
  }
  if (event.lifecycle === "finished" || /subagent_finished/i.test(event.name)) {
    return { key: "finishAgent", name: agentDisplayName(event) };
  }
  if (event.name === "todo_write") {
    return { key: "action" };
  }
  if (event.name === "call_agent_parallel") {
    return { key: "parallelDispatch", count: specCount(event.input) };
  }
  if (kind === "skill") {
    return skillTitle(event);
  }
  const progressLabel = progressLabelText(event);
  if (event.name.startsWith("mcp:") && progressLabel) {
    return { key: "raw", text: compact(progressLabel, 64) };
  }
  if (target) return { key: "actionTarget", target: compact(target, 48) };
  if (event.name === "model_gateway") return { key: "connectModel" };
  return { key: "action" };
}

function workSubtitle(event: LiveToolEvent, fallbackTarget: string): string {
  if (isManualVerificationRequiredEvent(event)) {
    return statusText(workBlockStatus(event));
  }
  // A failed block's one useful subtitle is why it failed. Without this the
  // subtitle fell through to the agent name / target, so a lane that died on an
  // SSL disconnect or a round cap read as "Researcher" and nothing else — the
  // cause was on the wire and in the event, just never on screen.
  if (event.status === "error" && event.error?.trim()) {
    return compact(event.error.trim(), 120);
  }
  const progress = progressSubtitleText(event);
  if (progress) return compact(progress, 88);
  if (event.name === "todo_write") {
    return todoTitle(event.input) || statusText(workBlockStatus(event));
  }
  const inputTarget = firstString(event.input, [
    "path",
    "file_path",
    "filepath",
    "url",
    "query",
    "pattern",
    "cwd",
  ]);
  if (inputTarget) return compact(publicInputTarget(inputTarget, event), 88);
  if (fallbackTarget) return compact(fallbackTarget, 88);
  if (event.agentName) return event.agentName;
  return statusText(workBlockStatus(event));
}

function workActionKey(
  event: LiveToolEvent,
  kind: WorkBlockKind,
  status: WorkBlockStatus,
): WorkBlockActionKey {
  if (isManualVerificationRequiredEvent(event)) return "awaitVerification";
  if (event.lifecycle === "spawned" || /subagent_spawned/i.test(event.name)) {
    return "spawnAgent";
  }
  if (event.lifecycle === "finished" || /subagent_finished/i.test(event.name)) {
    return "finishAgent";
  }
  if (event.name === "todo_write") return "writeTodoList";
  if (event.name === "call_agent_parallel") return "parallelDispatch";
  if (normalizedToolName(event.name) === "report") return "submitResult";
  if (kind === "skill") return "loadSkill";
  if (kind === "terminal") {
    if (status === "error") return "terminalFailed";
    if (status === "warning") return "terminalRecovered";
    return "runTerminal";
  }
  if (kind === "read") return "read";
  if (kind === "file") return fileActionKey(event);
  if (kind === "browser") return "browse";
  if (kind === "search") return "search";
  if (kind === "swarm") return "parallelDispatch";
  return "execute";
}

function fileActionKey(event: LiveToolEvent): WorkBlockActionKey {
  const op =
    firstString(event.input, ["op", "operation", "action"]) ||
    firstChangeString(event.input, ["op", "operation", "action"]);
  if (/add|create|new|generate|write/i.test(op)) return "createFile";
  if (/delete|remove/i.test(op)) return "deleteFile";
  return "editFile";
}

function workTarget(event: LiveToolEvent, kind: WorkBlockKind): string {
  if (event.lifecycle === "spawned" || /subagent_spawned/i.test(event.name)) {
    return agentDisplayName(event);
  }
  if (event.lifecycle === "finished" || /subagent_finished/i.test(event.name)) {
    return agentDisplayName(event);
  }
  if (event.name === "todo_write") return "";
  if (event.name === "call_agent_parallel") return "";
  if (kind === "skill") {
    return firstString(event.input, ["skill", "skill_name", "name"]);
  }
  const path =
    firstChangeString(event.input, ["path", "file_path", "filepath"]) ||
    firstString(event.input, ["path", "file_path", "filepath", "filename"]);
  const url = firstString(event.input, ["url"]);
  const query = firstString(event.input, ["query", "pattern"]);
  const commandSummary = firstString(event.input, [
    "description",
    "label",
    "title",
  ]);
  if ((kind === "read" || kind === "file") && path) return basename(path);
  if (kind === "browser" && url) return hostOf(url);
  if (kind === "search" && query) return compact(query, 48);
  if (kind === "terminal" && commandSummary) return compact(commandSummary, 48);
  return "";
}

function publicInputTarget(value: string, event: LiveToolEvent): string {
  const kind = workKind(event.name);
  if (kind === "browser") return hostOf(value);
  if (kind === "read" || kind === "file") return basename(value);
  if (kind === "terminal" && /[\\/]/.test(value)) return basename(value);
  return value;
}

function isManualVerificationRequiredEvent(event: LiveToolEvent): boolean {
  const normalizedName = event.name.trim().toLowerCase();
  if (
    !(
      normalizedName === "verification:manual" ||
      normalizedName.endsWith(":verification:manual")
    )
  ) {
    return false;
  }
  const command = firstString(event.input, ["command"]);
  const output = detailText(event.output);
  return /verification required|no verification step|Code changes were produced/i.test(
    `${command}\n${output}`,
  );
}

function isRecoverableToolFailureEvent(event: LiveToolEvent): boolean {
  const haystack = `${event.name}\n${detailText(event.input)}\n${detailText(
    event.output,
  )}`;
  return /工具失败|status=failed\s+error=TypeError|换一种方式重试|tool failed|tool_error|No such tool|不存在的工具/i.test(
    haystack,
  );
}

function agentDisplayName(event: LiveToolEvent): string {
  return (
    event.subagentCodename ||
    event.agentName ||
    event.subAgentRole ||
    event.agentId ||
    ""
  );
}

function progressRecord(event: LiveToolEvent): Record<string, unknown> | null {
  const progress = effectiveToolInput(event.input).progress;
  if (!progress || typeof progress !== "object" || Array.isArray(progress)) {
    return null;
  }
  return progress as Record<string, unknown>;
}

function progressLabelText(event: LiveToolEvent): string {
  return firstString(progressRecord(event) ?? undefined, ["label"]);
}

function progressSubtitleText(event: LiveToolEvent): string {
  const progress = progressRecord(event);
  if (!progress) return "";
  const label = progressLabelText(event);
  const metric = progressMetricText(progress);
  if (event.name.startsWith("mcp:") && label) return metric || label;
  if (label && metric) return `${label} · ${metric}`;
  return label || metric;
}

function progressMetricText(progress: Record<string, unknown>): string {
  const percent = numberValue(progress.percent);
  if (percent !== null) {
    const normalized = percent > 0 && percent <= 1 ? percent * 100 : percent;
    return `${Math.round(normalized)}%`;
  }
  const current = numberValue(progress.current);
  const total = numberValue(progress.total);
  if (current !== null && total !== null) return `${current}/${total}`;
  if (current !== null) return String(current);
  return "";
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function specCount(input: Record<string, unknown> | undefined): number {
  const specs = effectiveToolInput(input).specs;
  return Array.isArray(specs) ? specs.length : 0;
}

function skillTitle(event: LiveToolEvent): WorkBlockTitle {
  const skill =
    firstString(event.input, ["skill", "skill_name", "name"]) || event.name;
  if (skill === "deep-research-swarm" || event.name === "deep-research-swarm") {
    return { key: "skillDeepResearch" };
  }
  if (skill === "report-writing" || event.name === "report-writing") {
    return { key: "skillReportWriting" };
  }
  if (skill === "docx" || event.name === "docx") {
    return { key: "skillDocx" };
  }
  return { key: "skill", skill: compact(skill, 48) };
}

function todoTitle(input: Record<string, unknown> | undefined) {
  const effective = effectiveToolInput(input);
  const raw = effective.items ?? effective.todos;
  const items = Array.isArray(raw) ? raw : [];
  const current =
    items.find(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        (item as Record<string, unknown>).status === "in_progress",
    ) ??
    [...items]
      .reverse()
      .find((item) => typeof item === "object" && item !== null);
  if (!current || typeof current !== "object") return "";
  const record = current as Record<string, unknown>;
  const value =
    firstString(record, ["activeForm", "active_form"]) ||
    firstString(record, ["content", "text", "title", "task"]);
  return value ? compact(value, 64) : "";
}

function firstString(
  input: Record<string, unknown> | undefined,
  keys: string[],
) {
  const effective = effectiveToolInput(input);
  for (const key of keys) {
    const value = effective[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number") return String(value);
  }
  return "";
}

function firstChangeString(
  input: Record<string, unknown> | undefined,
  keys: string[],
) {
  const changes = effectiveToolInput(input).changes;
  if (!Array.isArray(changes)) return "";
  for (const change of changes) {
    if (!change || typeof change !== "object" || Array.isArray(change)) {
      continue;
    }
    const value = firstString(change as Record<string, unknown>, keys);
    if (value) return value;
  }
  return "";
}

function basename(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

function normalizedToolName(name: string): string {
  return name.trim().toLowerCase().replace(/^mcp:/, "");
}

function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (e) {
    swallow(e);
    return compact(url, 42);
  }
}

function compact(value: string, max: number) {
  const clean = value.replace(/\s+/g, " ").trim();
  return clean.length <= max ? clean : `${clean.slice(0, max - 1)}...`;
}

function detailText(value: unknown): string {
  if (value === undefined || value === null) return "";
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return text.length > 16000 ? `${text.slice(0, 16000)}\n...` : text;
}
