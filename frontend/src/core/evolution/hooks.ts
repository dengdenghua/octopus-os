import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getEvolutionOverview,
  getEvolutionStory,
  getLearningCurve,
  getSkillPerformance,
  getMemoryGrowth,
  getRecommendations,
  getFitness,
  getDrift,
  getLedger,
  getCanary,
  rollbackCanary,
} from "./api";
import { queryKeys } from "@/core/api/query-keys";

export function useEvolutionOverview(options: { enabled?: boolean } = {}) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.evolution.overview,
    queryFn: getEvolutionOverview,
    enabled: options.enabled ?? true,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
  return { data: data ?? null, isLoading, error, refetch };
}

export function useEvolutionStory() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...queryKeys.evolution.overview, "story"],
    queryFn: getEvolutionStory,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
  return { data: data ?? null, isLoading, error, refetch };
}

export function useLearningCurve(weeks?: number) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...queryKeys.evolution.learningCurve, weeks],
    queryFn: () => getLearningCurve(weeks),
    retry: false,
  });
  return { data: data ?? null, isLoading, error, refetch };
}

export function useSkillPerformance() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.evolution.skills,
    queryFn: getSkillPerformance,
    retry: false,
  });
  return { data: data ?? null, isLoading, error, refetch };
}

export function useMemoryGrowth(days?: number) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...queryKeys.evolution.memory, days],
    queryFn: () => getMemoryGrowth(days),
    retry: false,
  });
  return { data: data ?? null, isLoading, error, refetch };
}

export function useRecommendations() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.evolution.recommendations,
    queryFn: getRecommendations,
    retry: false,
  });
  return { data: data ?? null, isLoading, error, refetch };
}

export function useFitness(agentId: string | undefined, window?: number) {
  const { data, isLoading, error } = useQuery({
    queryKey: [...queryKeys.evolution.overview, "fitness", agentId, window],
    queryFn: () => getFitness(agentId!, window),
    enabled: !!agentId,
  });
  return { data: data ?? null, isLoading, error };
}

export function useDrift(agentId: string | undefined) {
  const { data, isLoading, error } = useQuery({
    queryKey: [...queryKeys.evolution.overview, "drift", agentId],
    queryFn: () => getDrift(agentId!),
    enabled: !!agentId,
  });
  return { data: data ?? null, isLoading, error };
}

export function useLedger(opts?: { limit?: number; offset?: number }) {
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: [...queryKeys.evolution.overview, "ledger", opts],
    queryFn: () => getLedger(opts),
  });
  return { data: data ?? null, isLoading, isFetching, error, refetch };
}

export function useCanary() {
  const { data, isLoading, error } = useQuery({
    queryKey: [...queryKeys.evolution.overview, "canary"],
    queryFn: getCanary,
  });
  return { data: data ?? null, isLoading, error };
}

export function useRollbackCanary() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (skillName: string) => rollbackCanary(skillName),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...queryKeys.evolution.overview, "canary"],
      });
      void queryClient.invalidateQueries({
        queryKey: [...queryKeys.evolution.overview, "ledger"],
      });
    },
  });
}
