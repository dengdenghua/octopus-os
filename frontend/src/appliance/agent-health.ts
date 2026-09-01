import { useEffect, useState } from "react";

import { getEchoBaseURL } from "@/core/config";

export type AgentDesktopHealthState =
  | "checking"
  | "ready"
  | "restart-required"
  | "unavailable";

export type AgentDesktopHealth = {
  state: AgentDesktopHealthState;
  version: string | null;
  sourceId: string | null;
  verifiedBundle: boolean;
};

const HEALTH_TIMEOUT_MS = 3_000;
const HEALTH_POLL_MS = 15_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export async function probeAgentDesktopHealth(
  fetcher: typeof fetch = fetch,
  apiBase = getEchoBaseURL(),
  timeoutMs = HEALTH_TIMEOUT_MS,
): Promise<AgentDesktopHealth> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetcher(`${apiBase.replace(/\/+$/, "")}/health`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return unavailableHealth();

    const payload: unknown = await response.json().catch(() => null);
    if (!isRecord(payload) || payload.status !== "ok") {
      return unavailableHealth();
    }

    const runtime = payload.runtime;
    const version =
      isRecord(runtime) &&
      typeof runtime.version === "string" &&
      runtime.version.length > 0 &&
      runtime.version.length <= 64 &&
      !/[\u0000-\u001f\u007f]/.test(runtime.version)
        ? runtime.version
        : null;
    const sourceId =
      isRecord(runtime) &&
      typeof runtime.sourceId === "string" &&
      /^[0-9a-f]{40}$/.test(runtime.sourceId)
        ? runtime.sourceId
        : null;
    if (isRecord(runtime) && runtime.verifiedBundle === true && !sourceId) {
      return unavailableHealth();
    }
    const identity = {
      version,
      sourceId,
      verifiedBundle:
        isRecord(runtime) &&
        runtime.verifiedBundle === true &&
        sourceId !== null,
    };

    const lifecycle = payload.lifecycle;
    if (isRecord(lifecycle)) {
      if (lifecycle.restartRequired === true) {
        return { state: "restart-required", ...identity };
      }

      const traceStore = lifecycle.traceStore;
      if (isRecord(traceStore) && traceStore.ready !== true) {
        return unavailableHealth();
      }
    }

    return { state: "ready", ...identity };
  } catch {
    return unavailableHealth();
  } finally {
    window.clearTimeout(timeout);
  }
}

function unavailableHealth(): AgentDesktopHealth {
  return {
    state: "unavailable",
    version: null,
    sourceId: null,
    verifiedBundle: false,
  };
}

export function useAgentDesktopHealth(enabled: boolean): AgentDesktopHealth {
  const [health, setHealth] = useState<AgentDesktopHealth>({
    state: "checking",
    version: null,
    sourceId: null,
    verifiedBundle: false,
  });

  useEffect(() => {
    if (!enabled) return;

    let active = true;
    let requestInFlight = false;
    const refresh = async () => {
      if (requestInFlight) return;
      requestInFlight = true;
      try {
        const next = await probeAgentDesktopHealth();
        if (active) setHealth(next);
      } finally {
        requestInFlight = false;
      }
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };

    void refresh();
    const timer = window.setInterval(() => void refresh(), HEALTH_POLL_MS);
    document.addEventListener("visibilitychange", refreshWhenVisible);

    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [enabled]);

  return health;
}
