import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { currentActorId } from "@/core/auth/api";

import {
  listCapabilityPermissions,
  getAgentToolRegistry,
  listArms,
  saveAgentToolRegistry,
  updateCapabilityPermission,
  type ToolRegistry,
} from "./tool-registry-api";

export function agentToolRegistryQueryKey(
  agentId: string | null | undefined,
  actor = currentActorId(),
) {
  return ["agent-tool-registry", actor, agentId ?? ""] as const;
}

export function capabilityPermissionsQueryKey() {
  // Capability switches are device-wide runtime policy, not account data.
  return ["capability-permissions"] as const;
}

export function useArms() {
  return useQuery({
    queryKey: ["arms"],
    queryFn: () => listArms(),
    staleTime: 5 * 60_000,
  });
}

export function useAgentToolRegistry(agentId: string | null | undefined) {
  const actor = currentActorId();
  return useQuery({
    queryKey: agentToolRegistryQueryKey(agentId, actor),
    queryFn: () => getAgentToolRegistry(agentId as string),
    enabled: Boolean(agentId),
  });
}

export function useSaveAgentToolRegistry(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ToolRegistry) => saveAgentToolRegistry(agentId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent-tool-registry"] });
      // Agent detail/list also changes (tool_groups, arms) after save
      void queryClient.invalidateQueries({
        queryKey: ["agents"],
      });
    },
  });
}

export function useCapabilityPermissions() {
  return useQuery({
    queryKey: capabilityPermissionsQueryKey(),
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
        queryKey: capabilityPermissionsQueryKey(),
      });
    },
  });
}
