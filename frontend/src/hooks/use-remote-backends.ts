/**
 * useRemoteBackends · CRUD + health + proxy for the remote
 * backends registry exposed at ``/api/remote-backends/*``.
 *
 * Mirrors the same minimalist style as ``useFeatureFlags`` /
 * ``useAmbientSuggestions``. No TanStack Query — the catalog is
 * tiny and the operator panel is the only consumer.
 */

import { swallow } from "@/core/utils/log";
import { useCallback, useEffect, useState } from "react";

export interface RemoteSshTunnel {
  host: string;
  user: string | null;
  port: number;
  identity_file: string | null;
  connect_timeout: number;
}

export interface RemoteBackend {
  id: string;
  name: string;
  url: string;
  ssh: RemoteSshTunnel | null;
  added_at: string;
  last_health: "ok" | "error" | null;
  last_health_at: string | null;
  health_detail: string | null;
}

export interface RemoteBackendsState {
  backends: RemoteBackend[];
  enabled: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  add: (input: {
    name: string;
    url: string;
    ssh?: RemoteSshTunnel | null;
  }) => Promise<{
    ok: boolean;
    error: string | null;
    backend: RemoteBackend | null;
  }>;
  remove: (id: string) => Promise<boolean>;
  ping: (id: string) => Promise<{ status: string; detail: string | null }>;
}

export interface UseRemoteBackendsOptions {
  baseUrl?: string;
  auto?: boolean;
}

export function useRemoteBackends(
  options: UseRemoteBackendsOptions = {},
): RemoteBackendsState {
  const { baseUrl = "", auto = true } = options;
  const [backends, setBackends] = useState<RemoteBackend[]>([]);
  const [enabled, setEnabled] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${baseUrl}/api/remote-backends`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const body = (await resp.json()) as {
        enabled: boolean;
        backends: RemoteBackend[];
      };
      setEnabled(Boolean(body.enabled));
      setBackends(Array.isArray(body.backends) ? body.backends : []);
    } catch (exc) {
      swallow(exc);
      setError(exc instanceof Error ? exc.message : String(exc));
      setBackends([]);
    } finally {
      setLoading(false);
    }
  }, [baseUrl]);

  const add = useCallback(
    async (input: {
      name: string;
      url: string;
      ssh?: RemoteSshTunnel | null;
    }) => {
      try {
        const resp = await fetch(`${baseUrl}/api/remote-backends`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: input.name,
            url: input.url,
            ssh: input.ssh ?? null,
          }),
        });
        if (!resp.ok) {
          const text = await resp.text();
          return {
            ok: false,
            error: text || `HTTP ${resp.status}`,
            backend: null,
          };
        }
        const body = await resp.json();
        await refresh();
        return { ok: true, error: null, backend: body.backend ?? null };
      } catch (exc) {
        swallow(exc);
        return {
          ok: false,
          error: exc instanceof Error ? exc.message : String(exc),
          backend: null,
        };
      }
    },
    [baseUrl, refresh],
  );

  const remove = useCallback(
    async (id: string) => {
      const resp = await fetch(
        `${baseUrl}/api/remote-backends/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      );
      const ok = resp.ok;
      await refresh();
      return ok;
    },
    [baseUrl, refresh],
  );

  const ping = useCallback(
    async (id: string) => {
      const resp = await fetch(
        `${baseUrl}/api/remote-backends/${encodeURIComponent(id)}/health`,
        { method: "POST" },
      );
      if (!resp.ok) {
        return { status: "error", detail: `HTTP ${resp.status}` };
      }
      const body = await resp.json();
      await refresh();
      return {
        status: String(body.status ?? "unknown"),
        detail: body.detail ?? null,
      };
    },
    [baseUrl, refresh],
  );

  useEffect(() => {
    if (auto) void refresh();
  }, [auto, refresh]);

  return {
    backends,
    enabled,
    loading,
    error,
    refresh,
    add,
    remove,
    ping,
  };
}
