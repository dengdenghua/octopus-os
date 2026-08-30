/**
 * 工具调用→人话动作显示层（纯函数模块，无 React 依赖）。
 *
 * 为每个工具调用提供结构化的人话描述：
 * - verb：动作动词（编辑/运行/查看/搜索…）
 * - object：操作对象（文件名/命令摘要/查询词…）
 * - iconName：图标标识（lucide icon 名，渲染层据此选图标）
 * - workbenchTab：点击"详情"跳转的 Workbench tab
 * - aggregateKind：聚合分组键（同 kind 的连续工具可以聚合）
 *
 * 推断优先级：显式工具名映射 > 工具名关键词启发式 > 拆词兜底。
 */
import type { LucideIcon } from "lucide-react";
import {
  BookOpenTextIcon,
  CheckCircleIcon,
  FilePlus2Icon,
  FileTextIcon,
  FolderOpenIcon,
  GlobeIcon,
  MonitorIcon,
  NetworkIcon,
  PencilLineIcon,
  PlayCircleIcon,
  SearchIcon,
  SquareTerminalIcon,
  UsersIcon,
  WrenchIcon,
  ListTodoIcon,
} from "lucide-react";

import {
  EDIT_TOOL_NAMES,
  READ_TOOL_NAMES,
  SEARCH_TOOL_NAMES,
  SHELL_TOOL_NAMES,
  WRITE_TOOL_NAMES,
  shellCommandFromInput,
} from "../tool-name-groups";

export type ActionAggregateKind =
  | "file_write"
  | "file_read"
  | "command"
  | "web_search"
  | "browser"
  | "teammate"
  | "todo"
  | "other";

export interface ActionDisplay {
  labelKey:
    | "create_file"
    | "edit_file"
    | "search_files"
    | "view_directory"
    | "read_file"
    | "run_command"
    | "search_web"
    | "browse_web"
    | "browser_click"
    | "browser_type"
    | "browser_screenshot"
    | "browser_navigate"
    | "browser_action"
    | "update_plan"
    | "use_capability"
    | "delegate_task"
    | "submit_result"
    | "delete_file"
    | "move_file"
    | "start_preview"
    | "network_request"
    | "raw";
  verb: string;
  object: string;
  iconName: string;
  workbenchTab: "agent" | "terminal" | "browser" | "diff" | "artifacts";
  aggregateKind: ActionAggregateKind;
}

const FILE_PATH_KEYS = [
  "file_path",
  "path",
  "filepath",
  "file",
  "filename",
  "target",
  "destination",
  "dest",
  "output_path",
  "output",
];

const QUERY_KEYS = [
  "query",
  "search_query",
  "q",
  "pattern",
  "keyword",
  "search",
];

const URL_KEYS = ["url", "uri", "link", "webpage", "site", "page_url"];
const CAPABILITY_KEYS = [
  "capability",
  "capability_name",
  "skill",
  "skill_name",
  "name",
  "id",
];
const CAPABILITY_TOOL_NAMES = new Set([
  "use_capability",
  "invoke_capability",
  "call_capability",
  "query_capability",
  "use_skill",
  "query_skill",
  "read_skill",
]);

const MAX_OBJECT_LENGTH = 50;
const MAX_COMMAND_LENGTH = 35;
const MAX_QUERY_LENGTH = 40;

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * Runtime MCP events keep transport metadata at the top level and the actual
 * tool arguments under ``arguments.input``. Restored events and direct calls
 * can instead use ``arguments`` or ``input``. Presenters should see one stable
 * argument object regardless of which transport produced the event.
 */
export function effectiveToolInput(
  input: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const root = input ?? {};
  const args = recordValue(root.arguments);
  const directInput = recordValue(root.input);
  const nestedInput = recordValue(args.input);
  return { ...root, ...args, ...directInput, ...nestedInput };
}

