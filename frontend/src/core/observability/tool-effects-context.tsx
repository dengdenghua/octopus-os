import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useMemo, type ReactNode } from "react";

import {
  GlobalControlPlaneAccessError,
  getToolEffectsSnapshot,
  type ToolEffectReceipt,
  type ToolEffectsSnapshot,
} from "./api";

export const TOOL_EFFECTS_QUERY_KEY = [
  "observability",
  "tool-effects",
] as const;

const EMPTY_RECEIPTS: ToolEffectReceipt[] = [];
const EMPTY_SNAPSHOT: ToolEffectsSnapshot = {
  backend: "disabled",
  shared_across_hosts: false,
  can_authorize_retry: false,
  count: 0,
  state_counts: {},
  receipts: EMPTY_RECEIPTS,
};

type ToolEffectsContextValue = {
  snapshot: ToolEffectsSnapshot;
  receiptsByCallId: ReadonlyMap<string, ToolEffectReceipt>;
  receiptsByEffectKey: ReadonlyMap<string, ToolEffectReceipt>;
  loading: boolean;
  error: Error | null;
  refresh: () => void;
};

const EMPTY_CONTEXT: ToolEffectsContextValue = {
  snapshot: EMPTY_SNAPSHOT,
  receiptsByCallId: new Map(),
  receiptsByEffectKey: new Map(),
  loading: false,
  error: null,
  refresh: () => undefined,
};

const ToolEffectsContext =
  createContext<ToolEffectsContextValue>(EMPTY_CONTEXT);

export function toolEffectsRefetchInterval(
  active: boolean,
  receipts: readonly Pick<ToolEffectReceipt, "state">[],
): number | false {
  if (!active) return false;
  return receipts.some(
    (receipt) =>
      receipt.state === "claimed" ||
      receipt.state === "started" ||
      receipt.state === "retry_authorized",
  )
    ? 3_000
    : 5_000;
}

export function ToolEffectsProvider({
  children,
  enabled = true,
  active = false,
}: {
  children: ReactNode;
  enabled?: boolean;
  active?: boolean;
}) {
  const query = useQuery({
    queryKey: TOOL_EFFECTS_QUERY_KEY,
    queryFn: ({ signal }) => getToolEffectsSnapshot({ limit: 100, signal }),
    enabled,
    // This snapshot is global rather than thread-scoped and may require a
    // database scan. Keep it warm across route changes, but only poll while a
    // turn is active. React Query pauses the cadence for hidden tabs.
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: (current) =>
      active && !(current.state.error instanceof GlobalControlPlaneAccessError),
    refetchOnReconnect: (current) =>
      active && !(current.state.error instanceof GlobalControlPlaneAccessError),
    refetchIntervalInBackground: false,
    retry: (failureCount, error) =>
      !(error instanceof GlobalControlPlaneAccessError) && failureCount < 1,
    refetchInterval: (current) => {
      if (current.state.error instanceof GlobalControlPlaneAccessError) {
        return false;
      }
      const receipts = current.state.data?.receipts ?? EMPTY_RECEIPTS;
      return toolEffectsRefetchInterval(active, receipts);
    },
  });
  const snapshot = enabled ? (query.data ?? EMPTY_SNAPSHOT) : EMPTY_SNAPSHOT;
  const { error: queryError, isFetching, isPending, refetch } = query;
  const value = useMemo<ToolEffectsContextValue>(() => {
    const receiptsByCallId = new Map<string, ToolEffectReceipt>();
    const receiptsByEffectKey = new Map<string, ToolEffectReceipt>();
    for (const receipt of snapshot.receipts) {
      receiptsByEffectKey.set(receipt.effect_key, receipt);
      if (receipt.call_id) receiptsByCallId.set(receipt.call_id, receipt);
    }
    return {
      snapshot,
      receiptsByCallId,
      receiptsByEffectKey,
      loading: isPending || isFetching,
      error: queryError instanceof Error ? queryError : null,
      refresh: () => void refetch(),
    };
  }, [isFetching, isPending, queryError, refetch, snapshot]);

  return (
    <ToolEffectsContext.Provider value={value}>
      {children}
    </ToolEffectsContext.Provider>
  );
}

export function useToolEffects(): ToolEffectsContextValue {
  return useContext(ToolEffectsContext);
}
