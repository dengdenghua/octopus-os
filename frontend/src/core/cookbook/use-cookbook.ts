/**
 * Local-model cookbook: hardware-aware recommendations + one-click pull.
 *
 * `/api/cookbook/snapshot` (public) returns detected hardware + ranked models;
 * `/api/cookbook/pull` (auth-gated) triggers a background ollama pull. The global
 * fetch interceptor supplies the bearer token.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getBackendBaseURL } from "@/core/config";

export interface CookbookHardware {
  backend: string;
  gpu_name?: string | null;
  vram_gb: number;
  ram_gb: number;
  bandwidth_gbps?: number | null;
  unified_memory: boolean;
  note?: string | null;
}

export interface CookbookRec {
  tag: string;
  label: string;
  params_b: number;
  quant: string;
  est_mem_gb: number;
  fits: boolean;
  verdict: string;
  est_tokens_per_s?: number | null;
  installed: boolean;
  score: number;
  family: string;
}

export interface CookbookSnapshot {
  hardware: CookbookHardware | null;
  ollama_available: boolean;
  recommendations: CookbookRec[];
  pulls: Record<string, string>;
  source?: string; // "huggingface" (live) | "static" (snapshot / cold cache)
}

const KEY = ["cookbook-snapshot"] as const;

const EMPTY: CookbookSnapshot = {
  hardware: null,
  ollama_available: false,
  recommendations: [],
  pulls: {},
  source: "static",
};

async function fetchSnapshot(signal?: AbortSignal): Promise<CookbookSnapshot> {
  const res = await fetch(`${getBackendBaseURL()}/api/cookbook/snapshot`, { signal });
  if (!res.ok) return EMPTY;
  return (await res.json()) as CookbookSnapshot;
}

export function useCookbook(): {
  snapshot: CookbookSnapshot | undefined;
  isLoading: boolean;
} {
  const { data, isLoading } = useQuery({
    queryKey: KEY,
    queryFn: ({ signal }) => fetchSnapshot(signal),
    // Poll while a pull is in flight, or while the catalog is still the cold-start
    // static fallback (the live HuggingFace list arrives a moment later); stop once
    // it's live and idle.
    refetchInterval: (query) => {
      const s = query.state.data;
      if (!s) return 6_000;
      const pulling = Object.values(s.pulls || {}).some((v) => v === "pulling");
      const awaitingLive = s.source !== "huggingface";
      return pulling || awaitingLive ? 6_000 : false;
    },
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });
  return { snapshot: data, isLoading };
}

export function useCookbookPull(): {
  pull: (tag: string) => void;
  pendingTag: string | null;
} {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: async (tag: string) => {
      const res = await fetch(`${getBackendBaseURL()}/api/cookbook/pull`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag }),
      });
      if (!res.ok) throw new Error(`pull failed: ${res.status}`);
      return res.json();
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY });
    },
  });
  return {
    pull: (tag: string) => m.mutate(tag),
    pendingTag: m.isPending ? ((m.variables as string) ?? null) : null,
  };
}
