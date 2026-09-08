import { currentActorId } from "./api";

/** Build one consistent local-storage namespace for account-owned state. */
export function actorScopedStorageKey(
  prefix: string,
  actor = currentActorId(),
): string {
  const normalized = actor.trim() || "anonymous";
  return `${prefix}:${encodeURIComponent(normalized)}`;
}

/** Read an actor-owned value, migrating the pre-scope device key once. */
export function readActorScopedStorageValue(
  prefix: string,
  actor = currentActorId(),
): string | null {
  if (typeof window === "undefined") return null;
  const normalizedActor = actor.trim() || "anonymous";
  const scopedKey = actorScopedStorageKey(prefix, normalizedActor);
  const scoped = window.localStorage.getItem(scopedKey);
  if (scoped !== null) return scoped;
  const legacy = window.localStorage.getItem(prefix);
  if (legacy === null || normalizedActor === "anonymous") return legacy;
  try {
    window.localStorage.setItem(scopedKey, legacy);
    window.localStorage.removeItem(prefix);
  } catch {
    // Private mode / quota: keep the legacy value usable for this render.
  }
  return legacy;
}
