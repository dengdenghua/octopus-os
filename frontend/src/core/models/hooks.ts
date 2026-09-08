import { currentActorId } from "@/core/auth/api";
import { useQuery } from "@tanstack/react-query";

import { loadModels } from "./api";

export function modelsQueryKey(actor = currentActorId()) {
  return ["models", actor] as const;
}

export function useModels({ enabled = true }: { enabled?: boolean } = {}) {
  const actor = currentActorId();
  const query = useQuery({
    queryKey: modelsQueryKey(actor),
    queryFn: () => loadModels(),
    enabled,
    refetchOnWindowFocus: false,
    // Model list changes only when the user edits config.yaml — that's
    // minutes-scale, not seconds. Keeping the previous default (staleTime: 0)
    // meant every workspace page mount re-fetched this, adding a round-trip
    // to the Code-page startup burst. 5 minutes is generous enough to
    // catch config-yaml edits on next navigation without re-fetching on
    // every remount.
    staleTime: 5 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  });
  return {
    models: query.data ?? [],
    isLoading: query.isLoading,
    error: query.error,
    refetch: query.refetch,
  };
}
