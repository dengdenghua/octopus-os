/**
 * `useActiveAgentId` — subscribes to the footer-picked agent.
 *
 * Single source of truth: ``localStorage["echo.active-agent"]`` +
 * a ``window.echo:active-agent`` CustomEvent that the footer
 * dropdown dispatches on change.
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
import { swallow } from "@/core/utils/log";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useEvent } from "../events";
import {
  DEFAULT_PRIMARY_AGENT_ID,
  isPrimaryPersonaAgentId,
} from "./persona-policy";

export const ACTIVE_AGENT_KEY = "echo.active-agent";
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
): string | null {
  const locked = routeLock(pathname);
  if (locked) return locked;

  // A fresh-task URL is an explicit persona choice. Resolve it synchronously
  // so the workspace shell does not paint the previously stored persona for a
  // frame before the realtime page's effects persist the new choice.
  if (/^\/workspace\/realtime\/new(?:\/|$)/.test(pathname)) {
    const requested = normalizeAgentId(
      new URLSearchParams(search).get("agent"),
    );
    if (requested && isPrimaryPersonaAgentId(requested)) return requested;
  }

  return storedAgentId;
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
    const raw = window.localStorage.getItem(ACTIVE_AGENT_KEY);
    const normalized = normalizeAgentId(raw);
    if (normalized && isPrimaryPersonaAgentId(normalized)) return normalized;
    if (normalized) {
      // Experts used to be persisted as standalone identities. They now join
      // a White Ghost-led conversation on demand, so migrate that old picker
      // state without affecting the owner stored on historical threads.
      window.localStorage.setItem(ACTIVE_AGENT_KEY, DEFAULT_PRIMARY_AGENT_ID);
      return DEFAULT_PRIMARY_AGENT_ID;
    }
    if (raw?.trim()) {
      // Stale legacy id (e.g. DID-xxx) — clean it so the UI doesn't
      // keep trying to route to a backend-unknown agent.
      window.localStorage.removeItem(ACTIVE_AGENT_KEY);
    }
  } catch (e) {
    swallow(e, "storage");
  }
  return null;
}

/** React hook · returns the currently-active agent id (or null).
 *
 *  Routes listed in ``ROUTE_LOCK`` override the stored preference —
 *  the Code workspace ALWAYS reports ``coder``, never whatever the
 *  footer was last set to — so thread lists / stream requests / other
 *  consumers can't drift off the owning persona.
 */
export function useActiveAgentId(): string | null {
  const { pathname, search } = useLocation();
  const [id, setId] = useState<string | null>(() => readActive());

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
      if (e.key === ACTIVE_AGENT_KEY) setId(readActive());
    }
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return activeAgentIdForLocation(pathname, search, id);
}
