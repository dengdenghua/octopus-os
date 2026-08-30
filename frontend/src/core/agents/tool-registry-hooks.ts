import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listCapabilityPermissions,
  getAgentToolRegistry,
  listArms,
  saveAgentToolRegistry,
  updateCapabilityPermission,
  type ToolRegistry,
} from "./tool-registry-api";

export function useArms() {
  return useQuery({
    queryKey: ["arms"],
    queryFn: () => listArms(),
    staleTime: 5 * 60_000,
  });
}

export function useAgentToolRegistry(agentId: string | null | undefined) {
  return useQuery({
    queryKey: ["agent-tool-registry", agentId],
    queryFn: () => getAgentToolRegistry(agentId as string),
    enabled: Boolean(agentId),
  });
}

export function useSaveAgentToolRegistry(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ToolRegistry) => saveAgentToolRegistry(agentId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["agent-tool-registry", agentId],
      });
      // Agent detail/list also changes (tool_groups, arms) after save
      void queryClient.invalidateQueries({
        queryKey: ["agent-detail", agentId],
      });
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useCapabilityPermissions() {
  return useQuery({
    queryKey: ["capability-permissions"],
    queryFn: () => listCapabilityPermissions(),
    staleTime: 30_000,
  });
}

export function useUpdateCapabilityPermission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ group, enabled }: { group: string; enabled: boolean }) =>
      updateCapabilityPermission(group, enabled),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["capability-permissions"],
      });
    },
  });
}
