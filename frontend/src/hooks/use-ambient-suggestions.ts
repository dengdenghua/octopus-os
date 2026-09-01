/**
 * useAmbientSuggestions · read + mutate ambient suggestions for a
 * project.
 *
 * Thin wrapper over ``/api/ambient-suggestions``. Gates on the
 * backend's ``ui.ambient_suggestions`` flag: when the server
 * returns ``enabled: false`` we surface that so the UI can show
 * a "feature disabled" hint rather than just an empty list.
 *
 * The hook is intentionally "dumb" — no TanStack Query, no global
 * store. Components that need sharing can lift the state up or
 * wrap in a context themselves. Keeps the dependency surface tiny
 * for what's still an experimental feature.
 */

import type { Locale } from "@/core/i18n";
import { swallow } from "@/core/utils/log";
import { useCallback, useEffect, useState } from "react";

export interface AmbientSuggestion {
  id: string;
  project_root: string;
  title: string;
  description: string;
  prompt: string;
  locale: Locale;
  status: "pending" | "accepted" | "dismissed" | string;
  source_turn_ids: string[];
  created_at: string;
  updated_at: string;
  model: string | null;
  experimental: boolean;
}

export interface AmbientSuggestionsBucket {
  project_root: string;
  generated_at: string;
  suggestions: AmbientSuggestion[];
  enabled: boolean;
}

export interface AmbientSuggestionsState {
  bucket: AmbientSuggestionsBucket | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  generate: (
    agentId: string,
    opts?: {
      model?: string;
      turnWindow?: number;
      locale?: Locale;
    },
  ) => Promise<{
    added: number;
    generated: number;
    error: string | null;
  }>;
  setStatus: (
    id: string,
    status: "pending" | "accepted" | "dismissed",
  ) => Promise<void>;
  clear: (onlyStatus?: "pending" | "accepted" | "dismissed") => Promise<void>;
}

export interface UseAmbientSuggestionsOptions {
  baseUrl?: string;
  /** Current global UI locale. Keeps generated content and cache aligned. */
  locale?: Locale;
  /**
   * If ``true`` (default), fetch once on mount (and whenever
   * ``project`` changes). ``false`` means the caller drives via
   * ``refresh()`` — useful when the component that owns this hook
   * isn't always mounted.
   */
  auto?: boolean;
}

export function useAmbientSuggestions(
  project: string | null,
  options: UseAmbientSuggestionsOptions = {},
): AmbientSuggestionsState {
  const { baseUrl = "", auto = true, locale } = options;
  const [bucket, setBucket] = useState<AmbientSuggestionsBucket | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!project) {
      setBucket(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ project });
      if (locale) params.set("locale", locale);
      const url = `${baseUrl}/api/ambient-suggestions?${params.toString()}`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const body = (await resp.json()) as AmbientSuggestionsBucket;
      setBucket(body);
    } catch (exc) {
      swallow(exc);
      setError(exc instanceof Error ? exc.message : String(exc));
      setBucket(null);
    } finally {
      setLoading(false);
    }
  }, [baseUrl, locale, project]);

  const generate = useCallback(
    async (
      agentId: string,
      opts?: { model?: string; turnWindow?: number; locale?: Locale },
    ) => {
      if (!project) {
        return { added: 0, generated: 0, error: "project required" };
      }
      const resp = await fetch(`${baseUrl}/api/ambient-suggestions/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project,
          agent_id: agentId,
          model: opts?.model,
          turn_window: opts?.turnWindow,
          locale: opts?.locale ?? locale,
        }),
      });
      if (!resp.ok) {
        const detail = await resp.text();
        return {
          added: 0,
          generated: 0,
          error: detail || `HTTP ${resp.status}`,
        };
      }
      const body = await resp.json();
      await refresh();
      return {
        added: body.added ?? 0,
        generated: body.generated ?? 0,
        error: body.error ?? null,
      };
    },
    [baseUrl, locale, project, refresh],
  );

  const setStatus = useCallback(
    async (id: string, status: "pending" | "accepted" | "dismissed") => {
      if (!project) return;
      await fetch(
        `${baseUrl}/api/ambient-suggestions/${encodeURIComponent(id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project, status }),
        },
      );
      await refresh();
    },
    [baseUrl, project, refresh],
  );

  const clear = useCallback(
    async (onlyStatus?: "pending" | "accepted" | "dismissed") => {
      if (!project) return;
      const params = new URLSearchParams({ project });
      if (onlyStatus) params.set("status", onlyStatus);
      await fetch(`${baseUrl}/api/ambient-suggestions?${params.toString()}`, {
        method: "DELETE",
      });
      await refresh();
    },
    [baseUrl, project, refresh],
  );

  useEffect(() => {
    if (auto) void refresh();
  }, [auto, refresh]);

  return { bucket, loading, error, refresh, generate, setStatus, clear };
}
