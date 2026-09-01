import { useQuery } from "@tanstack/react-query";

import { listWorkspaceArtifactRefs } from "./workspace-outputs";

/**
 * Shared React Query hook for workspace artifacts. Consolidates the query
 * configuration that was previously duplicated across chat-box.tsx and
 * context.tsx, reducing redundant API calls.
 */
export function useWorkspaceArtifacts(
  threadId: string | null | undefined,
  options?: {
    enabled?: boolean;
    refetchInterval?: number | false;
  },
) {
  const enabled =
    options?.enabled !== false && Boolean(threadId && threadId !== "new");

  return useQuery({
    queryKey: ["workspace-artifacts", threadId],
    queryFn: ({ signal }) => listWorkspaceArtifactRefs(threadId!, signal),
    enabled,
    refetchInterval: options?.refetchInterval,
    // Artifact discovery is presentation state, not task execution. The
    // backend keeps working while the tab is hidden; refresh once the tab is
    // visible again instead of polling every three seconds in the background.
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    staleTime: 3000,
    gcTime: 30000,
  });
}
