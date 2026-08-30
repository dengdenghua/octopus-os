import type { CommandExecutionItem } from "@/core/realtime/items";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function commandExecutionToolName(item: CommandExecutionItem): string {
  return item.command || "command";
}

export function commandExecutionInput(
  item: CommandExecutionItem,
): Record<string, unknown> {
  const input = isRecord(item.inputPreview) ? { ...item.inputPreview } : {};
  if (item.inputPreview !== undefined && !isRecord(item.inputPreview)) {
    input.inputPreview = item.inputPreview;
  }
  if (!("command" in input)) input.command = commandExecutionToolName(item);
  if (!("tool" in input)) input.tool = commandExecutionToolName(item);
  if (item.cwd !== null && !("cwd" in input)) input.cwd = item.cwd;
  if (!("networkAccess" in input)) input.networkAccess = item.networkAccess;
  return input;
}
