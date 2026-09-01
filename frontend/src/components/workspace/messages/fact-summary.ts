/**
 * 执行行事实摘要提取（纯函数模块，无 React 依赖）。
 *
 * 工具调用完成（result 已返回）时，从结构化结果中提取一条「已确认事实」，
 * 供执行行下方以弱显示样式附加一行。只提取 result 中确定存在的字段，
 * 无法提取时返回 null，绝不编造。
 *
 * 文案不在本模块拼写：函数返回结构化描述（kind + value），
 * 由渲染层用 i18n 模板拼成最终句子。
 */

export type FactSummaryKind =
  | "path"
  | "count"
  | "status"
  | "title"
  | "text"
  | "duration"
  | "lines"
  | "matches"
  | "succeeded"
  | "failed"
  | "exit_code";

export interface FactSummary {
  kind: FactSummaryKind;
  value: string;
}

const MAX_VALUE_LENGTH = 80;
const MAX_TEXT_RESULT_LENGTH = 120;
const MAX_SNIPPET_LENGTH = 60;
const COMMON_READ_TOOLS = new Set([
  "read_file",
  "read_file_range",
  "cat",
  "view",
  "open",
]);
const COMMON_SEARCH_TOOLS = new Set([
  "grep",
  "search_files",
  "find",
  "rg",
  "search_content",
]);
const COMMON_GLOB_TOOLS = new Set(["glob", "glob_files", "file_glob"]);
const COMMON_LIST_TOOLS = new Set(["ls", "list_cwd", "dir", "list_directory"]);
const COMMON_SHELL_TOOLS = new Set([
  "bash",
  "exec_shell",
  "mcp_exec_shell",
  "run_command",
]);

function truncateValue(text: string): string {
  return text.length > MAX_VALUE_LENGTH
    ? `${text.slice(0, MAX_VALUE_LENGTH - 1).trimEnd()}…`
    : text;
}

function truncateSnippet(text: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > MAX_SNIPPET_LENGTH
    ? `${normalized.slice(0, MAX_SNIPPET_LENGTH - 1).trimEnd()}…`
    : normalized;
}

function pathBasename(path: string): string {
  if (!/[\\/]/.test(path)) return path;
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? path;
}

function firstNonEmptyString(
  record: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function firstFiniteNumber(
  record: Record<string, unknown>,
  keys: readonly string[],
): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function firstBoolean(
  record: Record<string, unknown>,
  keys: readonly string[],
): boolean | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") return value;
  }
  return null;
}

function extractLineCount(text: string): number | null {
  const lines = text.replace(/(?:\r?\n)+$/, "").split(/\r?\n/).length;
  return lines > 1 ? lines : null;
}

function extractExitCode(result: Record<string, unknown>): number | null {
  const exitCode = firstFiniteNumber(result, [
    "exit_code",
    "exitCode",
    "returncode",
    "return_code",
  ]);
  if (exitCode !== null) return exitCode;
  const status = firstNonEmptyString(result, ["status"]);
  if (status === "success" || status === "ok") return 0;
  if (status === "error" || status === "failed" || status === "failure")
    return 1;
  return null;
}

function extractSnippetFromText(text: string): string | null {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length === 0) return null;
  const firstMeaningful = lines.find((l) => l.trim().length > 3) ?? lines[0];
  return firstMeaningful ? truncateSnippet(firstMeaningful) : null;
}

export function isToolResultError(result: unknown): boolean {
  if (typeof result === "string") {
    const trimmed = result.trim();
    if (!trimmed) return false;
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      if (typeof parsed === "object" && parsed !== null) {
        return isToolResultError(parsed);
      }
    } catch {
      // Plain command output is handled by diagnostic line shapes below.
    }
    return (
      /(?:^|\n)\s*(?:error|failed|failure|exception)\s*[:：]/i.test(trimmed) ||
      /(?:^|\n)\s*traceback \(most recent call last\):/i.test(trimmed) ||
      /(?:^|\n)[^\n]*(?:command not found|no such file|permission denied)\s*$/im.test(
        trimmed,
      )
    );
  }
  if (typeof result === "object" && result !== null && !Array.isArray(result)) {
    const record = result as Record<string, unknown>;
    if (record.error != null || record.exception != null) return true;
    const status = firstNonEmptyString(record, ["status", "state"]);
    if (status === "error" || status === "failed" || status === "failure")
      return true;
    const exitCode = extractExitCode(record);
    if (exitCode !== null && exitCode !== 0) return true;
  }
  return false;
}

