import { queryOptions } from "@tanstack/react-query";
import {
  getCoderAccount,
  getCoderModels,
  getCoderRateLimits,
  getCoderUsage,
} from "./api";
import type { coderQueryKeys } from "./api";

type CoderKeys = ReturnType<typeof coderQueryKeys>;

export function coderModelsQueryOptions(keys: CoderKeys) {
  return queryOptions({
    queryKey: keys.models,
    queryFn: ({ signal }) => getCoderModels(signal),
    staleTime: 60_000,
    retry: false,
  });
}

export function coderAccountQueryOptions(keys: CoderKeys) {
  return queryOptions({
    queryKey: keys.account,
    queryFn: ({ signal }) => getCoderAccount(signal),
    staleTime: 5_000,
    retry: false,
  });
}

export function coderRateLimitsQueryOptions(keys: CoderKeys) {
  return queryOptions({
    queryKey: keys.rateLimits,
    queryFn: ({ signal }) => getCoderRateLimits(signal),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
}

export function coderUsageQueryOptions(keys: CoderKeys) {
  return queryOptions({
    queryKey: keys.usage,
    queryFn: ({ signal }) => getCoderUsage(signal),
    staleTime: 5 * 60_000,
    retry: false,
  });
}
