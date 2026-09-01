import { useQuery } from "@tanstack/react-query";

import { listCapabilities } from "@/core/agents/agent-world-api";

const SURFACE_PLUGIN_ID = "echo-recorder";
export const CAPABILITY_SURFACE_QUERY_KEY = [
  "capability-surface",
  SURFACE_PLUGIN_ID,
] as const;

/** Resolve an optional UI surface from the installed + enabled plugin state. */
export function useCapabilitySurface(surface: string): boolean {
  const query = useQuery({
    queryKey: CAPABILITY_SURFACE_QUERY_KEY,
    queryFn: async () => {
      const result = await listCapabilities({
        search: SURFACE_PLUGIN_ID,
        source: "codex_plugin",
        limit: 20,
      });
      return (
        result.capabilities.find((item) => item.id === SURFACE_PLUGIN_ID) ??
        null
      );
    },
    // Installation state changes only through explicit plugin lifecycle
    // actions or an external process. Lifecycle actions invalidate this key;
    // focus/reconnect catches external changes. Polling every five seconds
    // kept the chat and browser surfaces issuing requests for the entire
    // lifetime of a long task without improving correctness.
    staleTime: Number.POSITIVE_INFINITY,
    refetchInterval: false,
    refetchOnWindowFocus: "always",
    refetchOnReconnect: "always",
    retry: false,
  });

  const capability = query.data;
  return Boolean(
    capability?.installed &&
    capability.enabled &&
    capability.surface_capabilities?.includes(surface),
  );
}
