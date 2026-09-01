export const SEARCH_TOOL_NAMES = new Set(["web_search"]);

export const READ_TOOL_NAMES = new Set([
  "read_file",
  "read_text_file",
  "read_file_range",
  "ls",
  "list_cwd",
  "glob",
  // Tool providers use a few aliases for the same file-discovery operation.
  // Keep these in the canonical group so the conversation never falls back
  // to rendering an implementation name such as `glob_files`.
  "glob_files",
  "find_files",
  "file_glob",
  "grep",
  "grep_files",
  "search_files",
  "find",
  "tree",
  "file_stats",
]);

export const WRITE_TOOL_NAMES = new Set([
  "write_text_file",
  "write_file",
  "create_file",
]);

export const EDIT_TOOL_NAMES = new Set([
  "edit_code",
  "edit_text_file",
  "str_replace",
  "str_replace_editor",
  "edit",
  "edit_file",
  "apply_patch",
]);

export const SHELL_TOOL_NAMES = new Set([
  "bash",
  "shell",
  "exec",
  "exec_shell",
  "mcp_exec_shell",
  "run_command",
  "shell_command",
]);

export function isSearchToolName(name: string): boolean {
  return SEARCH_TOOL_NAMES.has(name);
}

export function isReadToolName(name: string): boolean {
  return READ_TOOL_NAMES.has(name);
}

export function isWriteToolName(name: string): boolean {
  return WRITE_TOOL_NAMES.has(name);
}

export function isEditToolName(name: string): boolean {
  return EDIT_TOOL_NAMES.has(name);
}

export function isFileMutationToolName(name: string): boolean {
  return isWriteToolName(name) || isEditToolName(name);
}

export function isShellToolName(name: string): boolean {
  return SHELL_TOOL_NAMES.has(name);
}

export function shellCommandFromInput(
  input: Record<string, unknown> | undefined,
  toolName?: string,
): string | undefined {
  if (!input) return undefined;
  for (const key of ["command", "cmd", "script", "inputPreview"]) {
    const value = input[key];
    if (typeof value !== "string") continue;
    const trimmed = value.trim();
    if (trimmed && trimmed !== toolName) return trimmed;
  }
  return undefined;
}