function isSafePublicSnippet(text: string): boolean {
  return !/(?:sk-[\w-]+|bearer\s+[a-z0-9._-]+|api[_-]?key|token|secret|credential|password|passwd|~\/\.|\/Users\/|\/private\/|\/tmp\/|\.ssh|id_rsa|id_ed25519)/i.test(
    text,
  );
}

function extractFromShellResult(
  toolName: string,
  result: unknown,
): FactSummary | null {
  if (typeof result === "string") {
    const exitMatch = result.match(/exit code[:\s]+(-?\d+)/i);
    if (exitMatch) {
      const code = parseInt(exitMatch[1] ?? "1", 10);
      if (code !== 0) {
        return { kind: "exit_code", value: String(code) };
      }
    }
    const lines = extractLineCount(result);
    if (lines && lines > 1) {
      return { kind: "lines", value: String(lines) };
    }
    const text = result.trim();
    if (!text || text.length > MAX_TEXT_RESULT_LENGTH) return null;
    return isSafePublicSnippet(text)
      ? { kind: "text", value: truncateValue(text) }
      : null;
  }
  if (typeof result === "object" && result !== null && !Array.isArray(result)) {
    const record = result as Record<string, unknown>;
    const exitCode = extractExitCode(record);
    if (exitCode !== null) {
      if (exitCode !== 0) {
        return { kind: "exit_code", value: String(exitCode) };
      }
    }
    const stdout = typeof record.stdout === "string" ? record.stdout : null;
    const stderr = typeof record.stderr === "string" ? record.stderr : null;
    const output = stdout?.trim() ? stdout : stderr;
    if (output) {
      const lines = extractLineCount(output);
      if (lines && lines > 1) {
        return { kind: "lines", value: String(lines) };
      }
      const snippet = extractSnippetFromText(output);
      if (snippet && isSafePublicSnippet(snippet)) {
        return { kind: "text", value: snippet };
      }
    }
    const outputText = firstNonEmptyString(record, [
      "output",
      "result",
      "response",
    ]);
    if (outputText) {
      const lines = extractLineCount(outputText);
      if (lines && lines > 1) {
        return { kind: "lines", value: String(lines) };
      }
      const snippet = truncateSnippet(outputText);
      return isSafePublicSnippet(snippet)
        ? { kind: "text", value: snippet }
        : null;
    }
  }
  return null;
}

function extractFromReadResult(
  toolName: string,
  result: unknown,
): FactSummary | null {
  if (typeof result === "string") {
    const lines = extractLineCount(result);
    if (lines) {
      return { kind: "lines", value: String(lines) };
    }
    const snippet = extractSnippetFromText(result);
    if (snippet) {
      return { kind: "text", value: snippet };
    }
  }
  if (typeof result === "object" && result !== null && !Array.isArray(result)) {
    const record = result as Record<string, unknown>;
    const content = firstNonEmptyString(record, [
      "content",
      "file_content",
      "text",
    ]);
    if (content) {
      const lines = extractLineCount(content);
      if (lines) {
        return { kind: "lines", value: String(lines) };
      }
    }
    const lineCount = firstFiniteNumber(record, [
      "line_count",
      "lines",
      "total_lines",
    ]);
    if (lineCount !== null) {
      return { kind: "lines", value: String(lineCount) };
    }
    const path = firstNonEmptyString(record, [
      "path",
      "file",
      "filePath",
      "filename",
    ]);
    if (path) {
      return { kind: "path", value: truncateValue(pathBasename(path)) };
    }
  }
  return null;
}

function extractFromSearchResult(
  toolName: string,
  result: unknown,
): FactSummary | null {
  if (Array.isArray(result)) {
    return { kind: "matches", value: String(result.length) };
  }
  if (typeof result === "object" && result !== null) {
    const record = result as Record<string, unknown>;
    const matches = firstFiniteNumber(record, [
      "matches",
      "count",
      "total",
      "results",
      "num_matches",
    ]);
    if (matches !== null) {
      return { kind: "matches", value: String(matches) };
    }
    for (const key of ["results", "matches", "items", "files"]) {
      const value = record[key];
      if (Array.isArray(value)) {
        return { kind: "matches", value: String(value.length) };
      }
    }
  }
  if (typeof result === "string") {
    // Match grep-style "file:line:content" results, excluding URL schemes
    // (http:, https:, ftp:, file:, etc.) which would inflate match counts.
    const matchCount = (result.match(/^(?!\w+:\/\/)[^:\s]+:/gm) || []).length;
    if (matchCount > 0) {
      return { kind: "matches", value: String(matchCount) };
    }
  }
  return null;
}

