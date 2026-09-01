export function taskWorkspaceRoute({
  agentId,
  prompt,
  workspacePath,
}: {
  agentId?: string | null;
  prompt?: string | null;
  workspacePath?: string | null;
} = {}) {
  const params = new URLSearchParams();
  const cleanPrompt = prompt?.trim() ?? "";
  const cleanAgent = agentId?.trim() ?? "";
  const cleanWorkspacePath = workspacePath?.trim() ?? "";
  if (cleanPrompt) params.set("prompt", cleanPrompt);
  if (cleanAgent && cleanAgent !== "general") params.set("agent", cleanAgent);
  if (cleanWorkspacePath) params.set("workspace_path", cleanWorkspacePath);
  const query = params.toString() ? `?${params.toString()}` : "";
  return `/workspace/realtime/new${query}`;
}
