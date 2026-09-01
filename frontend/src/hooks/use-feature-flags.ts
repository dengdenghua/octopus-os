/**
 * useFeatureFlags · read-only feature flag catalog hook.
 *
 * Mirrors the backend's ``GET /api/feature-flags`` + ``POST
 * /api/feature-flags/reload`` surface. Keeps the fetched snapshot
 * in component state; callers use ``isOn(name)`` to gate UI.
 *
 * Intentionally avoids TanStack Query: the catalog is cheap,
 * single-shot per session, and we don't want prop-drilling a query
 * client through every component that wants to hide an
 * experimental panel.
 *
 * Design notes
 * ------------
 * - One fetch on mount; refetch is manual via ``reload()``. The
 *   frontend settings panel can call ``reload()`` after toggling
 *   an env override; day-to-day components never need to.
 * - Unknown flag names resolve to ``false`` in ``isOn``, matching
 *   the backend's ``ff.value(name, fallback)``.
 * - Zero external deps — just ``fetch`` + React state, same as
 *   the rest of ``frontend/src/core/api`` where applicable.
 */

import { swallow } from "@/core/utils/log";
import { authHeaders } from "@/core/auth/api";
import { useCallback, useEffect, useRef, useState } from "react";

const catalogInFlight = new Map<
  string,
  Promise<{ flags: FeatureFlagEntry[] }>
>();

function fetchCatalogOnce(url: string): Promise<{ flags: FeatureFlagEntry[] }> {
  const existing = catalogInFlight.get(url);
  if (existing) return existing;
  const request = fetch(url, { method: "GET", headers: authHeaders() })
    .then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return (await response.json()) as { flags: FeatureFlagEntry[] };
    })
    .finally(() => catalogInFlight.delete(url));
  catalogInFlight.set(url, request);
  return request;
}

export interface FeatureFlagEntry {
  name: string;
  value: unknown;
  source: "env" | "file" | "default" | string;
  default: unknown;
  description: string;
  experimental: boolean;
  primary_env: string;
  legacy_env: string[];
}

export interface FeatureFlagsState {
  flags: FeatureFlagEntry[];
  loading: boolean;
  error: string | null;
  isOn: (name: string) => boolean;
  value: <T>(name: string, fallback: T) => T;
  reload: () => Promise<void>;
}

export interface UseFeatureFlagsOptions {
  /** Override the base URL. Defaults to same-origin. */
  baseUrl?: string;
  /**
   * If true, skip the automatic mount fetch — caller must invoke
   * ``reload()`` explicitly. Useful in tests and in storybook.
   */
  manual?: boolean;
}

export function useFeatureFlags(
  options: UseFeatureFlagsOptions = {},
): FeatureFlagsState {
  const { baseUrl = "", manual = false } = options;

  const [flags, setFlags] = useState<FeatureFlagEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(!manual);
  const [error, setError] = useState<string | null>(null);

  // Memoize the latest snapshot as a Map for O(1) lookup in isOn.
  // Ref + state — state drives re-render, ref gives callbacks a
  // stable reference without stale-closure headaches.
  const indexRef = useRef<Map<string, FeatureFlagEntry>>(new Map());
  indexRef.current = new Map(flags.map((f) => [f.name, f]));

  const fetchCatalog = useCallback(
    async (method: "GET" | "POST" = "GET"): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const url = `${baseUrl}/api/feature-flags${
          method === "POST" ? "/reload" : ""
        }`;
        const body =
          method === "GET"
            ? await fetchCatalogOnce(url)
            : await fetch(url, {
                method,
                headers: authHeaders(),
              }).then(async (response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return (await response.json()) as {
                  flags: FeatureFlagEntry[];
                };
              });
        setFlags(Array.isArray(body.flags) ? body.flags : []);
      } catch (exc) {
        swallow(exc);
        setError(exc instanceof Error ? exc.message : String(exc));
        setFlags([]);
      } finally {
        setLoading(false);
      }
    },
    [baseUrl],
  );

  useEffect(() => {
    if (manual) return;
    void fetchCatalog("GET");
  }, [fetchCatalog, manual]);

  const isOn = useCallback((name: string): boolean => {
    const entry = indexRef.current.get(name);
    return Boolean(entry?.value);
  }, []);

  const valueFn = useCallback(function value<T>(name: string, fallback: T): T {
    const entry = indexRef.current.get(name);
    if (entry === undefined) return fallback;
    return entry.value as T;
  }, []);

  const reload = useCallback(async () => {
    await fetchCatalog("POST");
  }, [fetchCatalog]);

  return {
    flags,
    loading,
    error,
    isOn,
    value: valueFn,
    reload,
  };
}
