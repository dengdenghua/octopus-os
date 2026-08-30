/**
 * Which modules the user keeps in their sidebar.
 *
 * Storage is behind a tiny provider seam (`ModuleStateProvider`) so a backend
 * per-user preference endpoint can replace localStorage without touching any
 * caller. There is no such endpoint today — `IdentityStore` is read-only
 * (loaded from YAML, no write path), so cross-device sync is a follow-up.
 *
 * Persisted shape is a *disabled* list, not an enabled one: that way modules
 * added to the catalog in a later release default to visible instead of
 * silently staying hidden for existing users.
 */
import { useSyncExternalStore } from "react";

import {
  defaultEnabledModuleIds,
  moduleById,
  pinnedModuleIds,
} from "./catalog";
import { defaultModuleIdsForAgent } from "@/core/workspace/workspace-presets";

export type PersonaModuleOverrides = Record<string, Record<string, boolean>>;

export interface ModuleStateProvider {
  readDisabled(): string[];
  writeDisabled(ids: string[]): void;
  readOverrides?(): PersonaModuleOverrides;
  writeOverrides?(overrides: PersonaModuleOverrides): void;
}

const STORAGE_KEY = "echo.modules.disabled";
const OVERRIDES_STORAGE_KEY = "echo.modules.persona-overrides.v1";

const localStorageProvider: ModuleStateProvider = {
  readDisabled() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed)
        ? parsed.filter((id): id is string => typeof id === "string")
        : [];
    } catch {
      return [];
    }
  },
  writeDisabled(ids) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
    } catch {
      /* private mode / quota — this session only */
    }
  },
  readOverrides() {
    try {
      const raw = window.localStorage.getItem(OVERRIDES_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object"
        ? (parsed as PersonaModuleOverrides)
        : {};
    } catch {
      return {};
    }
  },
  writeOverrides(overrides) {
    try {
      window.localStorage.setItem(
        OVERRIDES_STORAGE_KEY,
        JSON.stringify(overrides),
      );
    } catch {
      /* private mode / quota — this session only */
    }
  },
};

let provider: ModuleStateProvider = localStorageProvider;

/** Swap the persistence backend (tests, or a future server-backed provider). */
export function setModuleStateProvider(next: ModuleStateProvider): void {
  provider = next;
  cache = null;
  overridesCache = null;
  snapshots.clear();
  notify();
}

let cache: Set<string> | null = null;
let overridesCache: PersonaModuleOverrides | null = null;
/**
 * Runtime availability is deliberately separate from the user's sidebar
 * preference. `undefined` means the backend has not answered yet; once a
 * module is known to be unavailable, no persona override can resurrect it.
 */
let availabilityCache: ReadonlyMap<string, boolean> | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

/** Pinned modules can never be disabled, whatever storage claims. */
function readDisabledSet(): Set<string> {
  const pinned = new Set(pinnedModuleIds());
  const stored = provider
    .readDisabled()
    // Drop ids that no longer exist so a removed module can't haunt storage.
    .filter((id) => moduleById(id) !== undefined && !pinned.has(id));
  return new Set(stored);
}

function getDisabledSet(): Set<string> {
  if (cache) return cache;
  cache = readDisabledSet();
  return cache;
}

function getOverrides(): PersonaModuleOverrides {
  if (overridesCache) return overridesCache;
  const raw = provider.readOverrides?.() ?? {};
  const cleaned: PersonaModuleOverrides = {};
  for (const [agentId, values] of Object.entries(raw)) {
    if (!values || typeof values !== "object") continue;
    const next: Record<string, boolean> = {};
    for (const [moduleId, enabled] of Object.entries(values)) {
      if (moduleById(moduleId) && typeof enabled === "boolean") {
        next[moduleId] = enabled;
      }
    }
    if (Object.keys(next).length > 0) cleaned[agentId] = next;
  }
  overridesCache = cleaned;
  return overridesCache;
}

export function isModuleEnabled(id: string, agentId?: string | null): boolean {
  return enabledModuleIds(agentId).includes(id);
}