function extractFromListResult(
  toolName: string,
  result: unknown,
): FactSummary | null {
  if (typeof result === "string") {
    const lines = result.split(/\r?\n/).filter((line) => line.trim());
    const hasLocalizedLongListSummary =
      lines.length > 1 &&
      /^\S+\s+\d+\s*$/.test(lines[0] ?? "") &&
      lines.slice(1).some((line) => /^[-dlcbps]/.test(line.trimStart()));
    const entries = hasLocalizedLongListSummary ? lines.slice(1) : lines;
    if (entries.length > 0) {
      return { kind: "count", value: String(entries.length) };
    }
  }
  if (Array.isArray(result)) {
    return { kind: "count", value: String(result.length) };
  }
  if (typeof result === "object" && result !== null) {
    const record = result as Record<string, unknown>;
    const count = firstFiniteNumber(record, [
      "count",
      "total",
      "entries",
      "files",
    ]);
    if (count !== null) {
      return { kind: "count", value: String(count) };
    }
    for (const key of ["entries", "files", "items"]) {
      const value = record[key];
      if (Array.isArray(value)) {
        return { kind: "count", value: String(value.length) };
      }
    }
  }
  return null;
}

export function extractFactSummary(
  toolName: string,
  result: unknown,
): FactSummary | null {
  if (result === null || result === undefined) return null;

  const name = toolName.toLowerCase();
  const isShellTool =
    COMMON_SHELL_TOOLS.has(name) ||
    name.includes("command") ||
    name.includes("exec");
  if (isShellTool) {
    const summary = extractFromShellResult(toolName, result);
    if (summary) return summary;
    if (isToolResultError(result)) return { kind: "failed", value: "" };
    if (typeof result === "string") return null;
  }

  if (isToolResultError(result)) {
    return { kind: "failed", value: "" };
  }

  if (
    COMMON_READ_TOOLS.has(name) ||
    /(?:^|[_-])read(?:[_-]|$)/.test(name) ||
    name === "cat" ||
    name === "view"
  ) {
    const summary = extractFromReadResult(toolName, result);
    if (summary) return summary;
  }

  if (COMMON_GLOB_TOOLS.has(name)) {
    const summary = extractFromListResult(toolName, result);
    if (summary) return summary;
  }

  if (
    COMMON_SEARCH_TOOLS.has(name) ||
    name.includes("search") ||
    name.includes("grep")
  ) {
    const summary = extractFromSearchResult(toolName, result);
    if (summary) return summary;
  }

  if (COMMON_LIST_TOOLS.has(name) || name === "ls" || name.includes("list")) {
    const summary = extractFromListResult(toolName, result);
    if (summary) return summary;
  }

  if (typeof result === "string") {
    const text = result.trim();
    if (!text || text.length > MAX_TEXT_RESULT_LENGTH) return null;
    return { kind: "text", value: truncateValue(text) };
  }

  if (Array.isArray(result)) {
    return { kind: "count", value: String(result.length) };
  }

  if (typeof result === "object") {
    const record = result as Record<string, unknown>;
    const success = firstBoolean(record, ["success", "ok"]);
    if (success === true) {
      return { kind: "succeeded", value: "" };
    }
    if (success === false) {
      return { kind: "failed", value: "" };
    }
    const path = firstNonEmptyString(record, [
      "path",
      "file",
      "filePath",
      "filename",
      "destination",
    ]);
    if (path) {
      return { kind: "path", value: truncateValue(pathBasename(path)) };
    }
    const count = firstFiniteNumber(record, [
      "count",
      "total",
      "affected",
      "processed",
    ]);
    if (count !== null) return { kind: "count", value: String(count) };
    const status = firstNonEmptyString(record, ["status", "state"]);
    if (status) return { kind: "status", value: truncateValue(status) };
    const title = firstNonEmptyString(record, ["title", "name", "summary"]);
    if (title) return { kind: "title", value: truncateValue(title) };
    const duration = firstFiniteNumber(record, [
      "duration_ms",
      "duration",
      "elapsed",
      "time_taken",
    ]);
    if (duration !== null) {
      return {
        kind: "duration",
        value:
          duration >= 1000
            ? `${(duration / 1000).toFixed(1)}s`
            : `${Math.round(duration)}ms`,
      };
    }
    const lines = firstFiniteNumber(record, ["lines", "line_count"]);
    if (lines !== null) return { kind: "lines", value: String(lines) };
    return null;
  }

  return null;
}
