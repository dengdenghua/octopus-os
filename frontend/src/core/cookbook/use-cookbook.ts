/**
 * Local-model cookbook: hardware-aware recommendations + one-click pull.
 *
 * `/api/cookbook/snapshot` (public) returns detected hardware + ranked models;
 * `/api/cookbook/pull` (auth-gated) triggers a background ollama pull. The global
 * fetch interceptor supplies the bearer token.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { getBackendBaseURL } from "@/core/config";
import { jsonAuthHeaders } from "@/core/auth/api";
import { useAuth } from "@/providers/AuthProvider";
import { coderQueryKeys, updateCoderModelProfile } from "@/core/coder/api";
import { modelsQueryKey } from "@/core/models/hooks";
import { getLocalSettings, saveLocalSettings } from "@/core/settings/local";

export interface CookbookHardware {
  backend: string;
  gpu_name?: string | null;
  vram_gb: number;
  ram_gb: number;
  bandwidth_gbps?: number | null;
  unified_memory: boolean;
  note?: string | null;
  available_ram_gb?: number | null;
  reserve_gb?: number;
  context_tokens?: number;
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
  verifications?: Record<string, CookbookVerification>;
}

export interface CookbookVerification {
  supports_tool_use: boolean;
  supports_vision: boolean;
  latency_ms: number;
  verified_at: number;
}

const KEY = ["cookbook-snapshot"] as const;

async function fetchSnapshot(
  contextTokens: number,
  signal?: AbortSignal,
): Promise<CookbookSnapshot> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/cookbook/snapshot?context_tokens=${contextTokens}`,
    { signal },
  );
  if (!res.ok) throw new Error(`读取本机模型状态失败 (${res.status})`);
  return (await res.json()) as CookbookSnapshot;
}

export function useCookbook(contextTokens = 4096): {
  snapshot: CookbookSnapshot | undefined;
  isLoading: boolean;
  error: Error | null;
} {
  const { data, isLoading, error } = useQuery({
    queryKey: [...KEY, contextTokens],
    queryFn: ({ signal }) => fetchSnapshot(contextTokens, signal),
    // Poll while a pull is in flight, or while the catalog is still the cold-start
    // static fallback (the live HuggingFace list arrives a moment later); stop once
    // it's live and idle.
    refetchInterval: (query) => {
      const s = query.state.data;
      if (!s) return 6_000;
      const pulling = Object.values(s.pulls || {}).some(
        (v) => v === "pulling" || v === "verifying",
      );
      return pulling ? 2_000 : 30_000;
    },
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });
  return { snapshot: data, isLoading, error };
}

async function post(path: string, tag: string) {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ tag }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status === "error" || data.ok === false) {
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : data.error || `本机模型操作失败 (${res.status})`,
    );
  }
  return data;
}

export async function activateLocalModel(
  tag: string,
  stillCurrent = () => true,
) {
  const data = await post("/api/config/local-models/activate", tag);
  if (
    data.ok !== true ||
    typeof data.selection_id !== "string" ||
    !data.selection_id.startsWith("echo-custom-model:v1:")
  ) {
    throw new Error("本机模型接入结果无效，默认模型未修改");
  }
  if (!stillCurrent()) throw new Error("登录用户已变化，请重新选择默认模型");
  const profile = await updateCoderModelProfile({
    source: "follow_system",
    model: data.selection_id,
  });
  if (
    profile.source !== "follow_system" ||
    profile.selected_model !== data.selection_id
  ) {
    throw new Error("默认模型保存结果不一致，请刷新设置后重试");
  }
  return { profile, selectionId: data.selection_id as string };
}

export function useCookbookPull(): {
  pull: (tag: string) => void;
  pendingTag: string | null;
  verify: (tag: string) => void;
  activate: (tag: string) => void;
  error: Error | null;
  activatedTag: string | null;
} {
  const qc = useQueryClient();
  const { user } = useAuth();
  const principalKey = user?.actor_id || user?.user_id || "local";
  const principalRef = useRef(principalKey);
  principalRef.current = principalKey;
  const m = useMutation({
    mutationFn: async ({
      tag,
      action,
      principalKey: requestedPrincipal,
    }: {
      tag: string;
      action: "pull" | "verify" | "activate";
      principalKey: string;
    }) => {
      if (action === "activate")
        return activateLocalModel(
          tag,
          () => requestedPrincipal === principalRef.current,
        );
      const result = await post(`/api/cookbook/${action}`, tag);
      if (!["started", "already_pulling"].includes(result.status))
        throw new Error("模型准备任务未启动");
      return null;
    },
    onSuccess: (result, variables) => {
      if (!result || variables.principalKey !== principalRef.current) return;
      qc.setQueryData(
        coderQueryKeys(variables.principalKey).profile,
        result.profile,
      );
      const settings = getLocalSettings();
      saveLocalSettings({
        ...settings,
        context: { ...settings.context, model_name: result.selectionId },
      });
      void qc.invalidateQueries({
        queryKey: modelsQueryKey(variables.principalKey),
      });
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY });
    },
  });
  return {
    pull: (tag: string) => m.mutate({ tag, action: "pull", principalKey }),
    verify: (tag: string) => m.mutate({ tag, action: "verify", principalKey }),
    activate: (tag: string) =>
      m.mutate({ tag, action: "activate", principalKey }),
    pendingTag: m.isPending ? m.variables.tag : null,
    error: m.error,
    activatedTag:
      m.isSuccess && m.variables.action === "activate" ? m.variables.tag : null,
  };
}