function computeModuleIds(
  agentId?: string | null,
  respectAvailability = true,
): string[] {
  const allIds = defaultEnabledModuleIds();
  const defaults = agentId ? defaultModuleIdsForAgent(allIds, agentId) : allIds;
  const enabled = new Set(defaults);
  const disabled = getDisabledSet();
  for (const id of disabled) enabled.delete(id);

  if (agentId) {
    const personaOverrides = getOverrides()[agentId] ?? {};
    for (const [id, isEnabled] of Object.entries(personaOverrides)) {
      if (isEnabled) enabled.add(id);
      else enabled.delete(id);
    }
  }

  for (const id of pinnedModuleIds()) enabled.add(id);
  if (respectAvailability && availabilityCache) {
    for (const [id, available] of availabilityCache) {
      if (!available) enabled.delete(id);
    }
  }
  return allIds.filter((id) => enabled.has(id));
}

export function enabledModuleIds(agentId?: string | null): string[] {
  return computeModuleIds(agentId, true);
}

/** User preference only; unavailable remote apps keep their deep-link error page. */
export function userEnabledModuleIds(agentId?: string | null): string[] {
  return computeModuleIds(agentId, false);
}

/** Replace the server-backed availability snapshot for installable modules. */
export function setModuleAvailabilitySnapshot(
  availability: Readonly<Record<string, boolean>> | null,
): void {
  availabilityCache = availability
    ? new Map(
        Object.entries(availability).filter(
          ([id]) => moduleById(id) !== undefined,
        ),
      )
    : null;
  snapshots.clear();
  notify();
}

/** Update one module after an install/enable/disable/uninstall mutation. */
export function setModuleAvailable(id: string, available: boolean): void {
  if (!moduleById(id)) return;
  const next = new Map(availabilityCache ?? []);
  next.set(id, available);
  availabilityCache = next;
  snapshots.clear();
  notify();
}

/** Enable/disable one module. Pinned modules are silently ignored. */
export function setModuleEnabled(
  id: string,
  enabled: boolean,
  agentId?: string | null,
): void {
  const descriptor = moduleById(id);
  if (!descriptor || !descriptor.removable) return;

  if (agentId) {
    const current = getOverrides();
    const next: PersonaModuleOverrides = {
      ...current,
      [agentId]: { ...(current[agentId] ?? {}), [id]: enabled },
    };
    overridesCache = next;
    provider.writeOverrides?.(next);
    notify();
    return;
  }

  const next = new Set(getDisabledSet());
  if (enabled) next.delete(id);
  else next.add(id);

  cache = next;
  provider.writeDisabled([...next]);
  notify();
}

function handleStorage(event: StorageEvent): void {
  if (event.key === STORAGE_KEY || event.key === OVERRIDES_STORAGE_KEY) {
    cache = null; // force re-read so other tabs stay consistent
    overridesCache = null;
    notify();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", handleStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", handleStorage);
  };
}

// Snapshot must be referentially stable — useSyncExternalStore re-renders on
// every changed reference, and a fresh array each call would loop forever.
const snapshots = new Map<string, { key: string; ids: string[] }>();

function getSnapshot(
  agentId?: string | null,
  respectAvailability = true,
): string[] {
  const snapshotId = `${respectAvailability ? "available" : "preference"}:${agentId ?? "__legacy__"}`;
  const ids = computeModuleIds(agentId, respectAvailability);
  const key = ids.join("|");
  const current = snapshots.get(snapshotId);
  if (!current || current.key !== key) {
    snapshots.set(snapshotId, { key, ids });
    return ids;
  }
  return current.ids;
}

/** Subscribe to the enabled-module id list. */
export function useEnabledModuleIds(agentId?: string | null): string[] {
  return useSyncExternalStore(
    subscribe,
    () => getSnapshot(agentId),
    () =>
      agentId
        ? defaultModuleIdsForAgent(defaultEnabledModuleIds(), agentId)
        : defaultEnabledModuleIds(),
  );
}

/** Subscribe to user preference without conflating it with install state. */
export function useUserEnabledModuleIds(agentId?: string | null): string[] {
  return useSyncExternalStore(
    subscribe,
    () => getSnapshot(agentId, false),
    () =>
      agentId
        ? defaultModuleIdsForAgent(defaultEnabledModuleIds(), agentId)
        : defaultEnabledModuleIds(),
  );
}

/** Test seam: drop the memoized state. */
export function resetModuleStateCache(): void {
  cache = null;
  overridesCache = null;
  availabilityCache = null;
  snapshots.clear();
  notify();
}
