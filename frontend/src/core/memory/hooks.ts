import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { currentActorId } from "../auth/api";

import {
  clearMemory,
  createMemoryFact,
  deleteMemoryFact,
  getMemoryConfig,
  getMemoryAssetTrace,
  importMemory,
  listMemoryAssets,
  loadMemory,
  searchMemory,
  updateMemoryConfig,
  updateMemoryFact,
} from "./api";
import type {
  MemoryConfig,
  MemoryAssetQuery,
  MemoryConfigPatch,
  MemoryFactInput,
  MemoryFactPatchInput,
  MemorySearchResult,
  UserMemory,
} from "./types";

export function memoryAssetsQueryKey(
  query: MemoryAssetQuery,
  actor = currentActorId(),
) {
  return ["memory-assets", actor, query] as const;
}

export function memoryAssetTraceQueryKey(
  assetId: string | null,
  actor = currentActorId(),
) {
  return ["memory-asset-trace", actor, assetId] as const;
}

export function memorySearchQueryKey(
  query: string,
  limit: number,
  actor = currentActorId(),
) {
  return ["memory-search", actor, query, limit] as const;
}

export function memoryQueryKey(actor = currentActorId()) {
  return ["memory", actor] as const;
}

export function memoryConfigQueryKey(actor = currentActorId()) {
  return ["memory-config", actor] as const;
}

export function useMemoryAssets(query: MemoryAssetQuery) {
  return useQuery({
    queryKey: memoryAssetsQueryKey(query),
    queryFn: () => listMemoryAssets(query),
  });
}

export function useMemoryAssetTrace(assetId: string | null) {
  return useQuery({
    queryKey: memoryAssetTraceQueryKey(assetId),
    queryFn: () => getMemoryAssetTrace(assetId as string),
    enabled: Boolean(assetId),
  });
}

export function useSearchMemory(query: string, limit = 50) {
  const { data, isLoading, error } = useQuery({
    queryKey: memorySearchQueryKey(query, limit),
    queryFn: () => searchMemory(query, limit),
    enabled: query.trim().length > 0,
  });
  return {
    results: data ?? ([] as MemorySearchResult[]),
    isSearching: isLoading,
    error,
  };
}

export function useMemory() {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: memoryQueryKey(),
    queryFn: () => loadMemory(),
  });
  return {
    memory: data ?? null,
    isLoading,
    error,
    refetch,
    isRefreshing: isFetching && !isLoading,
  };
}

export function useMemoryConfig() {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: memoryConfigQueryKey(),
    queryFn: () => getMemoryConfig(),
  });
  return {
    config: data ?? null,
    isLoading,
    error,
    refetch,
    isRefreshing: isFetching && !isLoading,
  };
}

export function useUpdateMemoryConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (patch: MemoryConfigPatch) => updateMemoryConfig(patch),
    onSuccess: (config) => {
      queryClient.setQueryData<MemoryConfig>(memoryConfigQueryKey(), config);
    },
  });
}

export function useClearMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => clearMemory(),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(memoryQueryKey(), memory);
      void queryClient.invalidateQueries({
        queryKey: ["memory-assets", currentActorId()],
      });
    },
  });
}

export function useDeleteMemoryFact() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (factId: string) => deleteMemoryFact(factId),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(memoryQueryKey(), memory);
      void queryClient.invalidateQueries({
        queryKey: ["memory-assets", currentActorId()],
      });
    },
  });
}

export function useImportMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (memory: UserMemory) => importMemory(memory),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(memoryQueryKey(), memory);
      void queryClient.invalidateQueries({
        queryKey: ["memory-assets", currentActorId()],
      });
    },
  });
}

export function useCreateMemoryFact() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: MemoryFactInput) => createMemoryFact(input),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(memoryQueryKey(), memory);
      void queryClient.invalidateQueries({
        queryKey: ["memory-assets", currentActorId()],
      });
    },
  });
}

export function useUpdateMemoryFact() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      factId,
      input,
    }: {
      factId: string;
      input: MemoryFactPatchInput;
    }) => updateMemoryFact(factId, input),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(memoryQueryKey(), memory);
      void queryClient.invalidateQueries({
        queryKey: ["memory-assets", currentActorId()],
      });
    },
  });
}
