import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { enableMarketSkill, enableSkill } from "./api";

import { loadSkills } from ".";

export function useSkills(options?: { enabled?: boolean }) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["skills"],
    queryFn: () => loadSkills(),
    enabled: options?.enabled ?? true,
  });
  return { skills: data ?? [], isLoading, isFetching, error, refetch };
}

export function useEnableSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      skillName,
      enabled,
    }: {
      skillName: string;
      enabled: boolean;
    }) => {
      await enableSkill(skillName, enabled);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

export function useEnableMarketSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (skillName: string) => {
      await enableMarketSkill(skillName);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}
