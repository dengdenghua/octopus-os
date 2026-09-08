/**
 * Which modules the user keeps in their sidebar.
 *
 * Installation/runtime availability is account-wide and shared by every
 * presentation. Sidebar placement is account-scoped, with optional
 * persona-specific overrides layered over the shared defaults.
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
import { currentActorId } from "@/core/auth/api";

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
const AVAILABILITY_EVENT_KEY = "echo.modules.availability-sync.v1";

function scopedStorageKey(key: string, actor = currentActorId()): string {
  return `${key}:actor:${encodeURIComponent(actor || "anonymous")}`;
}

/** Read user-scoped state and migrate the legacy device-wide value once. */
function readScopedStorage(key: string): string | null {
  const actor = currentActorId();
  const scopedKey = scopedStorageKey(key, actor);
  const scoped = window.localStorage.getItem(scopedKey);
  if (scoped !== null) return scoped;
  const legacy = window.localStorage.getItem(key);
  if (legacy === null || actor === "anonymous") return legacy;
  try {
    window.localStorage.setItem(scopedKey, legacy);
    window.localStorage.removeItem(key);
  } catch {
    // Private mode / quota: use the legacy value for this render only.
  }
  return legacy;
}

const localStorageProvider: ModuleStateProvider = {
  readDisabled() {
    try {
      const raw = readScopedStorage(STORAGE_KEY);
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
      window.localStorage.setItem(
        scopedStorageKey(STORAGE_KEY),
        JSON.stringify(ids),
      );
    } catch {
      /* private mode / quota — this session only */
    }
  },
  readOverrides() {
    try {
      const raw = readScopedStorage(OVERRIDES_STORAGE_KEY);
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
        scopedStorageKey(OVERRIDES_STORAGE_KEY),
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
  cacheActor = null;
  overridesCache = null;
  overridesActor = null;
  snapshots.clear();
  notify();
}

let cache: Set<string> | null = null;
let cacheActor: string | null = null;
let overridesCache: PersonaModuleOverrides | null = null;
let overridesActor: string | null = null;
/**
 * Runtime availability is deliberately separate from the user's sidebar
 * preference. `undefined` means the backend has not answered yet; once a
 * module is known to be unavailable, no persona override can resurrect it.
 */
let availabilityCache: ReadonlyMap<string, boolean> | null = null;
let availabilityActor: string | null = null;

function scopedAvailability() {
  return availabilityActor === currentActorId() ? availabilityCache : null;
}
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
  const actor = currentActorId();
  if (cache && cacheActor === actor) return cache;
  cache = readDisabledSet();
  cacheActor = actor;
  return cache;
}

function getOverrides(): PersonaModuleOverrides {
  const actor = currentActorId();
  if (overridesCache && overridesActor === actor) return overridesCache;
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
  overridesActor = actor;
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
  const availability = scopedAvailability();
  if (respectAvailability && availability) {
    for (const [id, available] of availability) {
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
  availabilityActor = currentActorId();
  availabilityCache = availability
    ? new Map(
        Object.entries(availability).filter(
          ([id]) => moduleById(id) !== undefined,
        ),
      )
    : null;
  snapshots.clear();
  notify();
  broadcastAvailability();
}

/** Update one module after an install/enable/disable/uninstall mutation. */
export function setModuleAvailable(id: string, available: boolean): void {
  if (!moduleById(id)) return;
  const next = new Map(scopedAvailability() ?? []);
  next.set(id, available);
  availabilityCache = next;
  availabilityActor = currentActorId();
  snapshots.clear();
  notify();
  broadcastAvailability();
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
    overridesActor = currentActorId();
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

/** Set an account-wide module preference and discard stale persona overrides. */
export function setModuleEnabledGlobally(id: string, enabled: boolean): void {
  const descriptor = moduleById(id);
  if (!descriptor || !descriptor.removable) return;

  const nextDisabled = new Set(getDisabledSet());
  if (enabled) nextDisabled.delete(id);
  else nextDisabled.add(id);
  cache = nextDisabled;
  provider.writeDisabled([...nextDisabled]);

  const current = getOverrides();
  const nextOverrides: PersonaModuleOverrides = {};
  let overridesChanged = false;
  for (const [agent, values] of Object.entries(current)) {
    const nextValues = { ...values };
    if (id in nextValues) {
      delete nextValues[id];
      overridesChanged = true;
    }
    if (Object.keys(nextValues).length > 0) nextOverrides[agent] = nextValues;
  }
  if (overridesChanged) {
    overridesCache = nextOverrides;
    overridesActor = currentActorId();
    provider.writeOverrides?.(nextOverrides);
  }
  notify();
}

function handleStorage(event: StorageEvent): void {
  if (event.key === AVAILABILITY_EVENT_KEY && event.newValue) {
    try {
      const payload = JSON.parse(event.newValue) as {
        actor?: unknown;
        availability?: unknown;
      };
      if (payload.actor !== currentActorId() || !payload.availability) return;
      if (typeof payload.availability !== "object") return;
      const entries = Object.entries(
        payload.availability as Record<string, unknown>,
      )
        .filter(
          ([id, value]) =>
            moduleById(id) !== undefined && typeof value === "boolean",
        )
        .map(([id, value]) => [id, value] as [string, boolean]);
      availabilityActor = currentActorId();
      availabilityCache = new Map(entries);
      snapshots.clear();
      notify();
    } catch {
      // A broadcast is only an optimization; the next server reconciliation
      // remains authoritative when another renderer sends malformed data.
    }
    return;
  }
  const actor = currentActorId();
  if (
    event.key === STORAGE_KEY ||
    event.key === OVERRIDES_STORAGE_KEY ||
    event.key === scopedStorageKey(STORAGE_KEY, actor) ||
    event.key === scopedStorageKey(OVERRIDES_STORAGE_KEY, actor)
  ) {
    cache = null; // force re-read so other tabs stay consistent
    cacheActor = null;
    overridesCache = null;
    overridesActor = null;
    notify();
  }
}

function broadcastAvailability(): void {
  if (typeof window === "undefined" || !availabilityCache) return;
  try {
    // Use a write/remove pulse so the event reaches other same-origin
    // renderers without turning a boot-time snapshot into stale durable state.
    window.localStorage.setItem(
      AVAILABILITY_EVENT_KEY,
      JSON.stringify({
        actor: currentActorId(),
        availability: Object.fromEntries(availabilityCache),
        nonce: `${Date.now()}-${Math.random()}`,
      }),
    );
    window.localStorage.removeItem(AVAILABILITY_EVENT_KEY);
  } catch {
    /* private mode / unavailable storage — this renderer remains correct */
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
  const snapshotId = `${currentActorId()}:${respectAvailability ? "available" : "preference"}:${agentId ?? "__legacy__"}`;
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
  cacheActor = null;
  overridesCache = null;
  overridesActor = null;
  availabilityCache = null;
  snapshots.clear();
  notify();
}

/** Installation status is independent of sidebar preference. */
export function useModuleAvailability(id: string): boolean | undefined {
  return useSyncExternalStore(
    subscribe,
    () => scopedAvailability()?.get(id),
    () => undefined,
  );
}

export function useModuleAvailabilitySnapshot(): ReadonlyMap<
  string,
  boolean
> | null {
  return useSyncExternalStore(subscribe, scopedAvailability, () => null);
}
