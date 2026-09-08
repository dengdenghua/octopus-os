export function taskWorkspaceRoute({
  threadId,
  agentId,
  prompt,
  workspacePath,
  artifact,
}: {
  threadId?: string | null;
  agentId?: string | null;
  prompt?: string | null;
  workspacePath?: string | null;
  artifact?: string | null;
} = {}) {
  const params = new URLSearchParams();
  const cleanPrompt = prompt?.trim() ?? "";
  const cleanAgent = agentId?.trim() ?? "";
  const cleanWorkspacePath = workspacePath?.trim() ?? "";
  const cleanArtifact = artifact?.trim() ?? "";
  const existingThread = threadId?.trim();
  if (cleanPrompt && !existingThread) params.set("prompt", cleanPrompt);
  if (cleanAgent && cleanAgent !== "general") params.set("agent", cleanAgent);
  if (cleanWorkspacePath) params.set("workspace_path", cleanWorkspacePath);
  if (cleanArtifact) params.set("artifact", cleanArtifact);
  const query = params.toString() ? `?${params.toString()}` : "";
  return `/workspace/realtime/${existingThread ? encodeURIComponent(existingThread) : "new"}${query}`;
}

/** An application location is not a task identity until its thread is created. */
export function threadIdForWorkspaceRoute(route: string): string | undefined {
  const match = route
    .split(/[?#]/)[0]
    ?.match(/^\/workspace\/realtime\/([^/]+)$/);
  if (!match?.[1]) return undefined;
  try {
    const id = decodeURIComponent(match[1]);
    return id && id !== "new" ? id : undefined;
  } catch {
    return undefined;
  }
}
