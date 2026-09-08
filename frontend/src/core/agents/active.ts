/**
 * `useActiveAgentId` — subscribes to the footer-picked agent.
 *
 * Resolve the stored/route preference against the server's selectable roster.
 * Selection changes arrive through agent:changed and cross-tab storage events.
 *
 * Reused by:
 *   • `WorkspaceSidebar` — scope thread list to the active agent
 *   • realtime workspace page (`app/workspace/realtime/[id]/page.tsx`) — route the
 *     turn to the active agent, and invalidate thread cache on change
 *   • anywhere else that needs to know "who am I talking to"
 *
 * Without this hook, each component had its own localStorage read +
 * listener, easy to drift out of sync (and historically did: the
 * sidebar kept showing one agent's threads while chat page sent
 * another's).
 */
import { currentActorId } from "@/core/auth/api";
import { actorScopedStorageKey } from "@/core/auth/scoped-storage";
import { swallow } from "@/core/utils/log";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useEvent } from "../events";
import { useAgents } from "./index";
import {
  DEFAULT_PRIMARY_AGENT_ID,
  isPrimaryPersonaAgentId,
  WHITE_GHOST_AGENT_ORDER,
} from "./persona-policy";

export const ACTIVE_AGENT_KEY = "echo.active-agent";
const ACTIVE_AGENT_SCOPED_PREFIX = "echo.active-agent.v2:";

export function activeAgentStorageKey(actor = currentActorId()): string {
  return actorScopedStorageKey(ACTIVE_AGENT_SCOPED_PREFIX, actor);
}

export function setActiveAgentId(agentId: string): void {
  try {
    window.localStorage.setItem(activeAgentStorageKey(), agentId);
  } catch (e) {
    swallow(e, "storage");
  }
}
// Kept for backward compatibility with external listeners
export const ACTIVE_AGENT_EVENT = "echo:active-agent";

export const ROUTE_LOCKS: { prefix: string; agent: string }[] = [
  // Collaborative tasks do NOT lock to a specific agent — the leader is
  // chosen by the user from the agent roster (same as chat mode).
  // This means "coder" in a collaborative task is the SAME person as "coder"
  // in chat mode — they're just "pulled into the group".
];

export function activeAgentIdForLocation(
  pathname: string,
  search: string,
  storedAgentId: string | null,
  availableAgentIds?: readonly string[],
): string | null {
  const locked = routeLock(pathname);
  let preferred = locked ?? storedAgentId;

  // A fresh-task URL is an explicit persona choice. Resolve it synchronously
  // so the workspace shell does not paint the previously stored persona for a
  // frame before the realtime page's effects persist the new choice.
  if (/^\/workspace\/realtime\/new(?:\/|$)/.test(pathname)) {
    const requested = normalizeAgentId(
      new URLSearchParams(search).get("agent"),
    );
    if (!locked && requested && isPrimaryPersonaAgentId(requested)) {
      preferred = requested;
    }
  }

  if (availableAgentIds === undefined) return preferred;
  const available = new Set(availableAgentIds.filter(isPrimaryPersonaAgentId));
  if (preferred && available.has(preferred)) return preferred;
  // Internal roles can still own historical threads without appearing in the
  // selectable roster. A stale preference must not silently launch new tasks
  // as one of those roles while the footer displays a different persona.
  return WHITE_GHOST_AGENT_ORDER.find((name) => available.has(name)) ?? null;
}

function routeLock(pathname: string): string | null {
  const agentChatMatch = /^\/workspace\/agents\/([^/]+)\/chats(?:\/|$)/.exec(
    pathname,
  );
  if (agentChatMatch?.[1]) {
    try {
      return decodeURIComponent(agentChatMatch[1]);
    } catch (e) {
      swallow(e);
      return agentChatMatch[1];
    }
  }
  for (const r of ROUTE_LOCKS) {
    if (pathname.startsWith(r.prefix)) return r.agent;
  }
  return null;
}

function normalizeAgentId(value: string | null | undefined): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  // Reject only legacy placeholders / malformed values. The backend roster is
  // dynamic now, so hard-coding known ids makes newly-created agents disappear
  // when returning from the HUD.
  if (raw.startsWith("DID-")) return null;
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(raw)) return null;
  return raw;
}

function readActive(): string | null {
  try {
    const scopedKey = activeAgentStorageKey();
    let raw = window.localStorage.getItem(scopedKey);
    if (raw === null) {
      // Preserve the pre-account-scoping preference for the first actor that
      // opens the upgraded shell. Anonymous sessions keep the legacy value
      // readable until authentication provides an unambiguous owner.
      const legacy = window.localStorage.getItem(ACTIVE_AGENT_KEY);
      if (legacy !== null) {
        raw = legacy;
        const actor = currentActorId();
        if (actor.trim() && actor !== "anonymous") {
          window.localStorage.setItem(scopedKey, legacy);
          window.localStorage.removeItem(ACTIVE_AGENT_KEY);
        }
      }
    }
    const normalized = normalizeAgentId(raw);
    if (normalized && isPrimaryPersonaAgentId(normalized)) return normalized;
    if (normalized) {
      // Experts used to be persisted as standalone identities. They now join
      // a White Ghost-led conversation on demand, so migrate that old picker
      // state without affecting the owner stored on historical threads.
      window.localStorage.setItem(
        activeAgentStorageKey(),
        DEFAULT_PRIMARY_AGENT_ID,
      );
      return DEFAULT_PRIMARY_AGENT_ID;
    }
    if (raw?.trim()) {
      // Stale legacy id (e.g. DID-xxx) — clean it so the UI doesn't
      // keep trying to route to a backend-unknown agent.
      window.localStorage.removeItem(activeAgentStorageKey());
    }
  } catch (e) {
    swallow(e, "storage");
  }
  return null;
}

/** Read the persisted persona without applying route or roster resolution. */
export function readStoredActiveAgentId(): string | null {
  return readActive();
}

/** The selectable persona for new tasks. Historical thread ownership is
 * resolved separately from that thread's metadata, never from this fallback. */
export function useActiveAgentId(): string | null {
  const { pathname, search } = useLocation();
  const actor = currentActorId();
  const [id, setId] = useState<string | null>(() => readStoredActiveAgentId());
  const [sessionActor, setSessionActor] = useState(actor);
  const { agents, isLoading, error } = useAgents();

  useEffect(() => {
    if (sessionActor === actor) return;
    setSessionActor(actor);
    setId(readStoredActiveAgentId());
  }, [actor, sessionActor]);

  // Subscribe to EventBus agent changes
  useEvent("agent:changed", (payload) => {
    const next = normalizeAgentId(payload.name);
    if (next && isPrimaryPersonaAgentId(next)) {
      setId(next);
    } else if (payload.source !== "thread") {
      setId(DEFAULT_PRIMARY_AGENT_ID);
    }
  });

  // Handle tab-to-tab sync too — user opens Privacy in one tab,
  // switches agent in another → sidebar auto-updates.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === activeAgentStorageKey()) setId(readStoredActiveAgentId());
    }
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  if (sessionActor !== actor || isLoading || (error && agents.length === 0)) {
    return null;
  }
  return activeAgentIdForLocation(
    pathname,
    search,
    id,
    agents.map((agent) => agent.name),
  );
}
