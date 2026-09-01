import { useQuery } from "@tanstack/react-query";

import { listPlugins, hubListPlugins } from "./api";
import type { PluginInfo, HubPluginInfo } from "./types";

/**
 * List all installed plugins (legacy Codex registry + PluginHub).
 *
 * Used by the chat input box's `/plugin` slash command picker.
 * Refetches on window focus so newly installed plugins show up.
 */
export function usePlugins(options?: { enabled?: boolean }) {
  const { data, isLoading, error, refetch } = useQuery<PluginInfo[]>({
    queryKey: ["plugins"],
    queryFn: listPlugins,
    staleTime: 30_000,
    enabled: options?.enabled ?? true,
  });
  return {
    plugins: data ?? [],
    isLoading,
    error,
    refetch,
  };
}

/**
 * List all PluginHub plugins (the new pluggable module architecture).
 * Use this when the workspace has migrated to PluginHub.
 */
export function useHubPlugins() {
  const { data, isLoading, error, refetch } = useQuery<HubPluginInfo[]>({
    queryKey: ["plugins", "hub"],
    queryFn: hubListPlugins,
    staleTime: 30_000,
  });
  return {
    plugins: data ?? [],
    isLoading,
    error,
    refetch,
  };
}