function truncate(value: string, max: number): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max - 1)}…`;
}

function getFileName(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/");
  return parts[parts.length - 1] || path;
}

function hasToolToken(name: string, token: string): boolean {
  return new RegExp(`(?:^|[_-])${token}(?:[_-]|$)`, "i").test(name);
}

function isHumanSafeObject(value: string): boolean {
  const text = value.trim();
  if (!text || text.length > 160) return false;
  // Tool adapters occasionally serialize an empty result into an argument
  // string. That is evidence for the Workbench, never a transcript object.
  if (
    /^[{[]|[}\]]$|["']\s*:\s*|(?:items|count|result|error)\s*["']?\s*:/i.test(
      text,
    )
  ) {
    return false;
  }
  return !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(text);
}

function extractPath(input: Record<string, unknown>): string | null {
  for (const key of FILE_PATH_KEYS) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) {
      const fileName = getFileName(value.trim());
      return isHumanSafeObject(fileName) ? fileName : null;
    }
  }
  return null;
}

function extractQuery(input: Record<string, unknown>): string | null {
  for (const key of QUERY_KEYS) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) {
      return isHumanSafeObject(truncate(value.trim(), MAX_QUERY_LENGTH))
        ? truncate(value.trim(), MAX_QUERY_LENGTH)
        : null;
    }
  }
  return null;
}

function extractUrl(input: Record<string, unknown>): string | null {
  for (const key of URL_KEYS) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) {
      try {
        const url = new URL(value.trim());
        return url.hostname + url.pathname.slice(0, 20);
      } catch {
        return truncate(value.trim(), MAX_OBJECT_LENGTH);
      }
    }
  }
  return null;
}

function extractCapabilityName(
  input: Record<string, unknown>,
): string | null {
  for (const key of CAPABILITY_KEYS) {
    const value = input[key];
    if (typeof value !== "string" || !value.trim()) continue;
    const name = truncate(value.trim(), MAX_QUERY_LENGTH);
    return isHumanSafeObject(name) ? name : null;
  }
  return null;
}

function extractCommandSummary(input: Record<string, unknown>): string | null {
  const cmd = shellCommandFromInput(input);
  if (!cmd) return null;
  const trimmed = cmd.trim();
  if (!trimmed) return null;
  const firstLine = trimmed.split("\n")[0] ?? trimmed;
  // Commands are evidence, not prose. Never surface local paths, credentials
  // or shell workarounds in the main transcript.
  if (
    /(?:~\/\.|\/Users\/|\/private\/|\/tmp\/|\.ssh|id_rsa|token|secret|password|credential|api[_-]?key)/i.test(
      firstLine,
    )
  ) {
    return null;
  }
  return truncate(firstLine.trim(), MAX_COMMAND_LENGTH);
}

function camelToWords(name: string): string {
  return name
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

function isBrowserToolName(name: string): boolean {
  const n = name.toLowerCase();
  return (
    n.includes("browser") ||
    n.includes("navigate") ||
    hasToolToken(n, "click") ||
    hasToolToken(n, "type") ||
    n.includes("screenshot") ||
    n.startsWith("browser_page") ||
    n.startsWith("page_action") ||
    n.startsWith("page_navigate")
  );
}

function isTodoToolName(name: string): boolean {
  const n = name.toLowerCase();
  return n.includes("todo") || n === "task_write" || n === "plan_write";
}

function isCapabilityToolName(name: string): boolean {
  return CAPABILITY_TOOL_NAMES.has(name.toLowerCase());
}

export function isTeammateToolName(name: string): boolean {
  const n = name.toLowerCase();
  return (
    n.includes("teammate") ||
    n.includes("subagent") ||
    n.includes("sub_agent") ||
    n.includes("spawn_agent") ||
    n.includes("delegate") ||
    n === "call_teammate" ||
    n === "call_agent" ||
    n === "call_agent_parallel" ||
    n === "delegate_agent"
  );
}

function extractTeammateName(args: Record<string, unknown>): string {
  // ``role_display_name`` / ``codename`` are what the sub-agent lifecycle
  // markers (``__subagent_spawned__`` / ``__subagent_finished__``) carry.
  // Without them a marker row fell through to a bare "委派任务" verb with no
  // name, because none of the keys below are present on those payloads.
  for (const key of [
    "agent_name",
    "display_name",
    "role_display_name",
    "codename",
  ]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  for (const key of ["agent_id", "subagent_type", "name", "role"]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) {
      return friendlyRoleName(value.trim());
    }
  }
  const specs = args.specs;
  if (Array.isArray(specs) && specs.length > 0) {
    const first = specs[0];
    if (first && typeof first === "object") {
      const record = first as Record<string, unknown>;
      let rawName = "";
      for (const key of ["agent_id", "agent", "name", "role", "display_name"]) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
          rawName = value.trim();
          break;
        }
      }
      if (rawName) {
        const friendly = friendlyRoleName(rawName);
        return specs.length > 1 ? `${friendly} 等` : friendly;
      }
    }
  }
  return "";
}

function friendlyRoleName(role: string | undefined | null): string {
  const value = role?.trim();
  if (!value) return "Task Agent";
  const lower = value.toLowerCase();
  const map: Record<string, string> = {
    architect: "System Architect",
    critic: "Reviewer",
    debugger: "Debug Investigator",
    designer: "Designer",
    implementer: "Builder",
    planner: "Planner",
    researcher: "Research Specialist",
    reviewer: "Code Reviewer",
    security: "Security Reviewer",
    "security-review": "Security Reviewer",
    synthesizer: "Synthesizer",
    writer: "Writer",
  };
  return map[lower] ?? value.replace(/[_-]+/g, " ");
}

function isWebFetchToolName(name: string): boolean {
  const n = name.toLowerCase();
  return (
    n === "fetch_url" ||
    n === "web_fetch" ||
    (n.includes("fetch") && (n.includes("web") || n.includes("http")))
  );
}

export function getActionDisplay(
  toolName: string,
  input: Record<string, unknown> | undefined,
): ActionDisplay {
  const name = toolName.toLowerCase();
  const args = effectiveToolInput(input);

  if (WRITE_TOOL_NAMES.has(name) || name.includes("create_file")) {
    const path = extractPath(args);
    return {
      labelKey: "create_file",
      verb: "创建",
      object: path ?? "文件",
      iconName: "file-plus",
      workbenchTab: "diff",
      aggregateKind: "file_write",
    };
  }

  if (EDIT_TOOL_NAMES.has(name)) {
    const path = extractPath(args);
    return {
      labelKey: "edit_file",
      verb: "编辑",
      object: path ?? "文件",
      iconName: "pencil-line",
      workbenchTab: "diff",
      aggregateKind: "file_write",
    };
  }

  if (READ_TOOL_NAMES.has(name)) {
    const path = extractPath(args);
    const query = extractQuery(args);
    if (
      name === "glob" ||
      name === "glob_files" ||
      name === "file_glob" ||
      name === "grep" ||
      name === "grep_files" ||
      name === "search_files" ||
      name === "find" ||
      name === "find_files"
    ) {
      return {
        labelKey: "search_files",
        verb: "搜索文件",
        object: query ?? path ?? "",
        iconName: "search",
        workbenchTab: "agent",
        aggregateKind: "file_read",
      };
    }
    if (name === "ls" || name === "list_cwd" || name === "tree") {
      return {
        labelKey: "view_directory",
        verb: "查看目录",
        object: path ?? "",
        iconName: "folder-open",
        workbenchTab: "agent",
        aggregateKind: "file_read",
      };
    }
    return {
      labelKey: "read_file",
      verb: "读取",
      object: path ?? "文件",
      iconName: "file-text",
      workbenchTab: "agent",
      aggregateKind: "file_read",
    };
  }

  if (SHELL_TOOL_NAMES.has(name)) {
    const cmd = extractCommandSummary(args);
    return {
      labelKey: "run_command",
      verb: "运行",
      object: cmd ?? "",
      iconName: "square-terminal",
      workbenchTab: "terminal",
      aggregateKind: "command",
    };
  }

  if (SEARCH_TOOL_NAMES.has(name)) {
    const query = extractQuery(args);
    return {
      labelKey: "search_web",
      verb: "搜索网页",
      object: query ?? "",
      iconName: "globe",
      workbenchTab: "browser",
      aggregateKind: "web_search",
    };
  }

  if (isWebFetchToolName(toolName)) {
    const url = extractUrl(args);
    return {
      labelKey: "browse_web",
      verb: "浏览网页",
      object: url ?? "",
      iconName: "globe",
      workbenchTab: "browser",
      aggregateKind: "web_search",
    };
  }

  if (isBrowserToolName(toolName)) {
    const url = extractUrl(args);
    const labelKey: ActionDisplay["labelKey"] = hasToolToken(name, "click")
      ? "browser_click"
      : hasToolToken(name, "type")
        ? "browser_type"
        : name.includes("screenshot")
          ? "browser_screenshot"
          : name.includes("navigate")
            ? "browser_navigate"
            : "browser_action";
    const verb = hasToolToken(name, "click")
      ? "点击"
      : hasToolToken(name, "type")
        ? "输入"
        : name.includes("screenshot")
          ? "截图"
          : name.includes("navigate")
            ? "导航到"
            : "操作浏览器";
    return {
      labelKey,
      verb,
      object: url ?? "",
      iconName: "monitor",
      workbenchTab: "browser",
      aggregateKind: "browser",
    };
  }

  if (isTodoToolName(toolName)) {
    return {
      labelKey: "update_plan",
      verb: "更新计划",
      object: "",
      iconName: "list-todo",
      workbenchTab: "agent",
      aggregateKind: "todo",
    };
  }

  if (isCapabilityToolName(toolName)) {
    return {
      labelKey: "use_capability",
      verb: "使用能力",
      object: extractCapabilityName(args) ?? "",
      iconName: "book-open",
      workbenchTab: "agent",
      aggregateKind: "other",
    };
  }

  if (isTeammateToolName(toolName)) {
    const teammateName = extractTeammateName(args);
    return {
      labelKey: "delegate_task",
      verb: "委派任务",
      object: teammateName ? `给 ${teammateName}` : "",
      iconName: "users",
      workbenchTab: "agent",
      aggregateKind: "teammate",
    };
  }

  if (name === "report" || name.endsWith(":report")) {
    return {
      labelKey: "submit_result",
      verb: "提交结果",
      object: "",
      iconName: "check-circle",
      workbenchTab: "agent",
      aggregateKind: "other",
    };
  }

  if (
    hasToolToken(name, "delete") ||
    hasToolToken(name, "remove") ||
    hasToolToken(name, "unlink") ||
    name === "rm"
  ) {
    const path = extractPath(args);
    return {
      labelKey: "delete_file",
      verb: "删除",
      object: path ?? "文件",
      iconName: "file-text",
      workbenchTab: "diff",
      aggregateKind: "other",
    };
  }

  if (name.includes("rename") || name.includes("move")) {
    const path = extractPath(args);
    return {
      labelKey: "move_file",
      verb: "移动/重命名",
      object: path ?? "文件",
      iconName: "file-text",
      workbenchTab: "diff",
      aggregateKind: "other",
    };
  }

  if (
    name.includes("preview") ||
    name.includes("serve") ||
    name.includes("start_server")
  ) {
    return {
      labelKey: "start_preview",
      verb: "启动预览",
      object: "",
      iconName: "play-circle",
      workbenchTab: "artifacts",
      aggregateKind: "other",
    };
  }

  if (
    name.includes("http") ||
    name.includes("request") ||
    name.includes("api_call")
  ) {
    const url = extractUrl(args);
    return {
      labelKey: "network_request",
      verb: "网络请求",
      object: url ?? "",
      iconName: "network",
      workbenchTab: "browser",
      aggregateKind: "other",
    };
  }

  const words = camelToWords(toolName);
  const explicitDescription =
    typeof args.description === "string" && args.description.trim()
      ? args.description.trim()
      : null;
  const hasSensitiveInput = Object.keys(args).some((key) =>
    /token|secret|password|credential|api[_-]?key/i.test(key),
  );
  return {
    labelKey: "raw",
    verb: explicitDescription ?? (hasSensitiveInput ? "执行操作" : words),
    object: "",
    iconName: "wrench",
    workbenchTab: "agent",
    aggregateKind: "other",
  };
}

export function getActionIcon(iconName: string): LucideIcon {
  switch (iconName) {
    case "file-plus":
      return FilePlus2Icon;
    case "pencil-line":
      return PencilLineIcon;
    case "file-text":
      return FileTextIcon;
    case "folder-open":
      return FolderOpenIcon;
    case "square-terminal":
      return SquareTerminalIcon;
    case "globe":
      return GlobeIcon;
    case "monitor":
      return MonitorIcon;
    case "list-todo":
      return ListTodoIcon;
    case "users":
      return UsersIcon;
    case "search":
      return SearchIcon;
    case "play-circle":
      return PlayCircleIcon;
    case "network":
      return NetworkIcon;
    case "book-open":
      return BookOpenTextIcon;
    case "check-circle":
      return CheckCircleIcon;
    case "wrench":
    default:
      return WrenchIcon;
  }
}

export function aggregateIconName(kind: ActionAggregateKind): string {
  switch (kind) {
    case "file_write":
      return "pencil-line";
    case "file_read":
      return "search";
    case "command":
      return "square-terminal";
    case "web_search":
      return "globe";
    case "browser":
      return "monitor";
    case "teammate":
      return "users";
    case "todo":
      return "list-todo";
    case "other":
    default:
      return "wrench";
  }
}
