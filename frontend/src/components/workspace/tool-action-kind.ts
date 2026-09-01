export type ToolActionKind =
  | "search"
  | "read"
  | "call"
  | "skill"
  | "create"
  | "write"
  | "edit"
  | "list"
  | "run"
  | "browse"
  | "fetch"
  | "update"
  | "learn"
  | "plan"
  | "other";

export function isSkillToolName(name: string): boolean {
  const normalized = name.toLowerCase();
  return (
    normalized === "apply_skill" ||
    normalized === "list_learned_skills" ||
    normalized === "learn_skill_from_text" ||
    normalized === "deep-research-swarm" ||
    normalized === "deep-research" ||
    normalized === "report-writing" ||
    normalized === "docx" ||
    normalized === "pptx-swarm" ||
    normalized === "webapp-building-swarm" ||
    normalized === "skill_search" ||
    normalized === "install_skill" ||
    normalized.includes("skill")
  );
}

export function inferToolActionKind(
  name: string,
  args: Record<string, unknown> = {},
): ToolActionKind {
  const normalized = name.toLowerCase();
  if (
    normalized.includes("planning") ||
    normalized === "plan" ||
    normalized === "planning"
  ) {
    return "plan";
  }
  if (isSkillToolName(normalized)) {
    return normalized.includes("learn") ? "learn" : "skill";
  }
  if (
    normalized === "web_search" ||
    normalized === "image_search" ||
    normalized.includes("search") ||
    normalized.includes("grep") ||
    normalized.includes("glob")
  ) {
    return "search";
  }
  if (normalized === "web_fetch" || normalized.includes("fetch")) {
    return "fetch";
  }
  if (
    normalized === "ls" ||
    normalized === "list_cwd" ||
    normalized.includes("list") ||
    normalized.includes("cwd")
  ) {
    return "list";
  }
  if (
    normalized === "read_file" ||
    normalized === "read_file_range" ||
    normalized === "read_text_file" ||
    normalized.includes("read") ||
    normalized.includes("view")
  ) {
    return "read";
  }
  if (
    normalized.includes("edit") ||
    normalized.includes("replace") ||
    normalized === "str_replace"
  ) {
    return "edit";
  }
  if (normalized.includes("create") || normalized.includes("new_file")) {
    return "create";
  }
  if (normalized.includes("write") || normalized.includes("append")) {
    return "write";
  }
  if (
    normalized === "bash" ||
    normalized === "exec_shell" ||
    normalized === "mcp_exec_shell" ||
    normalized === "shell_command" ||
    normalized.includes("run") ||
    normalized.includes("shell") ||
    normalized.includes("exec")
  ) {
    return "run";
  }
  if (normalized.includes("browse") || normalized.includes("url")) {
    return "browse";
  }
  if (normalized.includes("update") || normalized.includes("todo")) {
    return "update";
  }
  if (normalized.includes("agent") || normalized.includes("call_")) {
    return "call";
  }
  if (Object.keys(args).length > 0 && "query" in args) {
    return "search";
  }
  return "call";
}

export function inferToolActionKindFromText(text: string): ToolActionKind {
  const trimmed = text.trim();
  const actionMatch = /^Action:\s*([A-Za-z0-9_-]+)/i.exec(trimmed);
  if (actionMatch?.[1]) {
    return inferToolActionKind(actionMatch[1], {});
  }
  if (/搜索|查找|search/i.test(trimmed)) return "search";
  if (/web_fetch|fetch_url|\bfetch\b/i.test(trimmed)) return "fetch";
  if (/读取|read|查看|浏览/i.test(trimmed)) return "read";
  if (/技能|skill/i.test(trimmed)) return "skill";
  if (/创建|create|new file|生成文件/i.test(trimmed)) return "create";
  if (/写入|write|append/i.test(trimmed)) return "write";
  if (/编辑|edit|替换|replace/i.test(trimmed)) return "edit";
  if (/列出|list/i.test(trimmed)) return "list";
  if (/执行|run|bash|shell/i.test(trimmed)) return "run";
  if (/调用|call|invoke/i.test(trimmed)) return "call";
  if (
    /\u89c4\u5212|\u4e0b\u4e00\u6b65|\bplanning\b|\bplan next\b|\bmake a plan\b/i.test(
      trimmed,
    )
  )
    return "plan";
  return "other";
}
