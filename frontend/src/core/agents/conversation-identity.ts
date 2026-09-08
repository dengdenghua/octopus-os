/** New tasks follow the selected persona; an existing task keeps its owner. */
export function conversationAgentId({
  isNewThread,
  threadId,
  activeAgentId,
  threadOwnerAgentId,
}: {
  isNewThread: boolean;
  threadId: string;
  activeAgentId: string;
  threadOwnerAgentId?: string | null;
}): string {
  if (isNewThread) return activeAgentId;
  if (threadId === "echo-assistant") return "echo";
  return threadOwnerAgentId?.trim() || activeAgentId;
}
