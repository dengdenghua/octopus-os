import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getBackendBaseURL } from "@/core/config";

export interface ReachChannel {
  platform: string;
  available: boolean;
  backend: string;
  detail?: string;
  requires_login?: boolean;
}

export interface ReachStatus {
  ok: boolean;
  healthy: number;
  total: number;
  collection_count: number;
  channels: ReachChannel[];
}

const REACH_STATUS_KEY = ["reach-status"] as const;

async function fetchReachStatus(signal?: AbortSignal): Promise<ReachStatus> {
  const response = await fetch(`${getBackendBaseURL()}/api/reach/status`, {
    signal,
  });
  if (!response.ok) throw new Error(`reach status failed: ${response.status}`);
  return (await response.json()) as ReachStatus;
}

export function useReachStatus() {
  const query = useQuery({
    queryKey: REACH_STATUS_KEY,
    queryFn: ({ signal }) => fetchReachStatus(signal),
    refetchInterval: 30_000,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
  return {
    status: query.data,
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: () => void query.refetch(),
  };
}

export function useClearReachCache() {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${getBackendBaseURL()}/api/reach/cache`, {
        method: "DELETE",
      });
      if (!response.ok)
        throw new Error(`clear reach cache failed: ${response.status}`);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: REACH_STATUS_KEY }),
  });
  return { clear: mutation.mutateAsync, isPending: mutation.isPending };
}
